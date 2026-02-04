"""MCP Manager implementation.

本模块提供 MCPManager 类，负责管理 MCP 服务器连接、工具发现和调用。
"""

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.kernel.logger import get_logger
from src.core.config.mcp_config import MCPConfig
from src.core.managers.config_manager import ConfigManager
from src.core.managers.tool_manager.mcp_adapter import MCPToolAdapter

logger = get_logger("mcp_manager")


class MCPManager:
    """MCP 管理器。

    负责：
    1. 根据配置初始化 MCP 客户端连接 (Stdio/SSE)。
    2. 发现并注册 MCP 工具。
    3. 管理客户端会话生命周期。
    4. 提供工具调用的统一入口。
    
    Attributes:
        _sessions: 活跃的客户端会话 {server_name: session}
        _exit_stack: 用于管理上下文管理器的栈 (AsyncExitStack)
        _adapters: 对应的工具适配器 {tool_name: adapter}
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stack = AsyncExitStack()
        self._adapters: dict[str, MCPToolAdapter] = {}
        logger.info("MCP 管理器初始化")

    async def initialize(self) -> None:
        """初始化 MCP 管理器。
        
        读取配置，建立连接，并自动发现工具。
        """
        try:
            from src.core.config import get_mcp_config
            config = get_mcp_config()
        except Exception:
            logger.warning("MCP 配置尚未初始化，尝试使用默认配置")
            config = MCPConfig()
            
        if not config.mcp.enabled:
            logger.info("MCP 功能未启用")
            return

        # 连接 Stdio 服务器
        if config.mcp.stdio_servers:
            logger.info(f"开始连接 Stdio MCP 服务器: {list(config.mcp.stdio_servers.keys())}")
            for name, params in config.mcp.stdio_servers.items():
                command = params.get("command")
                args = params.get("args", [])
                env = params.get("env")
                
                if command:
                    await self.connect_stdio_server(name, command, args, env)
                else:
                    logger.error(f"MCP 服务器 {name} 配置缺少 command")

        # 连接 SSE 服务器 (暂未实现)
        if config.mcp.sse_servers:
            logger.warning("SSE MCP 服务器连接暂未实现")

    async def connect_stdio_server(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None) -> bool:
        """连接 Stdio MCP 服务器。"""
        try:
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**os.environ, **(env or {})}
            )
            
            # 使用 exit_stack 管理上下文
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
            read, write = stdio_transport
            
            session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            
            await session.initialize()
            self._sessions[name] = session
            logger.info(f"已连接 MCP 服务器: {name}")
            
            # 自动发现工具
            await self._discover_tools(name, session)
            return True
            
        except Exception as e:
            logger.error(f"连接 MCP 服务器失败 {name}: {e}")
            return False

    async def _discover_tools(self, server_name: str, session: ClientSession) -> None:
        """发现并注册工具。"""
        try:
            from src.core.components.registry import get_global_registry
            from src.core.components.base.tool import BaseTool
            from src.core.components.types import ComponentType

            result = await session.list_tools()
            registry = get_global_registry()
            
            for tool in result.tools:
                adapter = MCPToolAdapter(server_name, tool)
                self._adapters[adapter.tool_name] = adapter
                logger.debug(f"发现 MCP 工具: {adapter.tool_name}")
                
                # 动态创建 Tool 类
                # 使用闭包或类属性绑定 adapter
                
                class DynamicMCPTool(BaseTool):
                    """动态生成的 MCP 工具代理类"""
                    plugin_name = "mcp_provider"
                    tool_name = adapter.tool_name
                    tool_description = adapter.description
                    
                    # 绑定特定的 adapter 实例
                    _adapter = adapter

                    async def execute(self, **kwargs: Any) -> tuple[bool, str | dict[str, Any]]:
                        # 委托给 Adapter 执行
                        result = await self._adapter.execute(kwargs)
                        
                        is_error = result.get("is_error", False)
                        content = result.get("content", "")
                        
                        return not is_error, content

                    @classmethod
                    def to_schema(cls) -> dict[str, Any]:
                        return cls._adapter.get_schema()

                # 设置类名
                DynamicMCPTool.__name__ = f"MCPTool_{adapter.tool_name}"
                
                # 注册到全局注册表
                # 签名格式: mcp_provider:tool:mcp_{server}_{tool}
                signature = f"mcp_provider:{ComponentType.TOOL.value}:{adapter.tool_name}"
                
                try:
                    registry.register(DynamicMCPTool, signature)
                    logger.info(f"已动态注册 MCP 工具: {signature}")
                except ValueError as e:
                    logger.warning(f"注册 MCP 工具失败 ({signature}): {e}")

        except Exception as e:
            logger.error(f"从 {server_name} 获取工具列表失败: {e}")

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用 MCP 工具 (底层调用)。"""
        session = self._sessions.get(server_name)
        if not session:
            raise RuntimeError(f"MCP 服务器未连接: {server_name}")
            
        return await session.call_tool(tool_name, arguments)

    async def cleanup(self) -> None:
        """清理资源。"""
        await self._exit_stack.aclose()
        self._sessions.clear()
        self._adapters.clear()
        logger.info("MCP 管理器资源已清理")

# 全局单例
_mcp_manager = MCPManager()

def get_mcp_manager() -> MCPManager:
    return _mcp_manager
