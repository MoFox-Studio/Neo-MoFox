"""ProbabilityBypassHandler 单元测试。

覆盖概率直通处理器的所有路径：

- 命中放行概率 → 返回 ``STOP`` 并写回 ``proceed=True`` + reason
- 未命中 → 返回 ``SUCCESS`` 且不修改 ``proceed``
- 禁用 / 无未读 / 配置缺失 / chat_stream 缺失 → 早退 ``SUCCESS``
- 强提及（@bot_id / ``:<bot_id>>``）/ 弱提及（昵称 / 别名）加成
- 未读数加成、概率封顶 ``1.0``
- ``personality`` 配置缺失时回退到 ``chat_stream.bot_nickname``
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.neo_chatter.components.config import NeoChatterConfig
from plugins.neo_chatter.components.event_handlers.probability_bypass import (
    ProbabilityBypassHandler,
)
from plugins.neo_chatter.plugin import NeoChatterPlugin
from src.app.plugin_system.types import ChatStream, Message
from src.kernel.event import EventDecision

#: 处理器在事件参数里读取的决策字段默认值，与 ``utils.preprocess.run_preprocess`` 预填一致。
_DEFAULT_DECISION_FIELDS: dict[str, Any] = {
    "proceed": True,
    "reason": "",
    "mutations": "",
    "force_stop_minutes": None,
}


def _make_config(
    *,
    enabled: bool = True,
    base: float = 0.1,
    name_bonus: float = 0.7,
    alias_bonus: float = 0.4,
    unread_bonus: float = 0.05,
) -> NeoChatterConfig:
    """构造一份指定概率参数的 :class:`NeoChatterConfig`。"""

    cfg = NeoChatterConfig()
    section = cfg.plugin.preprocess_probability_bypass
    section.enabled = enabled
    section.base_bypass_probability = base
    section.name_mention_bonus = name_bonus
    section.alias_mention_bonus = alias_bonus
    section.unread_message_bonus = unread_bonus
    return cfg


def _make_stream(
    *,
    bot_id: str = "bot1",
    bot_nickname: str = "小狐狸",
    chat_type: str = "group",
) -> ChatStream:
    """构造测试用 :class:`ChatStream`。"""

    return ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type=chat_type,
        bot_id=bot_id,
        bot_nickname=bot_nickname,
        stream_name="test",
    )


def _make_msg(
    text: str,
    *,
    sender: str = "Alice",
    at_user_id: str | None = None,
) -> Message:
    """构造一条未读消息，可选附加 ``at_users`` 强提及。

    注意：``Message`` 的 ``**extra`` 会捕获所有未命名 kwargs 作为 ``msg.extra``，
    因此 ``at_users`` 必须作为关键字参数直接传，不能包在 ``extra=...`` 里，
    否则会被嵌套成 ``{"extra": {"at_users": ...}}`` 导致 ``msg.extra.get("at_users")`` 取不到。
    """

    extra_kwargs: dict[str, Any] = {}
    if at_user_id is not None:
        extra_kwargs["at_users"] = [{"user_id": at_user_id}]
    return Message(
        message_id="m1",
        content=text,
        processed_plain_text=text,
        sender_name=sender,
        chat_type="group",
        **extra_kwargs,
    )


def _make_params(
    unreads: list[Message],
    chat_stream: ChatStream,
    cfg: NeoChatterConfig,
    *,
    proceed: bool = True,
) -> dict[str, Any]:
    """构造与 ``run_preprocess`` 预填字段一致的事件参数。"""

    params: dict[str, Any] = {
        "stream_id": chat_stream.stream_id,
        "chat_type": str(chat_stream.chat_type),
        "chat_stream": chat_stream,
        "unreads": list(unreads),
        "history_text": "",
        "config": cfg,
    }
    params.update(_DEFAULT_DECISION_FIELDS)
    params["proceed"] = proceed
    return params


def _patch_personality(
    monkeypatch: pytest.MonkeyPatch,
    *,
    nickname: str = "小狐狸",
    alias_names: list[str] | None = None,
) -> None:
    """把 ``get_core_config`` 替换为返回指定 personality 的桩。"""

    monkeypatch.setattr(
        "plugins.neo_chatter.components.event_handlers.probability_bypass.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(
                nickname=nickname,
                alias_names=alias_names if alias_names is not None else [],
            )
        ),
    )


def _patch_random(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    """把处理器模块内的 ``random.random`` 替换为返回固定值的桩。"""

    monkeypatch.setattr(
        "plugins.neo_chatter.components.event_handlers.probability_bypass.random.random",
        lambda: value,
    )


def _make_plugin(cfg: NeoChatterConfig) -> NeoChatterPlugin:
    """构造一个仅注入 config 的 :class:`NeoChatterPlugin` 实例。

    ``BasePlugin.__init__`` 只设置 ``self.config``，不会触发 ``on_plugin_loaded``
    等生命周期钩子，因此可安全用于单元测试。
    """

    return NeoChatterPlugin(config=cfg)


# ---------------------------------------------------------------------------
# 早退路径
# ---------------------------------------------------------------------------


async def test_handler_disabled_returns_success_without_modification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """处理器禁用时应直接放行，不修改任何决策字段。"""

    cfg = _make_config(enabled=False)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is True
    assert out["reason"] == ""


async def test_handler_no_unreads_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """无未读消息时应早退放行。"""

    cfg = _make_config()
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


async def test_handler_missing_chat_stream_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chat_stream`` 缺失或类型错误时应早退放行。"""

    cfg = _make_config()
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    params = _make_params([_make_msg("hi")], _make_stream(), cfg)
    params["chat_stream"] = "not a chat stream"

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


