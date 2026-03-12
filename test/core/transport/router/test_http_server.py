"""HTTPServer 单元测试。

测试 HTTP 服务器的启动、停止、路由挂载等功能。
"""

import asyncio
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from src.core.transport.router.http_server import (
    HTTPServer,
    get_http_server,
)


class TestHTTPServer:
    """HTTP 服务器测试类。"""

    @pytest.fixture
    def server(self):
        """创建测试用的 HTTP 服务器实例。"""
        # 使用不同的端口避免冲突
        server = HTTPServer(host="127.0.0.1", port=8888)
        yield server
        # 清理：确保服务器停止
        if server.is_running():
            asyncio.create_task(server.stop())

    @pytest.mark.asyncio
    async def test_server_init(self, server):
        """测试服务器初始化。"""
        assert server.host == "127.0.0.1"
        assert server.port == 8888
        assert not server.is_running()
        assert isinstance(server.app, FastAPI)

    @pytest.mark.asyncio
    async def test_server_start_stop(self, server):
        """测试服务器启动和停止。"""
        # 启动服务器
        await server.start()
        assert server.is_running()

        # 等待服务器完全启动
        await asyncio.sleep(0.5)

        # 验证服务器可以响应
        async with AsyncClient(base_url=server.get_base_url()) as client:
            # 默认会返回 404，但说明服务器在运行
            response = await client.get("/")
            assert response.status_code in [404, 200]

        # 停止服务器
        await server.stop()
        assert not server.is_running()

    @pytest.mark.asyncio
    async def test_mount_sub_app(self, server):
        """测试挂载子应用。"""
        # 创建子应用
        sub_app = FastAPI()

        @sub_app.get("/test")
        async def test_endpoint():
            return {"message": "test"}

        # 挂载子应用
        server.app.mount("/api/v1", sub_app, name="test_api")

        # 启动服务器
        await server.start()
        await asyncio.sleep(0.5)

        # 测试子应用端点
        async with AsyncClient(base_url=server.get_base_url()) as client:
            response = await client.get("/api/v1/test")
            assert response.status_code == 200
            assert response.json() == {"message": "test"}

        await server.stop()

    @pytest.mark.asyncio
    async def test_get_app(self, server):
        """测试获取应用实例。"""
        app = server.app
        assert isinstance(app, FastAPI)

        # 添加端点到主应用
        @app.get("/health")
        async def health():
            return {"status": "ok"}

        await server.start()
        await asyncio.sleep(0.5)

        # 测试端点
        async with AsyncClient(base_url=server.get_base_url()) as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

        await server.stop()

    def test_get_base_url(self, server):
        """测试获取基础 URL。"""
        url = server.get_base_url()
        assert url == "http://127.0.0.1:8888"

    @pytest.mark.asyncio
    async def test_multiple_start_error(self, server):
        """测试重复启动服务器会抛出错误。"""
        await server.start()
        await asyncio.sleep(0.5)

        with pytest.raises(RuntimeError, match="服务器已经在运行中"):
            await server.start()

        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_not_running(self, server):
        """测试停止未运行的服务器不会报错。"""
        # 不应该抛出异常
        await server.stop()

    def test_get_openapi_schema(self, server):
        """测试获取 OpenAPI schema。"""
        schema = server.get_openapi_schema()
        assert isinstance(schema, dict)
        assert "openapi" in schema
        assert "info" in schema


class TestGlobalServer:
    """全局服务器实例测试类。"""

    def teardown_method(self):
        """清理全局服务器实例。"""
        import src.core.transport.router.http_server as module
        module._global_http_server = None

    def test_get_http_server_create(self):
        """测试获取全局服务器（自动创建）。"""
        server = get_http_server(host="127.0.0.1", port=9000)
        assert server is not None
        assert isinstance(server, HTTPServer)
        assert server.host == "127.0.0.1"
        assert server.port == 9000

        # 再次获取应该返回同一个实例（单例模式）
        server2 = get_http_server()
        assert server2 is server


@pytest.mark.asyncio
async def test_concurrent_requests():
    """测试并发请求处理。"""
    server = HTTPServer(host="127.0.0.1", port=8889)

    @server.app.get("/slow")
    async def slow_endpoint():
        await asyncio.sleep(0.1)
        return {"message": "slow"}

    await server.start()
    await asyncio.sleep(0.5)

    # 发送多个并发请求
    async with AsyncClient(base_url=server.get_base_url()) as client:
        tasks = [client.get("/slow") for _ in range(10)]
        responses = await asyncio.gather(*tasks)

        # 所有请求都应该成功
        for response in responses:
            assert response.status_code == 200
            assert response.json() == {"message": "slow"}

    await server.stop()
