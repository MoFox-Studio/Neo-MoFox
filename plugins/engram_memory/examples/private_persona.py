"""用内存人物印象演示私聊首个 User 的固定 SystemReminder，不连接数据库或模型。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from src.app.plugin_system.api import llm_api, prompt_api, stream_api
from src.app.plugin_system.types import ROLE, EventType, LLMPayload, Text

from ..config import EngramMemoryConfig
from ..plugin import EngramMemoryPlugin
from ..vnext.persona_injection import REMINDER_NAME, VNextPrivatePersonaEventHandler


async def run_example() -> None:
    """注入示例私聊印象，检查跨轮位置与唯一性，并清理内存提醒。"""
    stream_id = "engram-persona-example"
    impression = "他希望我叫他小树。我和他聊天时通常很放松。"
    snapshot = SimpleNamespace(
        person_id="person-example", impression_text=impression, is_current=True
    )
    plugin: Any = EngramMemoryPlugin(EngramMemoryConfig())
    plugin.runtime_owner = SimpleNamespace(
        persona_service=SimpleNamespace(
            get_persona=AsyncMock(return_value=snapshot),
            get_person_ref=AsyncMock(return_value="test:user-example"),
        )
    )
    handler = VNextPrivatePersonaEventHandler(plugin)
    try:
        with patch.object(
            stream_api,
            "get_stream_info",
            AsyncMock(
                return_value={
                    "chat_type": "private",
                    "person_id": "person-example",
                }
            ),
        ):
            await handler.execute(EventType.ON_CHATTER_STEP, {"stream_id": stream_id})
            request = llm_api.create_llm_request(
                cast(Any, []),
                request_name="persona_example",
                with_reminder="actor",
                stream_id=stream_id,
            )
            request.add_payload(LLMPayload(ROLE.USER, Text("第一轮聊天")))
            request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("第一轮回复")))
            request.add_payload(LLMPayload(ROLE.USER, Text("第二轮聊天")))
            params = {
                "request_name": "persona_example",
                "meta_data": {"stream_id": stream_id},
                "payloads": request.payloads,
            }
            await handler.execute(EventType.BEFORE_LLM_REQUEST, params)
            texts = [
                part.text
                for payload in request.payloads
                for part in payload.content
                if isinstance(part, Text)
            ]
            assert sum(f"[{REMINDER_NAME}]" in text for text in texts) == 1
            assert any("人物标识：test:user-example" in text for text in texts)
            assert all("person-example" not in text for text in texts)
            assert any(
                isinstance(part, Text) and impression in part.text
                for part in request.payloads[0].content
            )
            assert not any(
                isinstance(part, Text) and REMINDER_NAME in part.text
                for part in request.payloads[-1].content
            )
    finally:
        prompt_api.delete_stream_reminder(stream_id, "actor", REMINDER_NAME)
    print("私聊印象示例通过：固定注入首个 User，跨轮保持唯一，无模型调用。")


if __name__ == "__main__":
    asyncio.run(run_example())
