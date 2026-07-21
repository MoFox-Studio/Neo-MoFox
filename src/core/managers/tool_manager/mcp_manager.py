"""MCP Manager implementation.

本模块提供 MCPManager 类，负责管理 MCP 服务器连接、工具发现、
server metadata 缓存和工具调用。
"""

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Coroutine

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from src.core.config.mcp_config import MCPConfig, is_mcp_server_defer_loading
from src.core.managers.tool_manager.mcp_adapter import MCPToolAdapter
from src.kernel.logger import get_logger

logger = get_logger("mcp_manager")


def _extract_configured_instructions(params: Any) -> str:
    """从 MCP 服务配置中提取手动 instructions。"""
    if not isinstance(params, dict):
        return ""

    for key in ("instructions", "instruction"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


@dataclass(frozen=True, slots=True)
class MCPServerMetadata:
    """已连接 MCP 服务器的元数据快照。"""

    server_name: str
    instructions: str
    server_label: str
    defer_loading: bool


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
        _tool_signatures: 动态注册的 MCP Tool 组件签名集合
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stack = AsyncExitStack()
        self._adapters: dict[str, MCPToolAdapter] = {}
        self._tool_signatures: set[str] = set()
        self._server_metadata: dict[str, MCPServerMetadata] = {}
        self._tool_classes_by_server: dict[str, list[type[Any]]] = {}
        logger.info("MCP 管理器初始化")

    async def initialize(self) -> None:
        """初始化 MCP 管理器。
        
        读取配置，建立连接，并自动发现工具。
        """
        if self._sessions or self._adapters or self._tool_signatures:
            await self.cleanup()

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
                    await self._run_connection_task(
                        name,
                        self.connect_stdio_server(name, command, args, env),
                    )
                else:
                    logger.error(f"MCP 服务器 {name} 配置缺少 command")

        # 连接 SSE 服务器
        if config.mcp.sse_servers:
            logger.info(f"开始连接 SSE MCP 服务器: {list(config.mcp.sse_servers.keys())}")
            for name, params in config.mcp.sse_servers.items():
                await self._run_connection_task(
                    name,
                    self.connect_sse_server_from_config(name, params),
                )

        # 连接 Streamable HTTP 服务器
        if config.mcp.streamable_http_servers:
            logger.info(
                "开始连接 Streamable HTTP MCP 服务器: "
                f"{list(config.mcp.streamable_http_servers.keys())}"
            )
            for name, params in config.mcp.streamable_http_servers.items():
                await self._run_connection_task(
                    name,
                    self.connect_streamable_http_server_from_config(name, params),
                )

    async def _run_connection_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, bool],
    ) -> bool:
        """在隔离任务中执行一次 MCP 连接。"""
        from src.kernel.concurrency import get_task_manager

        task_info = get_task_manager().create_task(
            coro,
            name=f"mcp_connect_{name}",
            daemon=True,
        )
        if task_info.task is None:
            logger.error(f"MCP 服务器连接任务创建失败: {name}")
            return False

        try:
            return bool(await task_info.task)
        except asyncio.CancelledError as e:
            logger.error(f"MCP 服务器连接任务被取消 {name}: {e}")
            return False
        except BaseExceptionGroup as e:
            logger.error(f"MCP 服务器连接任务失败 {name}: {e}")
            return False
        except Exception as e:
            logger.error(f"MCP 服务器连接任务失败 {name}: {e}")
            return False

    async def connect_stdio_server(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None) -> bool:
        """连接 Stdio MCP 服务器。"""
        try:
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**os.environ, **(env or {})}
            )
            
            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
            await self._connect_session(name, stdio_transport)
            return True
            
        except asyncio.CancelledError as e:
            logger.error(f"连接 MCP 服务器被取消 {name}: {e}")
            return False
        except BaseExceptionGroup as e:
            logger.error(f"连接 MCP 服务器失败 {name}: {e}")
            return False
        except Exception as e:
            logger.error(f"连接 MCP 服务器失败 {name}: {e}")
            return False

    async def connect_sse_server_from_config(
        self,
        name: str,
        params: str | dict[str, Any],
    ) -> bool:
        """根据配置连接 SSE MCP 服务器。"""
        if isinstance(params, str):
            return await self.connect_sse_server(name, params)

        url = params.get("url")
        if not isinstance(url, str) or not url:
            logger.error(f"SSE MCP 服务器 {name} 配置缺少 url")
            return False

        return await self.connect_sse_server(
            name=name,
            url=url,
            headers=params.get("headers"),
            timeout=float(params.get("timeout", 5)),
            sse_read_timeout=float(params.get("sse_read_timeout", 300)),
        )

    async def connect_sse_server(
        self,
        name: str,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: float = 5,
        sse_read_timeout: float = 300,
    ) -> bool:
        """连接 SSE MCP 服务器。"""
        try:
            sse_transport = await self._exit_stack.enter_async_context(
                sse_client(
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    sse_read_timeout=sse_read_timeout,
                )
            )
            await self._connect_session(name, sse_transport)
            return True
        except asyncio.CancelledError as e:
            logger.error(f"连接 SSE MCP 服务器被取消 {name}: {e}")
            return False
        except BaseExceptionGroup as e:
            logger.error(f"连接 SSE MCP 服务器失败 {name}: {e}")
            return False
        except Exception as e:
            logger.error(f"连接 SSE MCP 服务器失败 {name}: {e}")
            return False

    async def connect_streamable_http_server_from_config(
        self,
        name: str,
        params: str | dict[str, Any],
    ) -> bool:
        """根据配置连接 Streamable HTTP MCP 服务器。"""
        if isinstance(params, str):
            return await self.connect_streamable_http_server(name, params)

        url = params.get("url")
        if not isinstance(url, str) or not url:
            logger.error(f"Streamable HTTP MCP 服务器 {name} 配置缺少 url")
            return False

        return await self.connect_streamable_http_server(
            name=name,
            url=url,
            headers=params.get("headers"),
            timeout=float(params.get("timeout", 30)),
        )

    async def connect_streamable_http_server(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> bool:
        """连接 Streamable HTTP MCP 服务器。"""
        try:
            http_client: httpx.AsyncClient | None = None
            if headers or timeout:
                http_client = await self._exit_stack.enter_async_context(
                    httpx.AsyncClient(headers=headers, timeout=timeout)
                )

            http_transport = await self._exit_stack.enter_async_context(
                streamable_http_client(url, http_client=http_client)
            )
            await self._connect_session(name, http_transport)
            return True
        except asyncio.CancelledError as e:
            logger.error(f"连接 Streamable HTTP MCP 服务器被取消 {name}: {e}")
            return False
        except BaseExceptionGroup as e:
            logger.error(f"连接 Streamable HTTP MCP 服务器失败 {name}: {e}")
            return False
        except Exception as e:
            logger.error(f"连接 Streamable HTTP MCP 服务器失败 {name}: {e}")
            return False

    def _cache_server_metadata(self, name: str, initialize_result: Any) -> None:
        """缓存已连接 MCP 服务器的元数据。"""
        raw_instructions = getattr(initialize_result, "instructions", None)
        instructions = raw_instructions.strip() if isinstance(raw_instructions, str) else ""
        defer_loading = True

        try:
            from src.core.config import get_mcp_config

            config = get_mcp_config().mcp
            configured_params = (
                config.stdio_servers.get(name)
                or config.sse_servers.get(name)
                or config.streamable_http_servers.get(name)
            )
            configured_instructions = _extract_configured_instructions(configured_params)
            if configured_instructions:
                instructions = configured_instructions
            defer_loading = is_mcp_server_defer_loading(configured_params)
        except Exception:
            pass

        server_info = getattr(initialize_result, "serverInfo", None)
        server_label = name
        info_name = getattr(server_info, "name", None)
        info_version = getattr(server_info, "version", None)
        if isinstance(info_name, str) and info_name.strip():
            server_label = info_name.strip()
            if isinstance(info_version, str) and info_version.strip():
                server_label = f"{server_label} {info_version.strip()}"

        self._server_metadata[name] = MCPServerMetadata(
            server_name=name,
            instructions=instructions,
            server_label=server_label,
            defer_loading=defer_loading,
        )

    async def _connect_session(self, name: str, transport: tuple[Any, ...]) -> None:
        """从 MCP 传输对象创建会话并发现工具。"""
        read, write = transport[0], transport[1]
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))

        initialize_result = await session.initialize()
        self._sessions[name] = session
        self._cache_server_metadata(name, initialize_result)
        logger.info(f"已连接 MCP 服务器: {name}")

        await self._discover_tools(name, session)

    async def _discover_tools(self, server_name: str, session: ClientSession) -> None:
        """发现并注册工具。"""
        try:
            from src.core.components.registry import get_global_registry
            from src.core.components.base.tool import BaseTool
            from src.core.components.state_manager import get_global_state_manager
            from src.core.components.types import ComponentState, ComponentType

            result = await session.list_tools()
            registry = get_global_registry()
            state_manager = get_global_state_manager()
            
            for tool in result.tools:
                adapter = MCPToolAdapter(server_name, tool, self)
                self._adapters[adapter.name] = adapter
                logger.debug(f"发现 MCP 工具: {adapter.name}")
                
                # 动态创建 Tool 类
                # 使用闭包或类属性绑定 adapter
                
                class DynamicMCPTool(BaseTool):
                    """动态生成的 MCP 工具代理类"""
                    plugin_name = "mcp_provider"
                    name = adapter.name
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
                DynamicMCPTool.__name__ = f"MCPTool_{adapter.name}"
                
                # 注册到全局注册表
                # 签名格式: mcp_provider:tool:mcp-{server}-{tool}
                signature = f"mcp_provider:{ComponentType.TOOL.value}:{adapter.name}"
                DynamicMCPTool._plugin_ = "mcp_provider"
                DynamicMCPTool._signature_ = signature
                
                try:
                    registry.register(DynamicMCPTool, signature)
                    state_manager.set_state(signature, ComponentState.ACTIVE)
                    self._tool_signatures.add(signature)
                    self._tool_classes_by_server.setdefault(server_name, []).append(
                        DynamicMCPTool
                    )
                    logger.info(f"已动态注册 MCP 工具: {signature}")
                except ValueError as e:
                    logger.warning(f"注册 MCP 工具失败 ({signature}): {e}")

        except Exception as e:
            logger.error(f"从 {server_name} 获取工具列表失败: {e}")

    def get_connected_server_metadata(self) -> list[MCPServerMetadata]:
        """返回当前已连接 MCP 服务器的元数据列表。"""
        return [
            self._server_metadata[name]
            for name in sorted(self._sessions)
            if name in self._server_metadata
        ]

    def get_tool_classes_for_servers(
        self,
        server_names: list[str] | None = None,
    ) -> list[type[Any]]:
        """返回指定 MCP 服务器暴露出的动态工具类。"""
        selected_server_names = server_names or list(self._tool_classes_by_server)
        selected_tools: list[type[Any]] = []
        seen_classes: set[type[Any]] = set()

        for server_name in selected_server_names:
            for tool_cls in self._tool_classes_by_server.get(server_name, []):
                if tool_cls in seen_classes:
                    continue
                seen_classes.add(tool_cls)
                selected_tools.append(tool_cls)

        return selected_tools

    def get_deferred_tool_classes(self) -> list[type[Any]]:
        """返回配置为 defer_loading 的 MCP 动态工具类。"""
        deferred_server_names = [
            metadata.server_name
            for metadata in self.get_connected_server_metadata()
            if metadata.defer_loading
        ]
        return self.get_tool_classes_for_servers(deferred_server_names)

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用 MCP 工具 (底层调用)。"""
        session = self._sessions.get(server_name)
        if not session:
            raise RuntimeError(f"MCP 服务器未连接: {server_name}")
            
        return await session.call_tool(tool_name, arguments)

    async def cleanup(self) -> None:
        """清理资源。"""
        from src.core.components.registry import get_global_registry
        from src.core.components.state_manager import get_global_state_manager

        registry = get_global_registry()
        state_manager = get_global_state_manager()
        for signature in list(self._tool_signatures):
            registry.unregister(signature)
            state_manager.remove_state(signature)

        try:
            await self._exit_stack.aclose()
        except asyncio.CancelledError as e:
            logger.warning(f"MCP 管理器关闭连接时被取消，已忽略: {e}")
        except BaseExceptionGroup as e:
            logger.warning(f"MCP 管理器关闭连接时出现异常，已忽略: {e}")
        except Exception as e:
            logger.warning(f"MCP 管理器关闭连接时出现异常，已忽略: {e}")
        finally:
            self._exit_stack = AsyncExitStack()
            self._sessions.clear()
            self._adapters.clear()
            self._tool_signatures.clear()
            self._server_metadata.clear()
            self._tool_classes_by_server.clear()
        logger.info("MCP 管理器资源已清理")

# 全局单例
_mcp_manager = MCPManager()

def get_mcp_manager() -> MCPManager:
    """获取全局 MCP 管理器实例。"""
    return _mcp_manager