async def test_handler_config_not_neo_chatter_config_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``params['config']`` 不是 :class:`NeoChatterConfig` 时应早退放行。"""

    handler = ProbabilityBypassHandler(_make_plugin(_make_config()))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, _make_config())
    params["config"] = SimpleNamespace()  # 类型不匹配

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


# ---------------------------------------------------------------------------
# 命中 / 未命中
# ---------------------------------------------------------------------------


async def test_handler_hit_returns_stop_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """基础概率=1.0 + 强提及必然命中，返回 ``STOP`` 并写回 reason。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.99)  # 任何随机数都 < 1.0 不成立，但概率封顶到 1.0
    cfg = _make_config(base=1.0)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("hi", at_user_id="bot1")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert out["proceed"] is True
    assert "概率直通命中" in out["reason"]
    assert "强提及" in out["reason"]
    assert "封顶 1.00" in out["reason"]


async def test_handler_miss_returns_success_without_modification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """概率为 0 + 无提及时必然未命中，返回 ``SUCCESS`` 且不修改决策字段。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.0)  # 0 < 0 不成立
    cfg = _make_config(base=0.0, unread_bonus=0.0)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("hello world")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is True
    assert out["reason"] == ""


async def test_handler_random_below_probability_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """随机值 < 概率时命中。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.05)  # < base 0.1
    cfg = _make_config(base=0.1, unread_bonus=0.0)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("hello")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "概率直通命中" in out["reason"]


async def test_handler_random_equal_or_above_probability_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """随机值 >= 概率时未命中（``random.random() < probability`` 为假）。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.1)  # == base 0.1，不命中（严格小于）
    cfg = _make_config(base=0.1, unread_bonus=0.0)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("hello")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


# ---------------------------------------------------------------------------
# 强提及 / 弱提及加成
# ---------------------------------------------------------------------------


async def test_strong_mention_via_at_users_adds_bonus(monkeypatch: pytest.MonkeyPatch) -> None:
    """``at_users`` 命中 ``bot_id`` 时应叠加强提及加成。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.5)  # base 0.1 + name_bonus 0.7 = 0.8，0.5 < 0.8 命中
    cfg = _make_config(base=0.1, name_bonus=0.7, alias_bonus=0.4)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("hi", at_user_id="bot1")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "强提及" in out["reason"]


async def test_strong_mention_via_text_at_marker_adds_bonus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文本中出现 ``:<bot_id>>`` 形式的 @ 标记也应算强提及。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.5)
    cfg = _make_config(base=0.1, name_bonus=0.7)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("hi :bot1>")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "强提及" in out["reason"]


