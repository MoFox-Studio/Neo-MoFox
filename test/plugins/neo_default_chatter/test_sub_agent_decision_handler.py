"""SubAgentDecisionHandler 单元测试。

覆盖 sub_agent 轻量 LLM 判定处理器的所有路径：

- 禁用 / 无未读 / 上游已放行 / 配置缺失 → 早退 ``SUCCESS``（不修改 ``proceed``）
- LLM 判定值得回复 → 写回 ``proceed=True`` + reason
- LLM 判定不值得回复 → 写回 ``proceed=False`` + reason
- LLM 调用异常 → 按放行处理（``SUCCESS``，置 ``proceed=True``）
- 模型任务未配置 → 按放行处理（``SUCCESS``，置 ``proceed=True``）
- ``_parse_decision``：JSON 解析 / markdown 包裹 / 关键词回退 / 解析失败默认放行
- ``NeoChatterPromptBuilder.build_sub_agent_user_prompt``：历史截断、未读截断、占位符填充
- ``NeoChatterPromptBuilder._format_sub_agent_message_line``：文本 / 非文本内容回退
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.neo_default_chatter.components.config import NeoChatterConfig
from plugins.neo_default_chatter.components.event_handlers.defaults.preprocess import (
    SubAgentDecisionHandler,
)
from plugins.neo_default_chatter.plugin import NeoChatterPlugin
from plugins.neo_default_chatter.utils.prompt_builder import NeoChatterPromptBuilder
from plugins.neo_default_chatter.utils.prompts import (
    sub_agent_system_prompt,
    sub_agent_user_prompt,
)
from src.app.plugin_system.types import ChatStream, Message
from src.core.prompt import get_prompt_manager, optional
from src.kernel.event import EventDecision

_DEFAULT_DECISION_FIELDS: dict[str, Any] = {
    "proceed": False,
    "reason": "",
    "mutations": "",
    "force_stop_minutes": None,
}


@pytest.fixture(autouse=True)
def _register_sub_agent_templates() -> None:
    """注册 sub_agent 提示词模板，供 prompt_builder 渲染。"""

    get_prompt_manager().get_or_create(
        name="neo_default_chatter_sub_agent_system_prompt",
        template=sub_agent_system_prompt,
        policies={},
    )
    get_prompt_manager().get_or_create(
        name="neo_default_chatter_sub_agent_user_prompt",
        template=sub_agent_user_prompt,
        policies={
            "stream_name": optional("未知对话"),
            "chat_type": optional("未知类型"),
            "bot_nickname": optional("机器人"),
            "history": optional("（无）"),
            "unreads": optional("（无）"),
        },
    )


def _make_config(
    *,
    enabled: bool = True,
    task_name: str = "actor",
    request_name: str = "test:sub_agent",
    max_context_messages: int = 8,
    max_unread_messages: int = 10,
    decision_temperature: float = 0.2,
) -> NeoChatterConfig:
    """构造指定参数的 :class:`NeoChatterConfig`。"""

    cfg = NeoChatterConfig()
    section = cfg.plugin.preprocess_sub_agent
    section.enabled = enabled
    section.task_name = task_name
    section.request_name = request_name
    section.max_context_messages = max_context_messages
    section.max_unread_messages = max_unread_messages
    section.decision_temperature = decision_temperature
    return cfg


def _make_stream(
    *,
    bot_nickname: str = "小狐狸",
    stream_name: str = "test-stream",
    chat_type: str = "group",
) -> ChatStream:
    """构造测试用 :class:`ChatStream`。"""

    return ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type=chat_type,
        bot_id="bot1",
        bot_nickname=bot_nickname,
        stream_name=stream_name,
    )


def _make_msg(text: str, *, sender: str = "Alice") -> Message:
    """构造一条未读消息。"""

    return Message(
        message_id="m1",
        content=text,
        processed_plain_text=text,
        sender_name=sender,
        chat_type="group",
    )


def _make_params(
    unreads: list[Message],
    chat_stream: ChatStream,
    cfg: NeoChatterConfig,
    *,
    proceed: bool = False,
    history_text: str = "",
) -> dict[str, Any]:
    """构造与 ``run_preprocess`` 预填字段一致的事件参数。"""

    params: dict[str, Any] = {
        "stream_id": chat_stream.stream_id,
        "chat_type": str(chat_stream.chat_type),
        "chat_stream": chat_stream,
        "unreads": list(unreads),
        "history_text": history_text,
        "config": cfg,
    }
    params.update(_DEFAULT_DECISION_FIELDS)
    params["proceed"] = proceed
    return params


def _make_plugin(cfg: NeoChatterConfig) -> NeoChatterPlugin:
    """构造一个仅注入 config 的 :class:`NeoChatterPlugin` 实例。"""

    return NeoChatterPlugin(config=cfg)


def _make_response(message: str = "") -> Any:
    """构造一个最小化的 LLM 响应桩，仅含 ``message`` 字段。"""

    return SimpleNamespace(message=message)


# ---------------------------------------------------------------------------
# 早退路径
# ---------------------------------------------------------------------------


async def test_handler_disabled_returns_success_without_modification() -> None:
    """处理器禁用时应早退，不修改任何决策字段（proceed 保持默认 False）。"""

    cfg = _make_config(enabled=False)
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, cfg)

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is False
    assert out["reason"] == ""


async def test_handler_no_unreads_returns_success() -> None:
    """无未读消息时应早退放行。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([], stream, cfg)

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


