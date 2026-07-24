"""Neo-Chatter 默认 Chatter。

作为 Neo-MoFox 框架聊天流调度入口，把对话主流程委托给自包含的
:class:`ConversationSession`。会话行为与第三方插件经 Service 拿到的完全一致。
"""

from __future__ import annotations

from typing import AsyncGenerator

from src.app.plugin_system.base import (
    BaseChatter,
    Failure,
    Stop,
    Success,
    Wait,
    WaitResumeEvent,
)
from src.app.plugin_system.types import ChatType

from .service import NeoChatterService


class NeoChatter(BaseChatter):
    """Neo-Chatter 默认聊天器，委托主会话逻辑执行。"""

    name: str = "neo_chatter"
    description: str = "可复用的会话逻辑中台，事件驱动预处理 + 原生多模态"
    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL
    dependencies: list[str] = []

    async def execute(
        self,
    ) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]:
        """创建主会话并转发其结果。

        驱动 :class:`ConversationSession`，把框架送来的 ``WaitResumeEvent``
        经 ``asend`` 回传给会话，会话产出的 ``Wait/Success/Failure/Stop``
        原样转发给框架。
        """
        service = NeoChatterService(self.plugin)
        session = service.create_session(stream_id=self.stream_id, plugin=self.plugin)
        runner = session.execute()
        resume: WaitResumeEvent | None = None
        while True:
            try:
                result = await runner.asend(resume)
            except StopAsyncIteration:
                return
            resume = yield result
