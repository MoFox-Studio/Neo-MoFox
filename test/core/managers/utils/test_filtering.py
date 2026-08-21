"""组件筛选公共逻辑测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType, EventType
from src.core.managers.utils.filtering import filter_component_classes


class _GroupAction(BaseAction):
    name = "group_action"
    description = "group"
    chat_type = ChatType.GROUP
    associated_types = ["text"]

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


class _PrivateAction(BaseAction):
    name = "private_action"
    description = "private"
    chat_type = ChatType.PRIVATE
    associated_types = ["text"]

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


class _AllowedTool(BaseTool):
    name = "allowed_tool"
    description = "allowed"
    chatter_allow = ["my_chatter"]

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


class _RejectedTool(BaseTool):
    name = "rejected_tool"
    description = "rejected"
    chatter_allow = ["other_chatter"]

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


class _PlatformTool(BaseTool):
    name = "platform_tool"
    description = "platform"
    associated_platforms = ["qq"]

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


class _OpenTool(BaseTool):
    name = "open_tool"
    description = "open"

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


_EVENT = EventType.BEFORE_TOOL_FILTER


@pytest.mark.asyncio
async def test_empty_input_returns_empty() -> None:
    """空输入直接返回空列表，不访问聊天流。"""
    with patch("src.core.managers.get_stream_manager") as mock_sm, patch(
        "src.core.managers.get_plugin_manager"
    ) as mock_pm:
        result = await filter_component_classes([], event_type=_EVENT)

    assert result == []
    mock_sm.assert_not_called()
    mock_pm.assert_not_called()


@pytest.mark.asyncio
async def test_without_stream_id_applies_static_filters_only() -> None:
    """未提供 stream_id 时仅做静态过滤。"""
    with patch("src.core.managers.get_stream_manager") as mock_sm:
        result = await filter_component_classes(
            [_GroupAction, _PrivateAction],
            event_type=_EVENT,
            chat_type=ChatType.PRIVATE,
        )

    assert result == [_PrivateAction]
    mock_sm.assert_not_called()


@pytest.mark.asyncio
async def test_chatter_allow_filter() -> None:
    """按 chatter_allow 过滤组件。"""
    result = await filter_component_classes(
        [_AllowedTool, _RejectedTool, _OpenTool],
        event_type=_EVENT,
        chatter_name="my_chatter",
    )

    assert _AllowedTool in result
    assert _OpenTool in result
    assert _RejectedTool not in result


@pytest.mark.asyncio
async def test_chatter_signature_filter() -> None:
    """按 chatter 签名过滤组件。"""

    class _SigTool(BaseTool):
        name = "sig_tool"
        description = "sig"
        chatter_allow = ["plugin:chatter:demo"]

        async def execute(self) -> tuple[bool, str]:
            return True, "ok"

    result = await filter_component_classes(
        [_SigTool],
        event_type=_EVENT,
        chatter_signature="plugin:chatter:demo",
    )

    assert _SigTool in result


@pytest.mark.asyncio
async def test_platform_filter() -> None:
    """按平台关联过滤组件。"""
    result = await filter_component_classes(
        [_PlatformTool, _OpenTool],
        event_type=_EVENT,
        platform="qq",
    )

    assert _PlatformTool in result
    assert _OpenTool in result


@pytest.mark.asyncio
async def test_filter_publishes_event_before_filtering() -> None:
    """筛选前应发布事件，事件可替换组件类。"""
    from src.kernel.event import EventDecision, get_event_bus

    event_bus = get_event_bus()
    received = {}

    async def _handler(event_name: str, params: dict) -> tuple[EventDecision, dict]:
        received.update(params)
        return EventDecision.SUCCESS, params

    unsubscribe = event_bus.subscribe(_EVENT, _handler)
    try:
        with patch("src.core.managers.get_stream_manager") as mock_sm:
            result = await filter_component_classes(
                [_OpenTool],
                event_type=_EVENT,
                stream_id="stream_123",
                chatter_name="my_chatter",
            )
    finally:
        unsubscribe()

    assert result == [_OpenTool]
    assert received["stream_id"] == "stream_123"
    assert received["chatter_name"] == "my_chatter"
    assert received["component_classes"] == [_OpenTool]
    mock_sm.assert_not_called()