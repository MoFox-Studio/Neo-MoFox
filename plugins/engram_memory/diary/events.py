"""聊天日记运行时的事件唤醒与 LLM 提醒注入。"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from src.app.plugin_system.api.event_api import EventDecision
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType

from .injection import _contains_diary_block, refresh_diary_payloads

if TYPE_CHECKING:
    from typing import Any


class _DiaryRuntime(Protocol):
    """聊天日记运行时的事件侧接口。"""

    def start(self) -> None:
        """启动后台日记处理。"""

    def observe_stream(self, stream_id: str) -> None:
        """唤醒指定流的后台处理。"""

    async def reminder_content(self, stream_id: str) -> str:
        """读取流当前允许注入的日记内容。"""

    def set_reminder(self, stream_id: str, content: str) -> None:
        """更新流日记提醒并登记其生命周期。"""


class _RuntimeOwner(Protocol):
    """插件 runtime owner 的日记运行时接口。"""

    diary: _DiaryRuntime | None


class _DiaryPlugin(Protocol):
    """事件处理器所需的插件最小接口。"""

    runtime_owner: _RuntimeOwner | None


class ChatDiaryEventHandler(BaseEventHandler):
    """启动聊天日记后台处理并维护 Actor 请求的流私有提醒。"""

    name = "chat_diary"
    description = "唤醒聊天日记并将当前许可内容注入 Actor 请求"
    init_subscribe: ClassVar[list[EventType]] = [
        EventType.ON_ALL_PLUGIN_LOADED,
        EventType.ON_MESSAGE_RECEIVED,
        EventType.AFTER_MESSAGE_SENT,
        EventType.ON_CHATTER_STEP,
        EventType.BEFORE_LLM_REQUEST,
    ]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """根据事件类型启动、唤醒或刷新当前流日记提醒。"""
        runtime = self._runtime()
        if event_name == EventType.ON_ALL_PLUGIN_LOADED:
            if runtime is not None:
                runtime.start()
            return EventDecision.SUCCESS, params

        if event_name in (EventType.ON_MESSAGE_RECEIVED, EventType.AFTER_MESSAGE_SENT):
            message = params.get("message")
            stream_id = getattr(message, "stream_id", "") if message is not None else ""
            if runtime is not None and isinstance(stream_id, str) and stream_id.strip():
                runtime.observe_stream(stream_id)
            return EventDecision.SUCCESS, params

        if runtime is None:
            return EventDecision.SUCCESS, params

        if event_name == EventType.ON_CHATTER_STEP:
            stream_id = params.get("stream_id")
            if isinstance(stream_id, str) and stream_id.strip():
                content = await runtime.reminder_content(stream_id)
                runtime.set_reminder(stream_id, content)
            return EventDecision.SUCCESS, params

        if event_name == EventType.BEFORE_LLM_REQUEST:
            await self._refresh_request(runtime, params)
        return EventDecision.SUCCESS, params

    async def _refresh_request(
        self, runtime: _DiaryRuntime, params: dict[str, Any]
    ) -> None:
        """只刷新带 Actor 日记标记且属于聊天流的实际请求 payload。"""
        request_name = params.get("request_name")
        if request_name in {"engram_chat_diary_update", "engram_vnext_persona_update"}:
            return
        meta_data = params.get("meta_data")
        if not isinstance(meta_data, dict):
            return
        stream_id = meta_data.get("stream_id")
        payloads = params.get("payloads")
        if (
            not isinstance(stream_id, str)
            or not stream_id.strip()
            or not isinstance(payloads, list)
            or not _contains_diary_block(payloads)
        ):
            return

        content = await runtime.reminder_content(stream_id)
        if refresh_diary_payloads(payloads, content):
            params["payloads"] = payloads
        runtime.set_reminder(stream_id, content)

    def _runtime(self) -> _DiaryRuntime | None:
        """读取插件 owner 当前持有的聊天日记运行时。"""
        plugin = cast(_DiaryPlugin, self.plugin)
        owner = plugin.runtime_owner
        return owner.diary if owner is not None else None


__all__ = ["ChatDiaryEventHandler"]
