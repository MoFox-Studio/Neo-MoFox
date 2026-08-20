"""验证新管理器架构对 snowluma_extension 真实工具类的筛选链路。

链路：
    ToolComponentManager.filter_tools_for_chat（静态筛选 + BEFORE_TOOL_FILTER 事件钩子）
    -> GetGroupJoinRequestsTool（仅静态维度过滤，不做 go_activate 激活）

实体名单（如群白名单）过滤由 BEFORE_TOOL_FILTER 事件处理器承担。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.components.types import ChatType, EventType
from src.kernel.event import EventDecision, get_event_bus
from src.core.managers.tool_manager.manager import get_tool_component_manager
from src.core.models.stream import ChatStream

from plugins.snowluma_extension.config import SnowLumaExtensionConfig
from plugins.snowluma_extension.src.tools import GetGroupJoinRequestsTool

ALLOWED_GROUP = "160791652"
DENIED_GROUP = "169850076"


def _make_plugin(config: SnowLumaExtensionConfig) -> MagicMock:
    """构造带真实配置的 mock 插件实例。"""
    plugin = MagicMock()
    plugin.config = config
    plugin.plugin_name = "snowluma_extension"
    return plugin


def _make_message(group_id: str) -> MagicMock:
    """构造携带群号的消息对象。"""
    msg = MagicMock()
    msg.stream_id = f"qq_group_{group_id}"
    msg.extra = {"group_id": group_id}
    msg.processed_plain_text = "查询加群请求"
    msg.content = "查询加群请求"
    return msg


def _group_allowlist_handler(group_list: list[str | int]):
    """构造按群名单改写 usables 的 BEFORE_TOOL_FILTER 事件处理器。"""
    allowed = {str(g) for g in group_list}

    async def _handler(
        event_name: str, params: dict
    ) -> tuple[EventDecision, dict]:
        stream_id = str(params.get("stream_id") or "")
        group_id = stream_id.replace("qq_group_", "")
        if group_id not in allowed:
            params["usables"] = []
        return EventDecision.SUCCESS, params

    return _handler


@pytest.fixture
def manager() -> object:
    """返回全局 ToolComponentManager 实例。"""
    return get_tool_component_manager()


@pytest.fixture
def whitelist_config() -> SnowLumaExtensionConfig:
    """构造仅白名单允许指定群的配置。"""
    config = SnowLumaExtensionConfig()
    config.plugin.enabled = True
    config.join_request.enable = True
    config.join_request.group_list_type = "white"
    config.join_request.group_list = [int(ALLOWED_GROUP)]
    return config


@pytest.mark.asyncio
async def test_whitelisted_group_tool_survives_static_filter(
    manager: object,
    whitelist_config: SnowLumaExtensionConfig,
) -> None:
    """白名单群：工具应通过静态筛选与事件钩子存活。"""
    msg = _make_message(ALLOWED_GROUP)
    chat_stream = ChatStream(
        stream_id=msg.stream_id,
        platform="qq",
        chat_type=ChatType.GROUP.value,
    )

    handler = _group_allowlist_handler(whitelist_config.join_request.group_list)
    get_event_bus().subscribe(EventType.BEFORE_TOOL_FILTER, handler)
    try:
        usables = await manager.filter_tools_for_chat(  # type: ignore[attr-defined]
            [GetGroupJoinRequestsTool],
            chat_type="group",
            platform="qq",
            stream_id=msg.stream_id,
            chat_stream=chat_stream,
            stream_context=chat_stream.context,
        )
    finally:
        get_event_bus().unsubscribe(EventType.BEFORE_TOOL_FILTER, handler)

    assert GetGroupJoinRequestsTool in usables


@pytest.mark.asyncio
async def test_denied_group_tool_removed_by_before_filter_event(
    manager: object,
    whitelist_config: SnowLumaExtensionConfig,
) -> None:
    """非白名单群：工具经 BEFORE_TOOL_FILTER 事件剔除。"""
    msg = _make_message(DENIED_GROUP)
    chat_stream = ChatStream(
        stream_id=msg.stream_id,
        platform="qq",
        chat_type=ChatType.GROUP.value,
    )

    handler = _group_allowlist_handler(whitelist_config.join_request.group_list)
    get_event_bus().subscribe(EventType.BEFORE_TOOL_FILTER, handler)
    try:
        usables = await manager.filter_tools_for_chat(  # type: ignore[attr-defined]
            [GetGroupJoinRequestsTool],
            chat_type="group",
            platform="qq",
            stream_id=msg.stream_id,
            chat_stream=chat_stream,
            stream_context=chat_stream.context,
        )
    finally:
        get_event_bus().unsubscribe(EventType.BEFORE_TOOL_FILTER, handler)

    assert GetGroupJoinRequestsTool not in usables


@pytest.mark.asyncio
async def test_before_tool_filter_event_can_rewrite_usables(
    manager: object,
) -> None:
    """BEFORE_TOOL_FILTER 事件处理器可改写组件集合。"""
    bus = get_event_bus()
    event_fired = False

    async def blocker(
        event_name: str, params: dict
    ) -> tuple[EventDecision, dict]:
        nonlocal event_fired
        event_fired = True
        params["usables"] = []
        return EventDecision.SUCCESS, params

    bus.subscribe(EventType.BEFORE_TOOL_FILTER, blocker)
    try:
        filtered = await manager.filter_tools_for_chat(  # type: ignore[attr-defined]
            [GetGroupJoinRequestsTool],
            chat_type="group",
            platform="qq",
            stream_id=f"qq_group_{ALLOWED_GROUP}",
        )
    finally:
        bus.unsubscribe(EventType.BEFORE_TOOL_FILTER, blocker)

    assert event_fired is True
    assert GetGroupJoinRequestsTool not in filtered
