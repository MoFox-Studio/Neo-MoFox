"""Neo-Default-Chatter 默认 Chatter。

作为 Neo-MoFox 框架聊天流调度入口，把对话主流程委托给自包含的
:class:`ConversationSession`。会话行为与第三方插件经 Service 拿到的完全一致。

恢复事件流转机制
================

框架以「驱动方 + 异步生成器」的协程模型驱动 Chatter，整体流程如下：

1. 框架调用 ``chatter.asend(None)`` 首次激活会话；
2. :meth:`NeoChatter.execute` 把 ``None`` 透传给内部
   :meth:`ConversationSession.execute` 的 ``runner``；
3. 会话产出 ``Wait / Stop / Failure`` 后挂起，``execute`` 把该结果原样
   ``yield`` 给框架；
4. 框架根据返回结果决定下一次送入什么 :class:`WaitResumeEvent`
   （定时器到期 / 新用户消息 / 子代理完成等）；
5. 框架调用 ``chatter.asend(resume)``，``execute`` 再次把它透传给会话；
6. 重复 3-5，直到会话产出 :class:`Stop` / :class:`Failure`（终态）或自然结束。

因此 ``NeoChatter`` 自身是无状态、无解释的：它不消费 ``WaitResumeEvent``，
也不构造任何结果对象，仅做「forward yield + forward asend」的桥接。
所有会话状态、阶段切换、resume 文本构造都集中在 :class:`ConversationSession`，
这就是「会话行为与第三方插件经 Service 拿到的完全一致」的根本原因。
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
    """Neo-Default-Chatter 默认聊天器，委托主会话逻辑执行。"""

    name: str = "neo_default_chatter"
    description: str = "可复用的会话逻辑中台，事件驱动预处理 + 原生多模态"
    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL
    dependencies: list[str] = []

    async def execute(
        self,
    ) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]:
        """创建主会话并转发其结果。

        本方法是 :class:`NeoChatter` 的唯一行为：构造 :class:`ConversationSession`
        并把它和框架之间通过 ``yield / asend`` 双向桥接（见模块 docstring 的「恢复事件流转机制」）。
        """
        # 1) 构造 Service 与会话实例；会话自包含、不持有 chatter 引用，
        #    因此本 chatter 实例即便被销毁，会话仍可由第三方插件继续驱动
        service = NeoChatterService(self.plugin)
        session = service.create_session(stream_id=self.stream_id, plugin=self.plugin)

        # 2) 拿到会话生成器；resume 初始为 None，对应「首轮尚未收到任何恢复事件」
        runner = session.execute()
        resume: WaitResumeEvent | None = None
        while True:
            try:
                # 3) 把上一次的 resume 透传给会话；会话消费后产出新结果或自然结束
                #    注意：chatter 不解释 resume 的语义，所有阶段切换 / 文本构造都在会话里完成
                result = await runner.asend(resume)
            except StopAsyncIteration:
                # 3a) 会话主动结束（产出 Stop / Failure 之后），整个 chatter 也随之结束
                return
            # 4) 把会话产出的 Wait / Stop / Failure / Success 原样转发给框架；
            #    框架在下次驱动时会通过 asend 把新的 resume 传回，从而进入下一轮
            resume = yield result
