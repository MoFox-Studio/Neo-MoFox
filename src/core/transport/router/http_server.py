"""HTTP 服务器管理。

本模块提供 HTTPServer 类，负责启动和管理 FastAPI 服务器。
支持动态挂载路由、配置端口和地址。
"""

import asyncio
from typing import Any

import uvicorn
from fastapi import FastAPI

from src.kernel.logger import get_logger, COLOR
from src.kernel.concurrency import get_task_manager

logger = get_logger("http_server", display="HTTP服务器", color=COLOR.CYAN)


class HTTPServer:
    """HTTP 服务器管理器。

    负责创建和管理 FastAPI 主应用，支持动态挂载子应用。
    使用 Uvicorn 作为 ASGI 服务器。
    通过 get_http_server() 获取全局单例实例。

    Attributes:
        host: 服务器监听地址
        port: 服务器监听端口
        app: FastAPI 主应用（直接访问以挂载路由或添加端点）
        server: Uvicorn 服务器实例
        _running: 服务器运行状态

    Examples:
        >>> server = get_http_server()
        >>> await server.start()
        >>> # 挂载子应用
        >>> server.app.mount("/api/v1", sub_app)
        >>> # 添加路由
        >>> @server.app.get("/health")
        >>> async def health():
        ...     return {"status": "ok"}
        >>> # 停止服务器
        >>> await server.stop()
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        title: str = "MoFox HTTP API",
        description: str = "Neo-MoFox HTTP API Server",
    ) -> None:
        """初始化 HTTP 服务器。

        Args:
            host: 监听地址
            port: 监听端口
            title: API 标题
            description: API 描述
        """
        self.host = host
        self.port = port

        # 创建主应用
        self.app: FastAPI = FastAPI(
            title=title,
            description=description,
        )

        self.server: uvicorn.Server | None = None
        self._running: bool = False
        self._server_task: asyncio.Task | None = None
        self._state_lock = asyncio.Lock()
        self._startup_timeout = 5.0

    async def start(self) -> None:
        """启动服务器。

        启动 Uvicorn 服务器并在后台运行。

        Raises:
            RuntimeError: 如果服务器已经在运行

        Examples:
            >>> await server.start()
        """
        async with self._state_lock:
            if self._running:
                raise RuntimeError("服务器已经在运行中")

            # 配置 Uvicorn
            config = uvicorn.Config(
                app=self.app,
                host=self.host,
                port=self.port,
                log_level="error",
            )

            self.server = uvicorn.Server(config)
            self._server_task = (
                get_task_manager().create_task(self.server.serve(), daemon=True).task
            )

            try:
                await self._wait_until_started()
            except BaseException:
                await self._cleanup_failed_start()
                raise

            self._running = True

        logger.info(f"HTTP 服务器已启动: http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """停止服务器。

        优雅地关闭服务器并清理资源。

        Examples:
            >>> await server.stop()
        """
        async with self._state_lock:
            if self.server is None and self._server_task is None:
                logger.warning("服务器未运行")
                return

            self._running = False

            if self.server is not None:
                self.server.should_exit = True

            if self._server_task is not None:
                try:
                    await asyncio.wait_for(self._server_task, timeout=self._startup_timeout)
                except asyncio.CancelledError:
                    logger.warning("服务器停止等待被取消，已忽略")
                    self._server_task.cancel()
                except asyncio.TimeoutError:
                    logger.warning("服务器停止超时，强制取消")
                    if self.server is not None:
                        self.server.force_exit = True
                    self._server_task.cancel()
                    try:
                        await self._server_task
                    except asyncio.CancelledError:
                        pass
                finally:
                    self._reset_state()
            else:
                self._reset_state()

        logger.info("HTTP 服务器已停止")

    def is_running(self) -> bool:
        """检查服务器是否正在运行。

        Returns:
            bool: 服务器运行状态

        Examples:
            >>> if server.is_running():
            ...     print("服务器正在运行")
        """
        return self._running

    def get_base_url(self) -> str:
        """获取服务器基础 URL。

        Returns:
            str: 基础 URL

        Examples:
            >>> url = server.get_base_url()
            >>> "http://127.0.0.1:8000"
        """
        return f"http://{self.host}:{self.port}"

    def get_openapi_schema(self) -> dict[str, Any]:
        """获取完整的 OpenAPI schema。

        Returns:
            dict[str, Any]: OpenAPI schema

        Examples:
            >>> schema = server.get_openapi_schema()
        """
        return self.app.openapi()

    async def _wait_until_started(self) -> None:
        """等待 Uvicorn 完成 startup，避免启动/停止竞态。"""
        if self.server is None or self._server_task is None:
            raise RuntimeError("HTTP 服务器尚未创建")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._startup_timeout

        while True:
            if getattr(self.server, "started", False):
                return

            if self._server_task.done():
                await self._consume_server_task_result()
                raise RuntimeError("HTTP 服务器在启动完成前提前退出")

            if loop.time() >= deadline:
                raise TimeoutError("HTTP 服务器启动超时")

            await asyncio.sleep(0.01)

    async def _cleanup_failed_start(self) -> None:
        """清理启动失败时残留的服务状态。"""
        if self.server is not None:
            self.server.should_exit = True

        if self._server_task is not None and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        self._reset_state()

    async def _consume_server_task_result(self) -> None:
        """消费后台任务结果，保留原始异常栈。"""
        if self._server_task is None:
            return

        await self._server_task

    def _reset_state(self) -> None:
        """重置运行时状态。"""
        self.server = None
        self._server_task = None
        self._running = False


# 全局服务器实例
_global_http_server: HTTPServer | None = None


def get_http_server(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> HTTPServer:
    """获取全局 HTTP 服务器单例实例。

    采用单例模式，首次调用时创建服务器实例。

    Args:
        host: 服务器监听地址（仅在首次创建时使用）
        port: 服务器监听端口（仅在首次创建时使用）

    Returns:
        HTTPServer: 全局服务器实例

    Examples:
        >>> # 获取或创建服务器
        >>> server = get_http_server()
        >>> # 挂载路由
        >>> server.app.mount("/api/v1/router", router_app)
        >>> # 添加路由
        >>> @server.app.get("/health")
        >>> async def health():
        ...     return {"status": "ok"}
    """
    global _global_http_server

    if _global_http_server is None:
        _global_http_server = HTTPServer(host=host, port=port)

    return _global_http_server