async def test_weak_mention_via_nickname_adds_bonus(monkeypatch: pytest.MonkeyPatch) -> None:
    """文本命中昵称应叠加弱提及加成（无强提及时）。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.3)  # base 0.1 + alias_bonus 0.4 = 0.5，0.3 < 0.5 命中
    cfg = _make_config(base=0.1, name_bonus=0.7, alias_bonus=0.4)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("小狐狸你好")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "弱提及" in out["reason"]
    assert "强提及" not in out["reason"]


async def test_weak_mention_via_alias_adds_bonus(monkeypatch: pytest.MonkeyPatch) -> None:
    """文本命中别名应叠加弱提及加成。"""

    _patch_personality(monkeypatch, nickname="小狐狸", alias_names=["阿狸"])
    _patch_random(monkeypatch, 0.3)
    cfg = _make_config(base=0.1, alias_bonus=0.4)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    params = _make_params([_make_msg("阿狸出来玩")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "弱提及" in out["reason"]


async def test_strong_mention_takes_precedence_over_weak(monkeypatch: pytest.MonkeyPatch) -> None:
    """强提及与弱提及同时存在时只叠加强提及加成。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.5)
    cfg = _make_config(base=0.1, name_bonus=0.7, alias_bonus=0.4)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    # 同时 @bot1 和文本含昵称
    params = _make_params(
        [_make_msg("小狐狸 :bot1>", at_user_id="bot1")], stream, cfg
    )

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "强提及" in out["reason"]
    assert "弱提及" not in out["reason"]


# ---------------------------------------------------------------------------
# 未读数加成 / 概率封顶
# ---------------------------------------------------------------------------


async def test_unread_count_bonus_accumulates(monkeypatch: pytest.MonkeyPatch) -> None:
    """未读数加成应按 ``len(unreads) * unread_bonus`` 累加。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.3)  # base 0.1 + 5*0.05 = 0.35，0.3 < 0.35 命中
    cfg = _make_config(base=0.1, unread_bonus=0.05)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    unreads = [_make_msg(f"msg{i}") for i in range(5)]
    params = _make_params(unreads, stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "5 条未读" in out["reason"]


async def test_probability_capped_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """概率超过 1.0 时应封顶并标注「封顶 1.00」。"""

    _patch_personality(monkeypatch, nickname="小狐狸")
    _patch_random(monkeypatch, 0.99)  # 1.0 必然命中
    cfg = _make_config(base=0.5, name_bonus=0.7, unread_bonus=0.1)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1")
    # base 0.5 + name 0.7 + 5*0.1 = 1.7 → 封顶 1.0
    params = _make_params(
        [_make_msg("hi", at_user_id="bot1")] * 5, stream, cfg
    )

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "封顶 1.00" in out["reason"]


# ---------------------------------------------------------------------------
# personality 缺失回退
# ---------------------------------------------------------------------------


async def test_personality_missing_falls_back_to_bot_nickname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_core_config`` 抛 RuntimeError 时应回退到 ``chat_stream.bot_nickname``。"""

    def _raise() -> Any:
        raise RuntimeError("core config not initialized")

    monkeypatch.setattr(
        "plugins.neo_chatter.components.event_handlers.probability_bypass.get_core_config",
        _raise,
    )
    _patch_random(monkeypatch, 0.3)  # base 0.1 + alias 0.4 = 0.5
    cfg = _make_config(base=0.1, alias_bonus=0.4)
    handler = ProbabilityBypassHandler(_make_plugin(cfg))
    stream = _make_stream(bot_id="bot1", bot_nickname="小狐狸")
    params = _make_params([_make_msg("小狐狸你好")], stream, cfg)

    decision, out = await handler.execute("neo_chatter:preprocess", params)

    assert decision == EventDecision.STOP
    assert "弱提及" in out["reason"]


# ---------------------------------------------------------------------------
# 组件元数据
# ---------------------------------------------------------------------------


def test_handler_metadata() -> None:
    """处理器元数据（name / weight / 订阅事件）应符合预期。"""

    assert ProbabilityBypassHandler.name == "probability_bypass"
    assert ProbabilityBypassHandler.weight == 100
    assert ProbabilityBypassHandler.component_type == "event_handler"
    assert "neo_chatter:preprocess" in ProbabilityBypassHandler.init_subscribe
