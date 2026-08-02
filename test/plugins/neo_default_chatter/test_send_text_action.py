"""``neo_default_chatter`` 的 ``SendTextAction`` 行为测试。

重点覆盖本次修复：``execute`` 改为**异步生成器**，在真正发送前 ``yield None``
暂停到 ``"_READY"`` 状态，配合统一调度器（``run_llm_usable_executions``）的
顺序门控把同一轮多个 ``send_text`` 串行化，使打字延迟（基于上次发送时间差值）
能逐条生效，而不是并发同时发出。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.neo_default_chatter.components.actions.send_text import (
    SendTextAction,
    _LAST_SEND_TIME_ATTR,
)
from plugins.neo_default_chatter.components.config import NeoChatterConfig
from src.core.models.stream import ChatStream


def _make_action(
    *, stream: ChatStream | None = None, config: NeoChatterConfig | None = None
) -> SendTextAction:
    """构造一个 config 已注入 mock 插件实例的 SendTextAction。"""
    plugin = MagicMock()
    plugin.config = config if config is not None else NeoChatterConfig()
    return SendTextAction(
        chat_stream=stream or ChatStream(stream_id="s_group", platform="qq", chat_type="group"),
        plugin=plugin,
    )


@pytest.mark.asyncio
async def test_execute_yields_none_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute 应先 yield None 进入 READY，再执行打字延迟与发送。"""
    action = _make_action()
    sleep_mock = AsyncMock()
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(action, "_sleep_for_typing_delay", sleep_mock)
    monkeypatch.setattr(action, "_send_to_stream", send_mock)

    execution = action.execute(content="你好")

    # 第一次推进：进入 READY 门控，尚未 sleep / 发送
    first = await anext(execution)
    assert first is None
    sleep_mock.assert_not_awaited()
    send_mock.assert_not_awaited()

    # 第二次推进：执行延迟并发送，产出最终结果
    second = await anext(execution)
    assert second == (True, "已发送消息:你好")
    sleep_mock.assert_awaited_once_with("你好")
    send_mock.assert_awaited_once_with("你好")


@pytest.mark.asyncio
async def test_execute_via_wrap_execute_returns_final_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通过 ``_wrap_execute().wait_done()`` 走框架协议应拿到最终 (success, text)。"""
    action = _make_action()
    monkeypatch.setattr(action, "_sleep_for_typing_delay", AsyncMock())
    monkeypatch.setattr(action, "_send_to_stream", AsyncMock(return_value=True))

    execution = action._wrap_execute(content="你好")
    success, detail = await execution.wait_done()

    assert success is True
    assert detail == "已发送消息:你好"


@pytest.mark.asyncio
async def test_execute_empty_content_skips_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空内容直接产出结果，不发送。"""
    action = _make_action()
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(action, "_send_to_stream", send_mock)

    execution = action.execute(content="")
    first = await anext(execution)
    assert first == (True, "内容为空，跳过发送")
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_typing_delay_uses_length_and_max_cap() -> None:
    """打字延迟应随字符长度增长，并受最大等待时间限制。"""
    config = NeoChatterConfig()
    config.plugin.typing_delay_per_char = 0.5
    config.plugin.typing_delay_max_seconds = 10.0
    action = _make_action(config=config)

    assert action._typing_delay_seconds("你好呀") == pytest.approx(1.5)
    assert action._typing_delay_seconds("x" * 10_000) == 10.0


@pytest.mark.asyncio
async def test_sleep_for_typing_delay_first_message_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首条消息（无上次发送时间）不等待。"""
    action = _make_action()
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.actions.send_text.asyncio.sleep",
        sleep_mock,
    )

    await action._sleep_for_typing_delay("你好")

    sleep_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sleep_for_typing_delay_waits_only_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续发送只等待尚未经过的模拟打字时间。"""
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    action = _make_action(stream=stream)
    setattr(stream.context, _LAST_SEND_TIME_ATTR, 100.0)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.actions.send_text.time.monotonic",
        lambda: 100.5,
    )
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.actions.send_text.asyncio.sleep",
        sleep_mock,
    )

    await action._sleep_for_typing_delay("你好呀")

    # 3 字符 * 0.5 = 1.5s 目标，已过 0.5s，剩余 1.0s
    sleep_mock.assert_awaited_once_with(pytest.approx(1.0))


@pytest.mark.asyncio
async def test_sleep_for_typing_delay_skips_when_interval_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """距上次发送间隔足够时不等待。"""
    stream = ChatStream(stream_id="s_group", platform="qq", chat_type="group")
    action = _make_action(stream=stream)
    setattr(stream.context, _LAST_SEND_TIME_ATTR, 100.0)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.actions.send_text.time.monotonic",
        lambda: 102.0,
    )
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.actions.send_text.asyncio.sleep",
        sleep_mock,
    )

    await action._sleep_for_typing_delay("你好呀")

    sleep_mock.assert_not_awaited()