async def test_handler_upstream_approved_skips_llm() -> None:
    """上游处理器已 ``proceed=True``（放行）时应跳过 LLM 判定，保持放行。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, cfg, proceed=True)

    # 即便 _call_llm 被调用也会因模型未初始化走 None 分支，验证它根本没被调用
    call_count = 0

    original_call = handler._call_llm

    async def _spy_call(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return await original_call(*args, **kwargs)

    handler._call_llm = _spy_call  # type: ignore[method-assign]
    try:
        decision, out = await handler.execute("neo_default_chatter:preprocess", params)
    finally:
        handler._call_llm = original_call  # type: ignore[method-assign]

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is True
    assert call_count == 0


async def test_handler_config_not_neo_default_chatter_config_returns_success() -> None:
    """``params['config']`` 不是 :class:`NeoChatterConfig` 时应早退放行。"""

    handler = SubAgentDecisionHandler(_make_plugin(_make_config()))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, _make_config())
    params["config"] = SimpleNamespace()

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


async def test_handler_missing_chat_stream_returns_success() -> None:
    """``chat_stream`` 缺失或类型错误时应早退放行。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    params = _make_params([_make_msg("hi")], _make_stream(), cfg)
    params["chat_stream"] = "not a chat stream"

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["reason"] == ""


# ---------------------------------------------------------------------------
# LLM 判定结果回写
# ---------------------------------------------------------------------------


async def test_llm_respond_true_sets_proceed_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 判定值得回复时 ``proceed=True`` 并写回 reason。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("你好")], stream, cfg)

    async def _fake_call(
        cs: ChatStream, unreads: list[Message], p: dict[str, Any], c: Any
    ) -> Any:
        return _make_response('{"respond": true, "reason": "用户在提问"}')

    monkeypatch.setattr(handler, "_call_llm", _fake_call)

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is True
    assert "值得回复" in out["reason"]
    assert "用户在提问" in out["reason"]


async def test_llm_respond_false_sets_proceed_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 判定不值得回复时 ``proceed=False`` 并写回 reason。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("刷屏中")], stream, cfg)

    async def _fake_call(
        cs: ChatStream, unreads: list[Message], p: dict[str, Any], c: Any
    ) -> Any:
        return _make_response('{"respond": false, "reason": "纯刷屏无需回复"}')

    monkeypatch.setattr(handler, "_call_llm", _fake_call)

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is False
    assert "不值得回复" in out["reason"]
    assert "纯刷屏无需回复" in out["reason"]


# ---------------------------------------------------------------------------
# LLM 异常 / 模型未配置
# ---------------------------------------------------------------------------


async def test_llm_exception_falls_back_to_proceed_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 调用抛异常时应按放行处理，不修改 ``proceed``。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, cfg)

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("LLM 服务不可用")

    monkeypatch.setattr(handler, "_call_llm", _boom)

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is True
    assert out["reason"] == ""


async def test_llm_returns_none_falls_back_to_proceed_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_call_llm`` 返回 ``None``（模型未配置）时应按放行处理。"""

    cfg = _make_config()
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, cfg)

    async def _none(*args: Any, **kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(handler, "_call_llm", _none)

    decision, out = await handler.execute("neo_default_chatter:preprocess", params)

    assert decision == EventDecision.SUCCESS
    assert out["proceed"] is True
    assert out["reason"] == ""


async def test_call_llm_model_task_not_configured_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_model_set_by_task`` 抛 ``RuntimeError`` 时 ``_call_llm`` 返回 ``None``。"""

    cfg = _make_config(task_name="no_such_task")
    handler = SubAgentDecisionHandler(_make_plugin(cfg))
    stream = _make_stream()
    params = _make_params([_make_msg("hi")], stream, cfg)

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Model config not initialized")

    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.event_handlers.defaults.preprocess.sub_agent_decision.llm_api.get_model_set_by_task",
        _raise,
    )

    result = await handler._call_llm(stream, [_make_msg("hi")], params, cfg.plugin.preprocess_sub_agent)

    assert result is None


