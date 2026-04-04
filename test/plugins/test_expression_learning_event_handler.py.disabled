"""expression_learning event handler 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from plugins.expression_learning.event_handler import (
    ExpressionMatchListener,
    ExpressionPromptSuggestionListener,
)
from plugins.expression_learning.service import get_expression_learning_service
from src.core.components.base.plugin import BasePlugin
from src.core.models.message import Message
from src.core.models.stream import StreamContext
from src.core.prompt.template import PROMPT_BUILD_EVENT
from src.kernel.event import EventDecision, get_event_bus


class _Plugin(BasePlugin):
    """最小插件桩。"""

    plugin_name = "expression_learning"

    def get_components(self) -> list[type]:
        return []


class _DummyResponse:
    """最小 LLM 响应桩。"""

    def __init__(self, message: str) -> None:
        self.message = message

    def __await__(self):
        async def _done() -> None:
            return None

        return _done().__await__()


@pytest.fixture(autouse=True)
def _reset_prompt_event_bus() -> None:
    """清理 prompt build 订阅。"""

    bus = get_event_bus()
    for handler in bus.get_subscribers(PROMPT_BUILD_EVENT):
        bus.unsubscribe(PROMPT_BUILD_EVENT, handler)


@pytest.mark.asyncio
async def test_match_listener_clears_buffer_when_learning_returns_nothing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """模型认为无内容可学时，不落库但应清空缓存。"""

    from plugins.expression_learning.config import ExpressionLearningConfig
    import plugins.expression_learning.event_handler as handler_module

    plugin = _Plugin()
    config = ExpressionLearningConfig()
    config.storage.db_path = str(tmp_path / "expression_learning_handler.db")
    config.collaborator.enabled = True
    config.collaborator.message_buffer_size = 2
    plugin.config = config

    service = get_expression_learning_service(plugin)
    await service.initialize()

    fake_response = _DummyResponse('{"items": []}')

    fake_request = AsyncMock()
    fake_request.add_payload = Mock()
    fake_request.send = AsyncMock(return_value=fake_response)

    monkeypatch.setattr(handler_module, "create_llm_request", lambda *args, **kwargs: fake_request)
    monkeypatch.setattr(handler_module, "get_model_set_by_task", lambda name: [])

    listener = ExpressionMatchListener(plugin)
    message_a = Message(processed_plain_text="第一句", stream_id="stream-x", sender_name="A")
    message_b = Message(processed_plain_text="第二句", stream_id="stream-x", sender_name="B")

    decision_a, _ = await listener.execute("on_message_received", {"message": message_a})
    decision_b, _ = await listener.execute("on_message_received", {"message": message_b})

    assert decision_a is EventDecision.SUCCESS
    assert decision_b is EventDecision.SUCCESS

    learning_streams = getattr(plugin, "_expression_learning_learning_streams", set())
    for _ in range(20):
        if not learning_streams:
            break
        import asyncio

        await asyncio.sleep(0)

    buffers = getattr(plugin, "_expression_learning_message_buffers", {})
    assert buffers["stream-x"] == []


@pytest.mark.asyncio
async def test_prompt_suggestion_listener_injects_once_for_target_stream(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """应只为目标 stream 注入一次建议。"""

    from plugins.expression_learning.config import ExpressionLearningConfig
    import plugins.expression_learning.event_handler as handler_module

    plugin = _Plugin()
    config = ExpressionLearningConfig()
    config.storage.db_path = str(tmp_path / "expression_learning_prompt_handler.db")
    config.collaborator.enabled = True
    plugin.config = config

    service = get_expression_learning_service(plugin)
    await service.initialize()
    await service.create_record(
        scene_types=["附和"],
        regex_patterns=[r"确实"],
        description="用于附和对方",
        source_context="A: 这波确实可以",
    )

    monkeypatch.setattr(handler_module, "_extract_scene_types", AsyncMock(return_value=["附和"]))

    listener = ExpressionPromptSuggestionListener(plugin)
    context = StreamContext(stream_id="stream-y")
    context.current_message = Message(processed_plain_text="这波确实可以", stream_id="stream-y", sender_name="A")

    decision, _ = await listener.execute(
        "on_chatter_step",
        {"stream_id": "stream-y", "context": context, "tick": None, "chatter_gene": None, "continue": True},
    )

    assert decision is EventDecision.SUCCESS

    prompt_params = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "stream-y"},
        "policies": {},
        "strict": False,
    }
    first_decision, first_out = await get_event_bus().publish(PROMPT_BUILD_EVENT, prompt_params)
    assert first_decision is EventDecision.SUCCESS
    assert "可参考的表达方式" in first_out["values"]["extra"]
    assert "用于附和对方" in first_out["values"]["extra"]

    second_params = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "stream-y"},
        "policies": {},
        "strict": False,
    }
    _, second_out = await get_event_bus().publish(PROMPT_BUILD_EVENT, second_params)
    assert second_out["values"]["extra"] == ""
