"""测试 BaseChatter.modify_llm_usables 两阶段过滤流程。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.base.chatter import BaseChatter
from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType


class StaticBlockedTool(BaseTool):
    """静态作用域不匹配的工具。"""

    name = "static_blocked_tool"
    chat_type = ChatType.GROUP  # 私聊流下应在 Phase 1 直接排除


class ActivateSuccessAction(BaseAction):
    """Phase 1 通过且 go_activate 返回 True 的动作。"""

    name = "activate_success_action"
    chat_type = ChatType.PRIVATE

    async def go_activate(self) -> bool:
        return True

    async def execute(self) -> tuple[bool, str]:
        return True, "ok"


class ActivateFailAction(BaseAction):
    """Phase 1 通过但 go_activate 返回 False 的动作。"""

    name = "activate_fail_action"
    chat_type = ChatType.PRIVATE

    async def go_activate(self) -> bool:
        return False

    async def execute(self) -> tuple[bool, str]:
        return True, "fail"


class ActivateExceptionAction(BaseAction):
    """Phase 1 通过但 go_activate 抛出异常的动作。"""

    name = "activate_exception_action"
    chat_type = ChatType.PRIVATE

    async def go_activate(self) -> bool:
        raise RuntimeError("模拟激活异常")

    async def execute(self) -> tuple[bool, str]:
        return True, "err"


class ConcreteChatter(BaseChatter):
    """用于测试的具体 Chatter 子类。"""

    name = "test_chatter"

    async def execute(self) -> Any:
        yield None


@pytest.mark.asyncio
async def test_modify_llm_usables_two_phase_pipeline() -> None:
    """验证两阶段过滤流水线：Phase 1 排除不调用 go_activate，Phase 2 准确筛选。"""
    mock_plugin = MagicMock()
    chatter = ConcreteChatter(stream_id="test_stream_1", plugin=mock_plugin)

    # 构造 Mock ChatStream
    mock_stream = MagicMock()
    mock_stream.stream_id = "test_stream_1"
    mock_stream.chat_type = "private"
    mock_stream.platform = "qq"
    mock_stream.context.current_message = None
    mock_stream.context.unread_messages = []

    mock_sm = MagicMock()
    mock_sm.get_or_create_stream = AsyncMock(return_value=mock_stream)

    usables = [
        StaticBlockedTool,
        ActivateSuccessAction,
        ActivateFailAction,
        ActivateExceptionAction,
    ]

    with patch("src.core.managers.get_stream_manager", return_value=mock_sm):
        available = await chatter.modify_llm_usables(usables)

    # 只有 ActivateSuccessAction 应该存活
    assert len(available) == 1
    assert available[0] is ActivateSuccessAction
