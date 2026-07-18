"""Default Chatter 可用工具解析与子代理协作 CRUD 模块。

封装子代理协作模式下的工具解析、MCP 工具过滤、以及子代理 CRUD 委托逻辑，
使 DefaultChatter 不再直接承载这些实现细节。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.llm import LLMPayload, ROLE, ToolRegistry
from src.kernel.llm.payload.tooling import LLMUsable

from .actions import CreateAgentUsable, GetAgentUsable, KillAgentUsable
from .prompt_builder import DefaultChatterPromptBuilder
from .sub_agent_collaboration import (
    FIXED_SUB_AGENT_SYSTEM_PROMPT,
    get_active_sub_agent_name,
    get_sub_agent_collaboration_manager,
)

if TYPE_CHECKING:
    from .plugin import DefaultChatter


def is_mcp_usable_class(usable_cls: type[LLMUsable]) -> bool:
    """判断一个 usable 是否来源于 MCP 动态工具。"""
    signature = getattr(usable_cls, "get_signature", lambda: None)()
    if isinstance(signature, str) and signature.startswith("mcp_provider:tool:"):
        return True

    schema = usable_cls.to_schema()
    function_name = schema.get("function", {}).get("name")
    return isinstance(function_name, str) and function_name.startswith("mcp-")


def normalized_usable_names(usable_cls: type[LLMUsable]) -> set[str]:
    """生成一个 usable 可匹配的名字集合。"""
    schema = usable_cls.to_schema()
    function_name = schema.get("function", {}).get("name")
    names: set[str] = set()
    if isinstance(function_name, str) and function_name:
        names.add(function_name)
        if "-" in function_name:
            names.add(function_name.split("-", 1)[1])
    return names


def get_deferred_mcp_usable_classes() -> set[type[LLMUsable]]:
    """返回仅允许子代理使用的 MCP 工具类集合。"""
    from src.core.managers.tool_manager import get_mcp_manager

    return set(get_mcp_manager().get_deferred_tool_classes())


def get_sub_agent_collaboration_usables() -> list[type[LLMUsable]]:
    """返回主代理协作模式下注入的管理工具。"""
    return [CreateAgentUsable, GetAgentUsable, KillAgentUsable]


def build_sub_agent_collaboration_system_extra(
    chatter: "DefaultChatter",
) -> str:
    """构建子代理协作模式下追加到系统提示词的额外说明。"""
    if not chatter._is_sub_agent_collaboration_enabled():
        return ""

    from src.core.managers.tool_manager import get_mcp_manager

    metadata = get_mcp_manager().get_connected_server_metadata()
    return DefaultChatterPromptBuilder.build_sub_agent_collaboration_extra(metadata)


async def inject_collaboration_usables(
    chatter: "DefaultChatter",
    request: Any,
) -> Any:
    """子代理协作模式下的工具注入：隐藏 defer_loading 的 MCP 工具，追加管理 Usable。

    调用方（DefaultChatter.inject_usables）负责在协作未开启时走 super().inject_usables()。
    """
    usables = await chatter.get_llm_usables()
    usables = await chatter.modify_llm_usables(usables)
    deferred_mcp_usables = get_deferred_mcp_usable_classes()
    filtered_usables = [
        usable_cls
        for usable_cls in usables
        if usable_cls not in deferred_mcp_usables
    ]
    filtered_usables.extend(get_sub_agent_collaboration_usables())

    registry = ToolRegistry()
    for usable_cls in filtered_usables:
        registry.register(usable_cls)

    if registry.get_all():
        request.add_payload(LLMPayload(ROLE.TOOL, registry.get_all()))  # type: ignore[arg-type]

    return registry


async def resolve_sub_agent_usable_classes(
    chatter: "DefaultChatter",
    tools: list[str],
    mcp: list[str],
    allow_create_sub_agent: bool,
) -> tuple[list[type[LLMUsable]], list[str], list[str], list[str], list[str]]:
    """解析子代理请求的普通工具与 MCP 能力。

    Args:
        chatter: DefaultChatter 实例，提供 get_llm_usables / modify_llm_usables
        tools: LLM 请求的普通工具名列表
        mcp: LLM 请求的 MCP 服务器名列表
        allow_create_sub_agent: 是否继续授予子代理管理工具

    Returns:
        (已解析 usable 类列表, 已解析工具名, 已解析 MCP 名, 非法工具名, 非法 MCP 名)
    """
    requested_tools = [tool_name.strip() for tool_name in tools if tool_name.strip()]
    requested_mcp = [mcp_name.strip() for mcp_name in mcp if mcp_name.strip()]

    usables = await chatter.get_llm_usables()
    usables = await chatter.modify_llm_usables(usables)

    normal_usable_map: dict[str, type[LLMUsable]] = {}
    for usable_cls in usables:
        if is_mcp_usable_class(usable_cls):
            continue
        for alias_name in normalized_usable_names(usable_cls):
            normal_usable_map.setdefault(alias_name, usable_cls)

    resolved_usables: list[type[LLMUsable]] = []
    resolved_tool_names: list[str] = []
    invalid_tools: list[str] = []
    seen_classes: set[type[LLMUsable]] = set()

    for tool_name in requested_tools:
        usable_cls = normal_usable_map.get(tool_name)
        if usable_cls is None:
            invalid_tools.append(tool_name)
            continue
        if usable_cls in seen_classes:
            continue
        seen_classes.add(usable_cls)
        resolved_usables.append(usable_cls)
        resolved_tool_names.append(tool_name)

    from src.core.managers.tool_manager import get_mcp_manager

    mcp_manager = get_mcp_manager()
    connected_mcp_names = {
        metadata.server_name for metadata in mcp_manager.get_connected_server_metadata()
    }
    invalid_mcp = [mcp_name for mcp_name in requested_mcp if mcp_name not in connected_mcp_names]
    resolved_mcp_names = [mcp_name for mcp_name in requested_mcp if mcp_name in connected_mcp_names]

    for usable_cls in mcp_manager.get_tool_classes_for_servers(resolved_mcp_names):
        if usable_cls in seen_classes:
            continue
        seen_classes.add(usable_cls)
        resolved_usables.append(usable_cls)

    if allow_create_sub_agent:
        for usable_cls in get_sub_agent_collaboration_usables():
            if usable_cls in seen_classes:
                continue
            seen_classes.add(usable_cls)
            resolved_usables.append(usable_cls)

    return (
        resolved_usables,
        resolved_tool_names,
        resolved_mcp_names,
        invalid_tools,
        invalid_mcp,
    )


def build_sub_agent_system_prompt(
    system_prompt: str,
    mcp_names: list[str],
) -> str:
    """拼接固定子代理系统提示词与委托提示。"""
    sections = [FIXED_SUB_AGENT_SYSTEM_PROMPT.strip()]
    if mcp_names:
        sections.append(
            "你被分配到了以下 MCP 服务器的能力：" + "、".join(mcp_names)
        )
    if system_prompt.strip():
        sections.append(system_prompt.strip())
    return "\n\n".join(section for section in sections if section)


async def create_managed_sub_agent(
    chatter: "DefaultChatter",
    *,
    name: str,
    system_prompt: str,
    tools: list[str],
    mcp: list[str],
    allow_create_sub_agent: bool,
) -> tuple[bool, dict[str, Any]]:
    """创建一个受管子代理。"""
    usable_classes, resolved_tool_names, resolved_mcp_names, invalid_tools, invalid_mcp = (
        await resolve_sub_agent_usable_classes(
            chatter,
            tools=tools,
            mcp=mcp,
            allow_create_sub_agent=allow_create_sub_agent,
        )
    )
    if invalid_tools or invalid_mcp:
        return False, {
            "invalid_tools": invalid_tools,
            "invalid_mcp": invalid_mcp,
            "name": name,
        }

    manager = get_sub_agent_collaboration_manager()
    try:
        snapshot = manager.create_agent(
            chatter=chatter,
            name=name,
            system_prompt=build_sub_agent_system_prompt(
                system_prompt=system_prompt,
                mcp_names=resolved_mcp_names,
            ),
            usable_classes=usable_classes,
            allowed_tool_names=resolved_tool_names,
            allowed_mcp_names=resolved_mcp_names,
            allow_create_sub_agent=allow_create_sub_agent,
            enable_action_suspend=chatter._is_action_suspend_enabled(),
            parent_name=get_active_sub_agent_name(),
        )
    except ValueError as error:
        return False, {"error": str(error), "name": name}
    return True, snapshot


async def query_managed_sub_agent(
    chatter: "DefaultChatter",
    *,
    name: str,
    message_limit: int,
    question: str,
) -> tuple[bool, dict[str, Any]]:
    """查询或驱动一个受管子代理。"""
    manager = get_sub_agent_collaboration_manager()
    try:
        snapshot = await manager.get_agent(
            chatter=chatter,
            name=name,
            question=question,
            message_limit=max(0, int(message_limit)),
            enable_action_suspend=chatter._is_action_suspend_enabled(),
        )
    except ValueError as error:
        return False, {"error": str(error), "name": name}
    return True, snapshot


def kill_managed_sub_agent(
    chatter: "DefaultChatter",
    *,
    name: str,
) -> tuple[bool, dict[str, Any]]:
    """销毁一个受管子代理。"""
    manager = get_sub_agent_collaboration_manager()
    try:
        result = manager.kill_agent(stream_id=chatter.stream_id, name=name)
    except ValueError as error:
        return False, {"error": str(error), "name": name}
    return True, result