# ---------------------------------------------------------------------------
# _parse_decision 解析逻辑
# ---------------------------------------------------------------------------


def test_parse_decision_valid_json() -> None:
    """标准 JSON 响应应正确解析。"""

    response = _make_response('{"respond": true, "reason": "用户在提问"}')
    respond, reason = SubAgentDecisionHandler._parse_decision(response)

    assert respond is True
    assert reason == "用户在提问"


def test_parse_decision_json_with_markdown_fence() -> None:
    """模型用 markdown 代码块包裹 JSON 时也应解析。"""

    response = _make_response('```json\n{"respond": false, "reason": "刷屏"}\n```')
    respond, reason = SubAgentDecisionHandler._parse_decision(response)

    assert respond is False
    assert reason == "刷屏"


def test_parse_decision_json_without_reason_defaults_reason() -> None:
    """JSON 缺少 ``reason`` 时应回退到默认理由。"""

    response = _make_response('{"respond": true}')
    respond, reason = SubAgentDecisionHandler._parse_decision(response)

    assert respond is True
    assert reason == "未提供理由"


def test_parse_decision_keyword_respond_falls_back() -> None:
    """非 JSON 但含「回复 / 值得」关键词时应回退为 ``respond=True``。"""

    response = _make_response("这值得回复")
    respond, reason = SubAgentDecisionHandler._parse_decision(response)

    assert respond is True


def test_parse_decision_keyword_skip_falls_back() -> None:
    """非 JSON 但含「不回复 / 不值得」关键词时应回退为 ``respond=False``。"""

    response = _make_response("不值得回复这条消息")
    respond, reason = SubAgentDecisionHandler._parse_decision(response)

    assert respond is False


def test_parse_decision_parse_failure_defaults_to_true() -> None:
    """无法解析且无关键词时应默认放行（``respond=True``）。"""

    response = _make_response("xxxx yyyy zzzz")
    respond, reason = SubAgentDecisionHandler._parse_decision(response)

    assert respond is True
    assert "解析失败" in reason


def test_parse_decision_empty_message_defaults_to_true() -> None:
    """空响应应默认放行。"""

    response = _make_response("")
    respond, _ = SubAgentDecisionHandler._parse_decision(response)

    assert respond is True


# ---------------------------------------------------------------------------
# build_sub_agent_system_prompt 构造逻辑
# ---------------------------------------------------------------------------


async def test_build_system_prompt_renders_json_literal() -> None:
    """系统提示词中的 JSON 示例 ``{"respond": ...}`` 应被原样渲染，不报 KeyError。"""

    prompt = await NeoChatterPromptBuilder.build_sub_agent_system_prompt()

    assert prompt, "system prompt 不应为空"
    assert '{"respond": true|false, "reason": "简短理由，不超过 50 字"}' in prompt
    assert "{{" not in prompt
    assert "}}" not in prompt


async def test_build_system_prompt_contains_judgement_rules() -> None:
    """系统提示词应包含判定原则关键字。"""

    prompt = await NeoChatterPromptBuilder.build_sub_agent_system_prompt()

    assert "消息判定子代理" in prompt
    assert "值得回复" in prompt
    assert "不值得回复" in prompt


# ---------------------------------------------------------------------------
# build_sub_agent_user_prompt 构造逻辑
# ---------------------------------------------------------------------------


async def test_build_user_prompt_fills_placeholders() -> None:
    """用户提示词应正确填充流名、类型、昵称、历史、未读。"""

    cfg = _make_config()
    stream = _make_stream(bot_nickname="小狐狸", stream_name="测试群")
    unreads = [_make_msg("你好", sender="Alice")]
    history_text = "[Bob] 早上好"

    prompt = await NeoChatterPromptBuilder.build_sub_agent_user_prompt(
        stream, history_text, unreads, cfg.plugin.preprocess_sub_agent
    )

    assert "测试群" in prompt
    assert "group" in prompt
    assert "小狐狸" in prompt
    assert "[Bob] 早上好" in prompt
    assert "[Alice] 你好" in prompt


