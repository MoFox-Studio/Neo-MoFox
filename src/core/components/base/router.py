"""路由组件基类。

本模块提供 BaseRouter 类，定义路由组件的基本行为。
Router 提供基于 FastAPI 的 HTTP 路由接口。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


class BaseRouter(ABC):
    """路由组件基类。

    Router 提供 HTTP API 接口，使用 FastAPI 实现。
    支持 CORS 配置和自定义路由路径。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        router_name: 路由名称
        router_description: 路由描述
        custom_route_path: 自定义路由路径（如 "/api/v1/myrouter"）
        cors_origins: CORS 允许的源列表（None 表示禁用 CORS）

    Examples:
        >>> class MyRouter(BaseRouter):
        ...     router_name = "my_router"
        ...     custom_route_path = "/api/v1/myrouter"
        ...     cors_origins = ["*"]
        ...
        ...     def register_endpoints(self) -> None:
        ...         @self.app.get("/hello")
        ...         async def hello():
        ...             return {"message": "Hello"}
    """
    _plugin_: str
    _signature_: str

    # 路由元数据
    router_name: str = ""
    router_description: str = ""

    custom_route_path: str | None = None
    cors_origins: list[str] | None = None

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:service:auth"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化路由组件。

        Args:
            plugin: 所属插件实例

        Raises:
            ImportError: 如果 FastAPI 未安装
        """
        self.plugin = plugin

        # 创建 FastAPI 应用
        self.app: FastAPI = FastAPI(
            title=self.router_name,
            description=self.router_description,
        )

        # 配置 CORS
        if self.cors_origins is not None:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=self.cors_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        # 注册端点
        self.register_endpoints()

    @classmethod
    def get_signature(cls) -> str | None:
        """获取动作组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:action:action_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = SendEmoji.get_signature()
            >>> "my_plugin:action:send_emoji"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.router_name:
            return f"{cls._plugin_}:router:{cls.router_name}"
        return None
    
    @abstractmethod
    def register_endpoints(self) -> None:
        """注册路由端点。

        子类应该在此方法中定义所有 HTTP 端点。

        Examples:
            >>> def register_endpoints(self) -> None:
            ...     @self.app.get("/items/{item_id}")
            ...     async def read_item(item_id: int):
            ...         return {"item_id": item_id}
            ...
            ...     @self.app.post("/items")
            ...     async def create_item(item: dict):
            ...         return {"item": item}
        """
        ...

    def get_route_path(self) -> str:
        """获取路由路径。

        返回自定义路径或默认路径。

        Returns:
            str: 路由路径

        Examples:
            >>> path = router.get_route_path()
            >>> "/api/v1/myrouter"
        """
        if self.custom_route_path:
            return self.custom_route_path

        # 默认路径：/router/{router_name}
        return f"/router/{self.router_name}"

    def get_app(self) -> FastAPI:
        """获取 FastAPI 应用实例。

        Returns:
            FastAPI: FastAPI 应用

        Examples:
            >>> app = router.get_app()
            >>> # 可以将 app 挂载到主应用
            >>> main_app.mount(router.get_route_path(), app)
        """
        return self.app

    async def startup(self) -> None:
        """路由启动钩子。

        在路由挂载后调用。

        Examples:
            >>> async def startup(self) -> None:
            ...     # 初始化资源
            ...     pass
        """
        pass

    async def shutdown(self) -> None:
        """路由关闭钩子。

        在路由卸载前调用。

        Examples:
            >>> async def shutdown(self) -> None:
            ...     # 清理资源
            ...     pass
        """
        pass

    def get_openapi_schema(self) -> dict[str, Any]:
        """获取 OpenAPI schema。

        Returns:
            dict[str, Any]: OpenAPI schema

        Examples:
            >>> schema = router.get_openapi_schema()
        """
        return self.app.openapi()
