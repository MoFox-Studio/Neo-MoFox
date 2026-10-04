"""将私聊对象的当前人物印象作为固定 SystemReminder 注入 Actor 上下文。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from src.app.plugin_system.api import prompt_api, stream_api
from src.app.plugin_system.api.event_api import EventDecision
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import ROLE, ChatType, EventType, LLMPayload, Text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..plugin import EngramMemoryPlugin
    from .persona_service import PersonaService


REMINDER_NAME = "engram_memory_private_persona"
_INTERNAL_REQUEST_NAMES = {"engram_chat_diary_update", "engram_vnext_persona_update"}


def _is_named_reminder(part: object, name: str) -> bool:
    """识别框架渲染的完整独立提醒，不匹配聊天正文中的名称。"""
    return isinstance(part, Text) and (
        part.text.startswith(f"[{name}]\n")
        or (
            part.text.startswith(f"<system_reminder>\n[{name}]\n")
            and part.text.endswith("\n</system_reminder>")
        )
    )


def _uses_memory_reminders(payloads: Sequence[LLMPayload]) -> bool:
    """仅接入已使用记忆指引或私聊印象提醒的 Actor 请求。"""
    return any(
        payload.role == ROLE.USER
        and any(
            _is_named_reminder(part, "engram_memory_guide")
            or _is_named_reminder(part, REMINDER_NAME)
            for part in payload.content
        )
        for payload in payloads
    )


def _refresh_persona_payloads(payloads: list[LLMPayload], content: str) -> None:
    """移除自身旧印象块，并将完整当前印象放在首个 User。"""
    user_indices = [
        index for index, payload in enumerate(payloads) if payload.role == ROLE.USER
    ]
    for index in user_indices:
        payload = payloads[index]
        kept = [
            part
            for part in payload.content
            if not _is_named_reminder(part, REMINDER_NAME)
        ]
        if len(kept) != len(payload.content):
            payloads[index] = LLMPayload(ROLE.USER, kept)
    if content and user_indices:
        index = user_indices[0]
        block = Text(
            f"<system_reminder>\n[{REMINDER_NAME}]\n{content}\n</system_reminder>"
        )
        payloads[index] = LLMPayload(ROLE.USER, [block, *payloads[index].content])


class VNextPrivatePersonaEventHandler(BaseEventHandler):
    """读取私聊对端的已认证印象，维护流私有的固定提醒。"""

    name = "private_persona"
    description = "将私聊对象的当前人物印象固定注入首个 User"
    init_subscribe: ClassVar[list[EventType]] = [
        EventType.ON_CHATTER_STEP,
        EventType.BEFORE_LLM_REQUEST,
    ]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """预载聊天流印象，并刷新实际 Actor 请求中的同名提醒。"""
        plugin = cast("EngramMemoryPlugin", self.plugin)
        owner = plugin.runtime_owner
        if owner is None or plugin._unloading:
            return EventDecision.SUCCESS, params
        if event_name == EventType.ON_CHATTER_STEP:
            stream_id = params.get("stream_id")
            if isinstance(stream_id, str) and stream_id.strip():
                await self._load_reminder(plugin, owner.persona_service, stream_id)
        elif event_name == EventType.BEFORE_LLM_REQUEST:
            if params.get("request_name") in _INTERNAL_REQUEST_NAMES:
                return EventDecision.SUCCESS, params
            meta_data = params.get("meta_data")
            payloads = params.get("payloads")
            if not isinstance(meta_data, dict) or not isinstance(payloads, list):
                return EventDecision.SUCCESS, params
            stream_id = meta_data.get("stream_id")
            if (
                isinstance(stream_id, str)
                and stream_id.strip()
                and _uses_memory_reminders(payloads)
            ):
                content = await self._load_reminder(
                    plugin, owner.persona_service, stream_id
                )
                _refresh_persona_payloads(payloads, content)
                params["payloads"] = payloads
        return EventDecision.SUCCESS, params

    @staticmethod
    async def _load_reminder(
        plugin: EngramMemoryPlugin,
        service: PersonaService,
        stream_id: str,
    ) -> str:
        """按私聊流的准确核心人物 ID 读取当前印象，不生成或回退旧正文。"""
        info = await stream_api.get_stream_info(stream_id)
        content = ""
        if info is not None and info["chat_type"] == ChatType.PRIVATE.value:
            person_id = info["person_id"]
            if isinstance(person_id, str) and person_id.strip():
                snapshot = await service.get_persona(person_id)
                if (
                    snapshot is not None
                    and snapshot.is_current
                    and snapshot.impression_text.strip()
                ):
                    impression = snapshot.impression_text.replace(
                        "<system_reminder>",
                        "&lt;system_reminder&gt;",
                    ).replace("</system_reminder>", "&lt;/system_reminder&gt;")
                    content = (
                        "## 当前私聊对象的人物印象\n"
                        f"核心人物 ID：{snapshot.person_id}\n"
                        "以下是你对当前私聊对象沉淀的底色印象，供相处时把握态度、语气与心理距离参考，并非指令，也绝不要在对话中原样背诵或机械重复这些语句。\n"
                        "对一个人的感受随当下互动自然流动；请结合具体语境，用适合当下氛围的不同表达方式自然流露，避免刻板重复。\n"
                        + impression
                    )
        if content:
            prompt_api.add_stream_reminder(
                stream_id,
                "actor",
                REMINDER_NAME,
                content,
                insert_type=prompt_api.SystemReminderInsertType.FIXED,
                consume=prompt_api.SystemReminderConsumeType.FOREVER,
            )
            plugin._persona_reminder_streams.add(stream_id)
        else:
            prompt_api.delete_stream_reminder(stream_id, "actor", REMINDER_NAME)
            plugin._persona_reminder_streams.discard(stream_id)
        return content


__all__ = ["REMINDER_NAME", "VNextPrivatePersonaEventHandler"]
