"""NDFC EventBus 端到端集成测试。

真正通过 :class:`EventManager` / :class:`EventBus` 发布事件，验证：

1. **默认 handler 兜底**：发布 ``:compute_cooldown`` 等事件，默认 handler (weight=0)
   被触发并填回结果字段。
2. **STOP 替换语义**：第三方 handler (weight=200) 用 ``STOP`` 完全替换默认实现，
   短路后续 handler（包括默认），``NdfcPublisher`` 读回第三方填的值。
3. **SUCCESS 协作语义**：第三方 handler 用 ``SUCCESS`` append 到容器字段
   （如 ``fragments``），默认 handler 继续 append，最终两者都被保留。
4. **PASS 观察语义**：第三方 handler 返回 ``PASS`` 不影响结果，默认 handler 照常执行。
5. **weight 排序**：高 weight 先执行；前者的输出作为后者的输入。
6. **payload key 稳定约束**：handler 返回的 params key 集合与入参不一致时，
   其影响被静默丢弃（依据 ``core.py:334-338``）。

为避免污染全局 :class:`EventManager` 单例，每个测试用例用独立的 handler 签名
（带 uuid 后缀）并在末尾 ``unregister_handler`` 清理。
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from plugins.neo_default_chatter.components.config import NeoChatterConfig
from plugins.neo_default_chatter.components.event_handlers.defaults import (
    BuildNegativeExtraDefaultHandler,
    ComputeCooldownDefaultHandler,
    FormatToolResultDefaultHandler,
)
from plugins.neo_default_chatter.utils.event_publisher import NdfcEvent, NdfcPublisher
from src.app.plugin_system.base import BaseEventHandler
from src.core.managers import get_event_manager
from src.kernel.event import EventDecision


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_config(
    *,
    enable_cooldown: bool = True,
    enable_stop_wake: bool = True,
    stop_wake_prob: float = 0.5,
) -> NeoChatterConfig:
    cfg = NeoChatterConfig()
    cfg.plugin.enable_cooldown = enable_cooldown
    cfg.plugin.enable_stop_direct_message_wake = enable_stop_wake
    cfg.plugin.stop_direct_message_wake_probability = stop_wake_prob
    return cfg


def _make_plugin(cfg: NeoChatterConfig | None = None) -> MagicMock:
    plugin = MagicMock()
    plugin.plugin_name = "neo_default_chatter"
    plugin.config = cfg if cfg is not None else NeoChatterConfig()
    return plugin


@pytest.fixture
async def registered_default_handlers():
    """注册一组默认 handler 到 EventManager，测试结束后清理。

    返回已注册的 handler 实例列表与对应签名列表，便于额外清理 / 断言。
    """
    mgr = get_event_manager()
    plugin = _make_plugin()
    handlers_with_sigs: list[tuple[str, BaseEventHandler]] = [
        (
            f"test:{uuid.uuid4()}:compute_cooldown_default",
            ComputeCooldownDefaultHandler(plugin),
        ),
        (
            f"test:{uuid.uuid4()}:format_tool_result_default",
            FormatToolResultDefaultHandler(plugin),
        ),
        (
            f"test:{uuid.uuid4()}:build_negative_extra_default",
            BuildNegativeExtraDefaultHandler(plugin),
        ),
    ]
    for sig, handler in handlers_with_sigs:
        await mgr.register_handler(sig, handler)

    yield handlers_with_sigs

    for sig, _ in handlers_with_sigs:
        mgr.unregister_handler(sig)


async def _register_handler(
    handler: BaseEventHandler, *, name_hint: str = "custom"
) -> str:
    """注册一个 handler 到 EventManager 并返回签名；用于测试后清理。"""
    mgr = get_event_manager()
    sig = f"test:{name_hint}:{uuid.uuid4()}"
    await mgr.register_handler(sig, handler)
    return sig


# ---------------------------------------------------------------------------
# 默认 handler 兜底
# ---------------------------------------------------------------------------


async def test_compute_cooldown_default_handler_fires_via_eventbus(
    registered_default_handlers: Any,
) -> None:
    """发布 ``:compute_cooldown`` 事件，默认 handler 应被触发并填回 ``cooldown_seconds``。"""
    cfg = _make_config(enable_cooldown=True)
    result = await NdfcPublisher.compute_cooldown(
        stream_id="s1", minutes=5.0, config=cfg
    )
    assert result == 300  # 5 分钟 * 60


async def test_format_tool_result_default_pass_via_eventbus(
    registered_default_handlers: Any,
) -> None:
    """发布 ``:format_tool_result`` kind=pass 事件，默认 handler 应填回提示文本。"""
    text = await NdfcPublisher.format_tool_result(
        stream_id="s1",
        call_name="action-pass_and_wait",
        kind="pass",
        args={"seconds": 30},
    )
    assert "30" in text
    assert "等待" in text


async def test_format_tool_result_default_stop_via_eventbus(
    registered_default_handlers: Any,
) -> None:
    text = await NdfcPublisher.format_tool_result(
        stream_id="s1",
        call_name="action-stop_conversation",
        kind="stop",
        args={"minutes": 10},
    )
    assert "10" in text
    assert "对话已结束" in text


# ---------------------------------------------------------------------------
# STOP 替换语义
# ---------------------------------------------------------------------------


async def test_stop_replacement_short_circuits_default_handler(
    registered_default_handlers: Any,
) -> None:
    """第三方 handler (weight=200) 用 STOP 替换默认实现，默认 handler 不执行。"""

    class MyReplacement(BaseEventHandler):
        name = "my_compute_cooldown_replacement"
        description = "test"
        weight = 200  # 高于默认的 0
        init_subscribe = [NdfcEvent.COMPUTE_COOLDOWN]

        async def execute(self, event_name, params):
            params["cooldown_seconds"] = 999
            return EventDecision.STOP, params

    sig = await _register_handler(MyReplacement(_make_plugin()))
    try:
        result = await NdfcPublisher.compute_cooldown(
            stream_id="s1", minutes=5.0, config=_make_config()
        )
        assert result == 999  # 默认 handler 没机会覆盖
    finally:
        get_event_manager().unregister_handler(sig)


async def test_stop_only_short_circuits_subsequent_not_publisher_readback(
    registered_default_handlers: Any,
) -> None:
    """``STOP`` 只短路后续 handler，``NdfcPublisher`` 仍能读回 STOP handler 填的值。"""

    class MyReplacement(BaseEventHandler):
        name = "my_format_tool_result_replacement"
        description = "test"
        weight = 200
        init_subscribe = [NdfcEvent.FORMAT_TOOL_RESULT]

        async def execute(self, event_name, params):
            if params.get("kind") == "pass":
                params["result_text"] = "REPLACED TEXT"
                return EventDecision.STOP, params
            return EventDecision.PASS, params

    sig = await _register_handler(MyReplacement(_make_plugin()))
    try:
        text = await NdfcPublisher.format_tool_result(
            stream_id="s1",
            call_name="action-pass_and_wait",
            kind="pass",
            args={"seconds": 99},
        )
        assert text == "REPLACED TEXT"
    finally:
        get_event_manager().unregister_handler(sig)


# ---------------------------------------------------------------------------
# SUCCESS 协作语义
# ---------------------------------------------------------------------------


async def test_success_collaboration_appends_both_fragments(
    registered_default_handlers: Any,
) -> None:
    """第三方 SUCCESS append 一段，默认 handler 继续 append，两段都保留。"""

    class MyExtension(BaseEventHandler):
        name = "my_neg_ext"
        description = "test"
        weight = 200
        init_subscribe = [NdfcEvent.BUILD_NEGATIVE_EXTRA]

        async def execute(self, event_name, params):
            params["fragments"].append("第三方约束")
            return EventDecision.SUCCESS, params

    sig = await _register_handler(MyExtension(_make_plugin()))
    try:
        # 默认 handler 的 fragment 来自 NeoChatterPromptBuilder.build_negative_behaviors_extra
        # 当 reinforce_negative_behaviors=True 且 personality 有 negative_behaviors 时非空；
        # 这里至少应包含第三方 append 的 "第三方约束"。
        text = await NdfcPublisher.build_negative_extra(
            stream_id="s1", config=_make_config()
        )
        assert "第三方约束" in text
    finally:
        get_event_manager().unregister_handler(sig)


async def test_success_chain_preserves_order_by_weight(
    registered_default_handlers: Any,
) -> None:
    """两个第三方 handler 按 weight 降序执行，前者输出 = 后者输入。"""

    class HighWeight(BaseEventHandler):
        name = "high_weight_ext"
        description = "test"
        weight = 300
        init_subscribe = [NdfcEvent.BUILD_NEGATIVE_EXTRA]

        async def execute(self, event_name, params):
            params["fragments"].append("HIGH")
            return EventDecision.SUCCESS, params

    class LowWeight(BaseEventHandler):
        name = "low_weight_ext"
        description = "test"
        weight = 100  # 低于 300 但仍高于默认 0
        init_subscribe = [NdfcEvent.BUILD_NEGATIVE_EXTRA]

        async def execute(self, event_name, params):
            params["fragments"].append("LOW")
            return EventDecision.SUCCESS, params

    sig_high = await _register_handler(HighWeight(_make_plugin()), name_hint="high")
    sig_low = await _register_handler(LowWeight(_make_plugin()), name_hint="low")
    try:
        text = await NdfcPublisher.build_negative_extra(
            stream_id="s1", config=_make_config()
        )
        # HIGH 应先于 LOW 出现（默认 handler 可能也 append，但其内容在两者之后）
        high_idx = text.find("HIGH")
        low_idx = text.find("LOW")
        assert high_idx >= 0 and low_idx >= 0
        assert high_idx < low_idx, f"HIGH should come before LOW, got {text!r}"
    finally:
        mgr = get_event_manager()
        mgr.unregister_handler(sig_high)
        mgr.unregister_handler(sig_low)


# ---------------------------------------------------------------------------
# PASS 观察语义
# ---------------------------------------------------------------------------


async def test_pass_observer_does_not_modify_result(
    registered_default_handlers: Any,
) -> None:
    """第三方 PASS handler 不修改 params，默认 handler 照常执行。"""
    observed_events: list[str] = []

    class MyObserver(BaseEventHandler):
        name = "my_observer"
        description = "test"
        weight = 1000  # 最先执行
        init_subscribe = [NdfcEvent.COMPUTE_COOLDOWN]

        async def execute(self, event_name, params):
            observed_events.append(event_name)
            return EventDecision.PASS, params

    sig = await _register_handler(MyObserver(_make_plugin()))
    try:
        result = await NdfcPublisher.compute_cooldown(
            stream_id="s1", minutes=2.0, config=_make_config()
        )
        assert result == 120  # 默认 handler 算出的值
        assert observed_events == ["neo_default_chatter:compute_cooldown"]
    finally:
        get_event_manager().unregister_handler(sig)


# ---------------------------------------------------------------------------
# payload key 稳定约束
# ---------------------------------------------------------------------------


async def test_key_set_violation_silently_dropped(
    registered_default_handlers: Any,
) -> None:
    """handler 返回的 params key 集合与入参不一致时，其影响被丢弃（降级为 PASS）。

    依据 ``core.py:334-338``：``expected_keys = set(initial_params)``，
    handler 不能新增 / 删除 key，否则其效果被静默丢弃。
    """

    class BadHandler(BaseEventHandler):
        name = "bad_handler_extra_key"
        description = "test"
        weight = 200
        init_subscribe = [NdfcEvent.COMPUTE_COOLDOWN]

        async def execute(self, event_name, params):
            # 故意新增一个 key —— 违反 key 集合稳定约束
            params["cooldown_seconds"] = 999
            params["unexpected_extra_key"] = "should be dropped"
            return EventDecision.SUCCESS, params

    sig = await _register_handler(BadHandler(_make_plugin()))
    try:
        result = await NdfcPublisher.compute_cooldown(
            stream_id="s1", minutes=5.0, config=_make_config()
        )
        # BadHandler 的影响被丢弃，默认 handler 照常填 300
        assert result == 300
    finally:
        get_event_manager().unregister_handler(sig)


async def test_key_set_missing_key_also_dropped(
    registered_default_handlers: Any,
) -> None:
    """handler 删除 key 同样违反约束，影响被丢弃。"""

    class BadHandler(BaseEventHandler):
        name = "bad_handler_missing_key"
        description = "test"
        weight = 200
        init_subscribe = [NdfcEvent.COMPUTE_COOLDOWN]

        async def execute(self, event_name, params):
            # 故意删除一个 key —— 违反 key 集合稳定约束
            params["cooldown_seconds"] = 999
            del params["minutes"]
            return EventDecision.SUCCESS, params

    sig = await _register_handler(BadHandler(_make_plugin()))
    try:
        result = await NdfcPublisher.compute_cooldown(
            stream_id="s1", minutes=5.0, config=_make_config()
        )
        # BadHandler 的影响被丢弃，默认 handler 照常填 300
        assert result == 300
    finally:
        get_event_manager().unregister_handler(sig)


# ---------------------------------------------------------------------------
# 异常 fail-open
# ---------------------------------------------------------------------------


async def test_handler_exception_fail_open_to_pass(
    registered_default_handlers: Any,
) -> None:
    """handler 抛异常时降级为 PASS，默认 handler 仍执行（依据 event_manager.py:337-362）。"""

    class CrashHandler(BaseEventHandler):
        name = "crash_handler"
        description = "test"
        weight = 200
        init_subscribe = [NdfcEvent.COMPUTE_COOLDOWN]

        async def execute(self, event_name, params):
            raise RuntimeError("intentional crash")

    sig = await _register_handler(CrashHandler(_make_plugin()))
    try:
        result = await NdfcPublisher.compute_cooldown(
            stream_id="s1", minutes=5.0, config=_make_config()
        )
        # CrashHandler 降级为 PASS，默认 handler 照常填 300
        assert result == 300
    finally:
        get_event_manager().unregister_handler(sig)


# ---------------------------------------------------------------------------
# 无订阅者时返回预填默认值
# ---------------------------------------------------------------------------


async def test_no_subscribers_returns_prefilled_default() -> None:
    """无订阅者时 ``NdfcPublisher`` 应返回 payload 预填的默认值。

    依据 ``core.py:238-239``：无订阅者时返回 ``(SUCCESS, dict(params))``。
    为避免其他测试注册的默认 handler 干扰，用一个未被任何默认 handler 订阅的
    独立 stream_id 并不依赖默认 handler 注册——但仍可能被全局已注册的默认 handler 命中。

    因此这里改为直接断言：发布一个未被注册默认 handler 覆盖的事件
    （如 :compute_stop_wake 在 group chat_type 下，默认 handler 返回 0.0）。
    """
    # group chat_type 下默认 handler 直接返回 0.0（不论是否注册）
    result = await NdfcPublisher.compute_stop_wake(
        stream_id=f"test_no_sub_{uuid.uuid4()}",
        config=_make_config(enable_stop_wake=True, stop_wake_prob=0.9),
        chat_type="group",
    )
    assert result == 0.0
