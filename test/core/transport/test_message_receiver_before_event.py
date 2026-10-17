"""``BEFORE_MESSAGE_RECEIVED`` 预处理事件行为测试。

覆盖 MessageReceiver 在路由前发布事件、支持 STOP 拦截与就地修改 envelope 的行为。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.components.types import EventType
from src.core.models.message import Message
from src.core.transport.message_receive.receiver import MessageReceiver
from src.kernel.event import EventDecision


def _make_envelope(message_id: str = "msg-001", text: str = "hello") -> dict[str, Any]:
    """构造一封可被 MessageReceiver 处理的最小 incoming 信封。"""

    return {
        "direction": "incoming",
        "message_info": {
            "message_id": message_id,
            "platform": "qq",
            "user_info": {"user_id": "u1", "user_nickname": "Alice"},
            "group_info": {"group_id": "g1", "group_name": "TestGroup"},
        },
        "message_segment": [{"type": "text", "data": {"text": text}}],
    }


def _make_receiver_with_event_result(
    decision: EventDecision,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[MessageReceiver, MagicMock]:
    """构造一个 MessageReceiver，并把 event_manager.publish_event 替身成统一回执。"""

    message = Message(
        message_id="msg-001",
        content="hello",
        processed_plain_text="hello",
        sender_id="u1",
        sender_name="Alice",
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )
    converter = MagicMock()
    converter.envelope_to_message = AsyncMock(return_value=message)

    receiver = MessageReceiver(converter=converter)
    receiver._update_person_info = AsyncMock()  # type: ignore[method-assign]

    event_manager = MagicMock()
    event_manager.publish_event = AsyncMock(
        return_value={"decision": decision, "params": params or {}}
    )
    receiver._event_manager = event_manager
    return receiver, event_manager


@pytest.mark.asyncio
async def test_stop_decision_drops_envelope_before_conversion() -> None:
    """订阅者返回 STOP 时，消息不再被转换或发布 ON_MESSAGE_RECEIVED。"""

    receiver, event_manager = _make_receiver_with_event_result(EventDecision.STOP)

    await receiver.receive_envelope(_make_envelope(), "plugin:adapter:qq")

    # 只有 BEFORE_MESSAGE_RECEIVED 一次发布，没有进入转换或下游事件。
    assert event_manager.publish_event.await_count == 1
    assert event_manager.publish_event.call_args.args[0] == EventType.BEFORE_MESSAGE_RECEIVED
    receiver._converter.envelope_to_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_pass_decision_falls_through_with_original_envelope() -> None:
    """订阅者返回 PASS 时保留原 envelope 继续后续流程。"""

    receiver, event_manager = _make_receiver_with_event_result(EventDecision.PASS)

    await receiver.receive_envelope(_make_envelope(), "plugin:adapter:qq")

    # PASS 不会阻断链路：BEFORE 与 ON_MESSAGE_RECEIVED 都应触发。
    assert event_manager.publish_event.await_count == 2
    published_events = [call.args[0] for call in event_manager.publish_event.call_args_list]
    assert published_events == [
        EventType.BEFORE_MESSAGE_RECEIVED,
        EventType.ON_MESSAGE_RECEIVED,
    ]
    receiver._converter.envelope_to_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_decision_propagates_modified_envelope() -> None:
    """订阅者返回 SUCCESS 并回写 params['envelope'] 时，后续使用新 envelope。"""

    receiver, event_manager = _make_receiver_with_event_result(
        EventDecision.SUCCESS,
        params={"envelope": None, "adapter_signature": "plugin:adapter:qq"},
    )

    # 构造一个被插件“改写”过的 envelope：把 message_segment 改成新文本。
    rewritten_envelope = _make_envelope(text="rewritten by plugin")
    event_manager.publish_event.return_value = {
        "decision": EventDecision.SUCCESS,
        "params": {"envelope": rewritten_envelope, "adapter_signature": "plugin:adapter:qq"},
    }

    await receiver.receive_envelope(_make_envelope(text="hello"), "plugin:adapter:qq")

    # 转换器应收到被改写后的 envelope，因此其入参 message_segment 应包含 "rewritten by plugin"。
    receiver._converter.envelope_to_message.assert_awaited_once()
    received_envelope = receiver._converter.envelope_to_message.await_args.args[0]
    assert received_envelope["message_segment"][0]["data"]["text"] == "rewritten by plugin"


@pytest.mark.asyncio
async def test_in_place_mutation_visible_to_downstream_handlers() -> None:
    """订阅者就地修改 envelope 字段（不回写 params）也能影响后续流程。"""

    receiver, event_manager = _make_receiver_with_event_result(
        EventDecision.SUCCESS,
        params={"envelope": None, "adapter_signature": "plugin:adapter:qq"},
    )

    # 让“插件”在事件处理中就地修改 message_segment 的文本。
    original_text = "hello"

    async def _publish(event: EventType | str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if event == EventType.BEFORE_MESSAGE_RECEIVED:
            envelope = kwargs["envelope"]
            # 就地把 message_segment 内容改写。
            envelope["message_segment"][0]["data"]["text"] = "mutated in place"
            return {
                "decision": EventDecision.SUCCESS,
                "params": {"envelope": envelope, "adapter_signature": kwargs["adapter_signature"]},
            }
        # ON_MESSAGE_RECEIVED 走默认放行回执。
        return {"decision": EventDecision.SUCCESS, "params": {}}

    event_manager.publish_event = AsyncMock(side_effect=_publish)

    await receiver.receive_envelope(_make_envelope(text=original_text), "plugin:adapter:qq")

    receiver._converter.envelope_to_message.assert_awaited_once()
    received_envelope = receiver._converter.envelope_to_message.await_args.args[0]
    assert received_envelope["message_segment"][0]["data"]["text"] == "mutated in place"


@pytest.mark.asyncio
async def test_publish_failure_falls_through_without_blocking() -> None:
    """事件总线抛异常时，receiver 应放行原 envelope，不阻断下游链路。"""

    receiver, event_manager = _make_receiver_with_event_result(EventDecision.SUCCESS)

    # BEFORE_MESSAGE_RECEIVED 抛异常，ON_MESSAGE_RECEIVED 正常放行。
    call_count = {"n": 0}

    async def _publish(event: EventType | str, kwargs: dict[str, Any]) -> dict[str, Any]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return {"decision": EventDecision.SUCCESS, "params": kwargs}

    event_manager.publish_event = AsyncMock(side_effect=_publish)

    await receiver.receive_envelope(_make_envelope(), "plugin:adapter:qq")

    # 异常被吞掉，BEFORE 与 ON_MESSAGE_RECEIVED 都应继续触发（异常发生时回退到 SUCCESS）。
    assert event_manager.publish_event.await_count == 2
    receiver._converter.envelope_to_message.assert_awaited_once()