async def test_build_user_prompt_truncates_history() -> None:
    """历史消息超过 ``max_context_messages`` 时应截断到最近 N 行。"""

    cfg = _make_config(max_context_messages=2)
    stream = _make_stream()
    history_text = "[A] 1\n[A] 2\n[A] 3\n[A] 4"

    prompt = await NeoChatterPromptBuilder.build_sub_agent_user_prompt(
        stream, history_text, [_make_msg("hi")], cfg.plugin.preprocess_sub_agent
    )

    # 只保留最近 2 行
    assert "[A] 3" in prompt
    assert "[A] 4" in prompt
    assert "[A] 1" not in prompt
    assert "[A] 2" not in prompt


async def test_build_user_prompt_truncates_unreads() -> None:
    """未读消息超过 ``max_unread_messages`` 时应截断到最近 N 条。"""

    cfg = _make_config(max_unread_messages=2)
    stream = _make_stream()
    unreads = [_make_msg(f"msg{i}") for i in range(5)]

    prompt = await NeoChatterPromptBuilder.build_sub_agent_user_prompt(
        stream, "", unreads, cfg.plugin.preprocess_sub_agent
    )

    assert "msg4" in prompt
    assert "msg3" in prompt
    assert "msg0" not in prompt
    assert "msg1" not in prompt


async def test_build_user_prompt_empty_history_shows_placeholder() -> None:
    """历史为空时应显示「（无）」占位。"""

    cfg = _make_config()
    stream = _make_stream()

    prompt = await NeoChatterPromptBuilder.build_sub_agent_user_prompt(
        stream, "", [_make_msg("hi")], cfg.plugin.preprocess_sub_agent
    )

    assert "（无）" in prompt


# ---------------------------------------------------------------------------
# _format_sub_agent_message_line
# ---------------------------------------------------------------------------


def test_format_message_line_uses_processed_plain_text() -> None:
    """优先用 ``processed_plain_text``。"""

    msg = Message(
        message_id="m",
        content="原始",
        processed_plain_text="处理后",
        sender_name="Alice",
    )
    line = NeoChatterPromptBuilder._format_sub_agent_message_line(msg)

    assert "[Alice] 处理后" == line


def test_format_message_line_falls_back_to_content() -> None:
    """``processed_plain_text`` 为空时回退到 ``content``。"""

    msg = Message(
        message_id="m",
        content="原始内容",
        processed_plain_text=None,
        sender_name="Bob",
    )
    line = NeoChatterPromptBuilder._format_sub_agent_message_line(msg)

    assert "[Bob] 原始内容" == line


def test_format_message_line_non_text_content_stringified() -> None:
    """``content`` 为非字符串且无 ``processed_plain_text`` 时回退到 ``str(content)``。"""

    msg = Message(
        message_id="m",
        content={"type": "image"},
        processed_plain_text=None,
        sender_name="Carol",
    )
    line = NeoChatterPromptBuilder._format_sub_agent_message_line(msg)

    assert line.startswith("[Carol] ")
    assert "image" in line  # str({"type": "image"}) 包含 "image"


def test_format_message_line_empty_text_shows_placeholder() -> None:
    """``processed_plain_text`` 与 ``content`` 都为空时应显示「（非文本内容）」。"""

    msg = Message(
        message_id="m",
        content="",
        processed_plain_text="   ",
        sender_name="Dave",
    )
    line = NeoChatterPromptBuilder._format_sub_agent_message_line(msg)

    assert "[Dave] （非文本内容）" == line


def test_format_message_line_anonymous_sender() -> None:
    """``sender_name`` 和 ``sender_id`` 都为空时显示「匿名」。"""

    msg = Message(
        message_id="m",
        content="hello",
        processed_plain_text="hello",
        sender_name="",
        sender_id="",
    )
    line = NeoChatterPromptBuilder._format_sub_agent_message_line(msg)

    assert "[匿名] hello" == line


# ---------------------------------------------------------------------------
# 组件元数据
# ---------------------------------------------------------------------------


def test_handler_metadata() -> None:
    """处理器元数据（name / weight / 订阅事件）应符合预期。"""

    assert SubAgentDecisionHandler.name == "sub_agent_decision"
    assert SubAgentDecisionHandler.weight == 0
    assert SubAgentDecisionHandler.component_type == "event_handler"
    assert "neo_default_chatter:preprocess" in SubAgentDecisionHandler.init_subscribe
