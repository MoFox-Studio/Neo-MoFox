"""子代理管理组件和主会话协作工具注入。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.chat_api import get_chatter_by_stream
from src.app.plugin_system.api.llm_api import LLMRequest
from src.app.plugin_system.base import BaseAgent, BaseChatter
from src.app.plugin_system.types import LLMPayload, LLMUsable, ROLE, ToolRegistry

from .components.config import DefaultChatterConfig
from .utils.prompt_builder import DefaultChatterPromptBuilder
from .sub_agent_collaboration import (
    FIXED_SUB_AGENT_SYSTEM_PROMPT,
    get_active_sub_agent_name,
    get_sub_agent_collaboration_manager,
)


def get_plugin_config(plugin: Any) -> DefaultChatterConfig | None:
    """返回插件配置；配置类型不匹配时返回 None。"""
    config = getattr(plugin, "config", None)
    return config if isinstance(config, DefaultChatterConfig) else None


def is_collaboration_enabled(plugin: Any) -> bool:
    """返回是否启用子代理协作。"""
    config = get_plugin_config(plugin)
    return bool(config and config.plugin.enable_sub_agent_collaboration)


def is_action_suspend_enabled(plugin: Any) -> bool:
    """返回纯 Action 回合是否启用挂起机制。"""
    config = get_plugin_config(plugin)
    return config is None or bool(config.plugin.enable_action_suspend)


def get_sub_agent_task_name(plugin: Any) -> str:
    """返回协作子代理使用的模型任务名。"""
    config = get_plugin_config(plugin)
    if config is None:
        return "actor"
    task_name = config.plugin.sub_agent_task_name.strip()
    return task_name or "actor"


def is_mcp_usable_class(usable_cls: type[LLMUsable]) -> bool:
    """判断 usable 类是否为 MCP 动态工具。"""
    signature = getattr(usable_cls, "get_signature", lambda: None)()
    if isinstance(signature, str) and signature.startswith("mcp_provider:tool:"):
        return True

    function_name = usable_cls.to_schema().get("function", {}).get("name")
    return isinstance(function_name, str) and function_name.startswith("mcp-")


def normalized_usable_names(usable_cls: type[LLMUsable]) -> set[str]:
    """返回 usable schema 名称及其去除组件前缀后的名称。"""
    function_name = usable_cls.to_schema().get("function", {}).get("name")
    if not isinstance(function_name, str) or not function_name:
        return set()

    names = {function_name}
    for prefix in ("tool-", "action-", "agent-"):
        if function_name.startswith(prefix):
            names.add(function_name[len(prefix):])
            break
    return names


def get_deferred_mcp_usable_classes() -> set[type[LLMUsable]]:
    """返回仅向子代理提供的 MCP 工具类。"""
    from src.core.managers.tool_manager import get_mcp_manager

    return set(get_mcp_manager().get_deferred_tool_classes())


def get_management_usables() -> list[type[LLMUsable]]:
    """返回子代理管理组件类。"""
    return [CreateAgentUsable, GetAgentUsable, KillAgentUsable]


def build_collaboration_system_extra(plugin: Any) -> str:
    """构建子代理协作说明；未启用协作时返回空字符串。"""
    if not is_collaboration_enabled(plugin):
        return ""

    from src.core.managers.tool_manager import get_mcp_manager

    metadata = get_mcp_manager().get_connected_server_metadata()
    return DefaultChatterPromptBuilder.build_sub_agent_collaboration_extra(metadata)


async def inject_collaboration_usables(
    runtime: BaseChatter,
    request: LLMRequest,
) -> ToolRegistry:
    """向主会话注入普通工具和子代理管理组件，并隐藏延迟加载的 MCP 工具。"""
    usables = await runtime.get_llm_usables()
    usables = await runtime.modify_llm_usables(usables)
    deferred_mcp_usables = get_deferred_mcp_usable_classes()

    registry = ToolRegistry()
    for usable_cls in usables:
        if usable_cls not in deferred_mcp_usables:
            registry.register(usable_cls)
    for usable_cls in get_management_usables():
        registry.register(usable_cls)

    schemas = registry.get_all()
    if schemas:
        request.add_payload(LLMPayload(ROLE.TOOL, schemas))  # type: ignore[arg-type]
    return registry


async def resolve_sub_agent_usable_classes(
    runtime: BaseChatter,
    tools: list[str],
    mcp: list[str],
    allow_create_sub_agent: bool,
) -> tuple[list[type[LLMUsable]], list[str], list[str], list[str], list[str]]:
    """解析分配给子代理的普通工具和 MCP 服务器。

    Args:
        runtime: 当前活跃 Chatter
        tools: 普通工具名列表
        mcp: MCP 服务器名列表
        allow_create_sub_agent: 是否分配子代理管理组件

    Returns:
        usable 类、有效工具名、有效 MCP 名、无效工具名和无效 MCP 名
    """
    requested_tools = [name.strip() for name in tools if name.strip()]
    requested_mcp = [name.strip() for name in mcp if name.strip()]

    usables = await runtime.get_llm_usables()
    usables = await runtime.modify_llm_usables(usables)

    normal_usable_map: dict[str, type[LLMUsable]] = {}
    for usable_cls in usables:
        if is_mcp_usable_class(usable_cls):
            continue
        for name in normalized_usable_names(usable_cls):
            normal_usable_map.setdefault(name, usable_cls)

    resolved_usables: list[type[LLMUsable]] = []
    resolved_tool_names: list[str] = []
    invalid_tools: list[str] = []
    seen_classes: set[type[LLMUsable]] = set()

    for tool_name in requested_tools:
        usable_cls = normal_usable_map.get(tool_name)
        if usable_cls is None:
            invalid_tools.append(tool_name)
            continue
        if usable_cls not in seen_classes:
            seen_classes.add(usable_cls)
            resolved_usables.append(usable_cls)
            resolved_tool_names.append(tool_name)

    from src.core.managers.tool_manager import get_mcp_manager

    mcp_manager = get_mcp_manager()
    connected_mcp_names = {
        metadata.server_name for metadata in mcp_manager.get_connected_server_metadata()
    }
    invalid_mcp = [name for name in requested_mcp if name not in connected_mcp_names]
    resolved_mcp_names = [name for name in requested_mcp if name in connected_mcp_names]

    for usable_cls in mcp_manager.get_tool_classes_for_servers(resolved_mcp_names):
        if usable_cls not in seen_classes:
            seen_classes.add(usable_cls)
            resolved_usables.append(usable_cls)

    if allow_create_sub_agent:
        for usable_cls in get_management_usables():
            if usable_cls not in seen_classes:
                seen_classes.add(usable_cls)
                resolved_usables.append(usable_cls)

    return (
        resolved_usables,
        resolved_tool_names,
        resolved_mcp_names,
        invalid_tools,
        invalid_mcp,
    )


def build_sub_agent_system_prompt(system_prompt: str, mcp_names: list[str]) -> str:
    """构建子代理系统提示词。"""
    sections = [FIXED_SUB_AGENT_SYSTEM_PROMPT.strip()]
    if mcp_names:
        sections.append("已分配的 MCP 服务器：" + "、".join(mcp_names))
    if system_prompt.strip():
        sections.append(system_prompt.strip())
    return "\n\n".join(sections)


def require_active_runtime(stream_id: str) -> BaseChatter:
    """返回指定会话流的活跃 Chatter。

    Raises:
        ValueError: 会话流没有活跃的 default_chatter 实例
    """
    runtime = get_chatter_by_stream(stream_id)
    if runtime is None or runtime.name != "default_chatter":
        raise ValueError("当前会话流没有活跃的 default_chatter")
    return runtime


async def create_managed_sub_agent(
    runtime: BaseChatter,
    *,
    name: str,
    system_prompt: str,
    tools: list[str],
    mcp: list[str],
    allow_create_sub_agent: bool,
) -> tuple[bool, dict[str, Any]]:
    """创建受管子代理。"""
    usable_classes, tool_names, mcp_names, invalid_tools, invalid_mcp = (
        await resolve_sub_agent_usable_classes(
            runtime,
            tools=tools,
            mcp=mcp,
            allow_create_sub_agent=allow_create_sub_agent,
        )
    )
    if invalid_tools or invalid_mcp:
        return False, {
            "name": name,
            "invalid_tools": invalid_tools,
            "invalid_mcp": invalid_mcp,
        }

    try:
        snapshot = get_sub_agent_collaboration_manager().create_agent(
            chatter=runtime,
            name=name,
            system_prompt=build_sub_agent_system_prompt(system_prompt, mcp_names),
            usable_classes=usable_classes,
            allowed_tool_names=tool_names,
            allowed_mcp_names=mcp_names,
            allow_create_sub_agent=allow_create_sub_agent,
            enable_action_suspend=is_action_suspend_enabled(runtime.plugin),
            task_name=get_sub_agent_task_name(runtime.plugin),
            parent_name=get_active_sub_agent_name(),
        )
    except ValueError as error:
        return False, {"error": str(error), "name": name}
    return True, snapshot


async def query_managed_sub_agent(
    runtime: BaseChatter,
    *,
    name: str,
    message_limit: int,
    question: str,
) -> tuple[bool, dict[str, Any]]:
    """查询受管子代理，并可追加一条指令。"""
    try:
        snapshot = await get_sub_agent_collaboration_manager().get_agent(
            chatter=runtime,
            name=name,
            question=question,
            message_limit=max(0, message_limit),
            enable_action_suspend=is_action_suspend_enabled(runtime.plugin),
        )
    except ValueError as error:
        return False, {"error": str(error), "name": name}
    return True, snapshot


def kill_managed_sub_agent(
    runtime: BaseChatter,
    *,
    name: str,
) -> tuple[bool, dict[str, Any]]:
    """销毁受管子代理及其后代。"""
    try:
        result = get_sub_agent_collaboration_manager().kill_agent(
            stream_id=runtime.stream_id,
            name=name,
        )
    except ValueError as error:
        return False, {"error": str(error), "name": name}
    return True, result


class _SubAgentManagementUsable(BaseAgent):
    """子代理管理组件基类。"""

    chatter_allow: list[str] = ["default_chatter"]
    associated_types = ["text"]


class CreateAgentUsable(_SubAgentManagementUsable):
    """创建受管子代理。"""

    name = "create_agent"
    description = "创建子代理，并分配指定的普通工具和 MCP 服务器能力。"

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
            name: 子代理唯一名称
            system_prompt: 子代理任务和约束
            tools: 分配的普通工具名
            mcp: 分配的 MCP 服务器名
            allow_create_sub_agent: 是否允许该子代理管理下级子代理
        """
        try:
            runtime = require_active_runtime(self.stream_id)
        except ValueError as error:
            return False, {"error": str(error), "name": name}
        return await create_managed_sub_agent(
            runtime,
            name=name,
            system_prompt=system_prompt,
            tools=tools or [],
            mcp=mcp or [],
            allow_create_sub_agent=allow_create_sub_agent,
        )


class GetAgentUsable(_SubAgentManagementUsable):
    """查询受管子代理或向其追加指令。"""

    name = "get_agent"
    description = "返回子代理状态和最近活动，并可追加一条问题或指令。"

    async def execute(
        self,
        name: str,
        message_limit: int = 10,
        question: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        """查询子代理。

        Args:
            name: 子代理名称
            message_limit: 返回的最近活动条数，0 表示全部
            question: 追加给子代理的问题或指令
        """
        try:
            runtime = require_active_runtime(self.stream_id)
        except ValueError as error:
            return False, {"error": str(error), "name": name}
        return await query_managed_sub_agent(
            runtime,
            name=name,
            message_limit=message_limit,
            question=question,
        )


class KillAgentUsable(_SubAgentManagementUsable):
    """销毁受管子代理。"""

    name = "kill_agent"
    description = "销毁指定子代理及其后代。"

    async def execute(self, name: str) -> tuple[bool, dict[str, Any]]:
        """销毁子代理。

        Args:
            name: 子代理名称
        """
        try:
            runtime = require_active_runtime(self.stream_id)
        except ValueError as error:
            return False, {"error": str(error), "name": name}
        return kill_managed_sub_agent(runtime, name=name)
