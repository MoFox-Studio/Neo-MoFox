"""Routing helpers for transport APIs.

本模块提供 HTTP 路由功能，包括：
- HTTPServer: HTTP 服务器管理
- get_http_server: 获取全局 HTTP 服务器单例实例
"""

from src.core.transport.router.http_server import (
    HTTPServer,
    get_http_server,
)

__all__ = [
    "HTTPServer",
    "get_http_server",
]
