"""MCP 工具适配器。

本模块提供 MCP (Model Context Protocol) 工具适配器，将 MCP 协议的工具
适配为标准的 Tool 组件，使其能够被插件系统识别和调用。
"""

import mcp.types
from typing import TYPE_CHECKING, Any
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.managers.tool_manager.mcp_manager import MCPManager

logger = get_logger("mcp_adapter")


def _normalize_tool_name_part(value: str) -> str:
    """将 MCP 工具名片段规范化为 LLM tool name 友好的短横线格式。"""
    return value.strip().replace("_", "-")


class MCPToolAdapter:
    """MCP 工具适配器。

    将 MCP 协议的工具适配为 Tool 组件。
    负责 MCP 工具的参数转换、执行和结果格式化。

    Attributes:
        server_name: MCP 服务器名称
        mcp_tool: MCP 工具对象
        manager: 负责调用该工具的 MCP 管理器
        name: 适配后的工具名称
        description: 工具描述

    Examples:
        >>> adapter = MCPToolAdapter(
        ...     server_name="weather_server",
        ...     mcp_tool=mcp_tool
        ... )
        >>> schema = adapter.get_schema()
        >>> result = await adapter.execute({"city": "Beijing"})
    """

    def __init__(
        self,
        server_name: str,
        mcp_tool: "mcp.types.Tool",
        manager: "MCPManager | None" = None,
    ) -> None:
        """初始化 MCP 工具适配器。

        Args:
            server_name: MCP 服务器名称
            mcp_tool: MCP 工具对象
            manager: 负责底层调用的 MCP 管理器，未提供时使用全局管理器
        """
        self.server_name = server_name
        self.mcp_tool = mcp_tool
        self.manager = manager
        normalized_server_name = _normalize_tool_name_part(server_name)
        normalized_tool_name = _normalize_tool_name_part(mcp_tool.name)
        self.name = f"mcp-{normalized_server_name}-{normalized_tool_name}"
        self.description = mcp_tool.description or f"MCP tool from {server_name}"

        logger.debug(f"创建 MCP 工具适配器: {self.name}")

    def get_schema(self) -> dict[str, Any]:
        """获取 Tool Schema。

        将 MCP 工具的 inputSchema 转换为 OpenAI Tool Calling 格式。

        Returns:
            dict[str, Any]: OpenAI Tool Calling 格式的 schema

        Examples:
            >>> schema = adapter.get_schema()
            >>> {
            ...     "type": "function",
            ...     "function": {
            ...         "name": "mcp-weather-server-get-weather",
            ...         "description": "获取天气信息",
            ...         "parameters": {...}
            ...     }
            ... }
        """
        input_schema = self.mcp_tool.inputSchema or {}

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": input_schema,
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 MCP 工具调用。

        Args:
            arguments: 工具调用参数

        Returns:
            dict[str, Any]: 标准化的工具执行结果

        Examples:
            >>> result = await adapter.execute({"city": "Beijing"})
            >>> {
            ...     "type": "mcp_result",
            ...     "content": "今天天气晴",
            ...     "tool_name": "mcp-weather-server-get-weather",
            ...     "is_error": False
            ... }
        """
        try:
            logger.debug(
                f"执行 MCP 工具: {self.name} | "
                f"服务器: {self.server_name} | 参数: {arguments}"
            )

            manager = self.manager
            if manager is None:
                from src.core.managers.tool_manager import get_mcp_manager

                manager = get_mcp_manager()

            result = await manager.call_tool(
                server_name=self.server_name,
                tool_name=self.mcp_tool.name,
                arguments=arguments
            )

            if result:
                return self._format_result(result)

            return {
                "type": "mcp_result",
                "content": "",
                "tool_name": self.name,
                "is_error": False,
            }

        except Exception as e:
            logger.error(f"MCP 工具执行失败: {self.name} | 错误: {e}")
            return {
                "type": "error",
                "content": f"MCP 工具调用失败: {e!s}",
                "tool_name": self.name,
                "is_error": True,
            }

    def _format_result(self, result: "mcp.types.CallToolResult") -> dict[str, Any]:
        """格式化 MCP 工具执行结果为标准格式。

        Args:
            result: MCP CallToolResult 对象

        Returns:
            dict[str, Any]: 标准化的工具执行结果
        """
        if not result.content:
            return {
                "type": "mcp_result",
                "content": "",
                "tool_name": self.name,
                "is_error": False,
            }

        # 提取所有内容
        content_parts = []
        for content_item in result.content:
            content_type = getattr(content_item, "type", None)

            if content_type == "text":
                text = getattr(content_item, "text", "")
                content_parts.append(text)
            elif content_type == "image":
                data = getattr(content_item, "data", b"")
                content_parts.append(f"[Image data: {len(data)} bytes]")
            elif content_type == "audio":
                data = getattr(content_item, "data", b"")
                content_parts.append(f"[Audio data: {len(data)} bytes]")
            else:
                text = getattr(content_item, "text", None)
                if text is not None:
                    content_parts.append(text)
                else:
                    data = getattr(content_item, "data", None)
                    if data is not None:
                        data_len = len(data) if hasattr(data, "__len__") else "unknown"
                        content_parts.append(f"[Binary data: {data_len} bytes]")
                    else:
                        content_parts.append(str(content_item))

        return {
            "type": "mcp_result",
            "content": "\n".join(content_parts),
            "tool_name": self.name,
            "is_error": getattr(result, "isError", False),
        }


async def load_mcp_tools(server_name: str) -> list[MCPToolAdapter]:
    """加载 MCP 服务器的所有工具并转换为适配器。

    Args:
        server_name: MCP 服务器名称

    Returns:
        list[MCPToolAdapter]: 工具适配器列表

    Examples:
        >>> adapters = await load_mcp_tools("weather_server")
        >>> len(adapters)
        5
    """
    logger.info(f"开始加载 MCP 工具: {server_name}")

    try:
        # 获取 MCP 管理器并查找对应会话
        from src.core.managers.tool_manager import get_mcp_manager

        manager = get_mcp_manager()
        # 注意：这里直接访问了 manager 的受保护成员 _sessions 以获取底层会话
        session = manager._sessions.get(server_name)

        if not session:
            logger.warning(f"MCP 服务器未连接: {server_name}")
            return []

        # 获取工具列表
        result = await session.list_tools()
        tools = result.tools

        adapters = []
        for mcp_tool in tools:
            try:
                adapter = MCPToolAdapter(server_name, mcp_tool, manager)
                adapters.append(adapter)
                logger.debug(f" 加载工具: {adapter.name}")
            except Exception as e:
                logger.error(f" 创建工具适配器失败: {mcp_tool.name} | 错误: {e}")
                continue

        logger.info(f"MCP 工具加载完成: 服务器 {server_name}, 成功 {len(adapters)} 个")
        return adapters

    except Exception as e:
        logger.error(f"加载 MCP 工具失败: {server_name} | 错误: {e}")
        return []
