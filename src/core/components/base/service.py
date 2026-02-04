"""服务组件基类。

本模块提供 BaseService 类，定义服务组件的基本行为。
Service 暴露特定功能供其他插件或组件调用。
可以实现 typing.Protocol 定义的接口标准（如 MemoryService, ConfigService 等）。
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


class BaseService:
    """服务组件基类。

    Service 暴露特定功能供其他插件或组件调用。
    提供插件间通信和功能复用的机制。

    服务可以实现 typing.Protocol 定义的接口标准，例如：
    - MemoryService: 内存管理服务
    - ConfigService: 配置管理服务
    - LogService: 日志服务

    外部调用者可以通过 Service Manager 获取 Service 组件的实例，
    然后直接调用服务方法，例如：

    >>> service = service_manager.get_service("my_memory")
    >>> await service.store("key", "value")

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        service_name: 服务名称
        service_description: 服务描述
        version: 服务版本

    Examples:
        >>> from src.core.models.protocols import MemoryService
        >>>
        >>> class MyMemoryService(BaseService):
        ...     # 实现 MemoryService 协议
        ...     service_name = "my_memory"
        ...     service_description = "我的内存服务"
        ...     version = "1.0.0"
        ...
        ...     async def store(self, key: str, value: Any) -> bool:
        ...         # 实现存储逻辑
        ...         return True
        ...
        ...     async def retrieve(self, key: str) -> Any | None:
        ...         # 实现检索逻辑
        ...         return None
    """

    # 所属插件名称（由 PluginManager 在注册时注入）
    plugin_name: str = "unknown_plugin"

    # 服务元数据
    service_name: str = ""
    service_description: str = ""
    version: str = "1.0.0"

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:service:database"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化服务组件。

        Args:
            plugin: 所属插件实例
        """
        self.plugin = plugin
    
    @classmethod
    def get_signature(cls) -> str | None:
        """获取动作组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:action:action_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = SendEmoji.get_signature()
            >>> "my_plugin:action:send_emoji"
        """
        return f"{cls.plugin_name}:service:{cls.service_name}" if cls.plugin_name != "unknown_plugin" else None
