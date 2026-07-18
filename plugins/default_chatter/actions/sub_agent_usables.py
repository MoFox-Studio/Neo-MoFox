"""子代理管理 Usable：create_agent / get_agent / kill_agent。

这三个 Agent 组件供 LLM 在子代理协作模式下调用，
通过 DefaultChatter 的委托方法执行实际的子代理 CRUD。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.base.agent import BaseAgent

if TYPE_CHECKING:
    pass  # DefaultChatter 仅在方法体内延迟导入，避免循环


class _SubAgentManagementUsable(BaseAgent):
    """default chatter 子代理管理工具基类。"""

    chatter_allow: list[str] = ["default_chatter"]
    associated_types = ["text"]


class CreateAgentUsable(_SubAgentManagementUsable):
    """创建一个新的子代理。"""

    agent_name = "create_agent"
    agent_description = "创建一个新的子代理，并把指定的普通工具与 MCP 服务器能力委托给它。"

    async def execute(
        self,
        name: str,
        system_prompt: str,
        tools: list[str] | None = None,
        mcp: list[str] | None = None,
        allow_create_sub_agent: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """创建子代理。

        Args:
            name: 子代理的唯一标识名
            system_prompt: 追加到固定系统提示词末尾的任务描述与约束
            tools: 分配给子代理的普通工具名列表，只填工具名即可
            mcp: 分配给子代理的 MCP 服务器名列表，只填 MCP 名即可
            allow_create_sub_agent: 是否继续授予 create_agent/get_agent/kill_agent
        """
        from ..plugin import DefaultChatter

        chatter = DefaultChatter(self.stream_id, self.plugin)
        return await chatter.create_managed_sub_agent(
            name=name,
            system_prompt=system_prompt,
            tools=tools or [],
            mcp=mcp or [],
            allow_create_sub_agent=allow_create_sub_agent,
        )


class GetAgentUsable(_SubAgentManagementUsable):
    """与已存在的子代理交互。"""

    agent_name = "get_agent"
    agent_description = "查看子代理最近的活动记录，并可附带一条新的问题或指令驱动它继续执行。"

    async def execute(
        self,
        name: str,
        message_limit: int = 10,
        question: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        """获取子代理状态或向其发送新指令。

        Args:
            name: 子代理标识名
            message_limit: 最近活动记录条数，0 表示全部
            question: 要发送给子代理的问题或指令，留空则只查看状态
        """
        from ..plugin import DefaultChatter

        chatter = DefaultChatter(self.stream_id, self.plugin)
        return await chatter.query_managed_sub_agent(
            name=name,
            message_limit=message_limit,
            question=question,
        )


class KillAgentUsable(_SubAgentManagementUsable):
    """销毁一个子代理。"""

    agent_name = "kill_agent"
    agent_description = "销毁指定子代理；如果它创建过后代子代理，将级联一起销毁。"

    async def execute(self, name: str) -> tuple[bool, dict[str, Any]]:
        """销毁子代理。

        Args:
            name: 子代理标识名
        """
        from ..plugin import DefaultChatter

        chatter = DefaultChatter(self.stream_id, self.plugin)
        return await chatter.kill_managed_sub_agent(name=name)
