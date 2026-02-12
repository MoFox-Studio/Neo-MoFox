"""插件根组件基类。

本模块提供 BasePlugin 类，作为所有插件的根组件。
插件是组件的容器，包含其他各种类型的组件。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.components.base.config import BaseConfig


class BasePlugin(ABC):
    """插件根组件。

    所有插件的基类，作为其他组件的容器。
    插件是组件系统的核心单位，每个插件包含多个子组件。

    Class Attributes:
        plugin_name: 插件名称（唯一标识符）
        plugin_description: 插件描述
        plugin_version: 插件版本
        dependent_components: 依赖的其他组件列表，格式：["plugin_name:component_type:component_name"]

    Examples:
        >>> from src.core.components.loader import register_plugin
        >>> from src.core.components.base.action import BaseAction
        >>>
        >>> @register_plugin
        ... class MyPlugin(BasePlugin):
        ...     plugin_name = "my_plugin"
        ...     plugin_description = "我的插件"
        ...     plugin_version = "1.0.0"
        ...
        ...     dependent_components: list[str] = []
        ...
        ...     def __init__(self, config: BaseConfig):
        ...         super().__init__(config)
        ...         self._components: dict[str, type] = {}
        ...         self._instances: dict[str, object] = {}
        ...
        ...     def get_components(self) -> list[type]:
        ...         return list(self._components.values())
    """

    # 插件元数据
    plugin_name: str = "unknown_plugin"
    plugin_description: str = "无描述"
    plugin_version: str = "1.0.0"

    configs: list["BaseConfig"] = []

    # 依赖的其他组件
    dependent_components: list[str] = []

    def __init__(self, config: "BaseConfig | None" = None) -> None:
        """初始化插件。

        Args:
            config: 插件配置实例，可选
        """
        self.config = config

    @abstractmethod
    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        Returns:
            list[type]: 插件内所有组件类的列表

        Examples:
            >>> components = plugin.get_components()
            >>> [<class MyAction>, <class MyTool>]
        """
        ...

    async def on_plugin_loaded(self) -> None:
        """插件加载时的钩子。

        子类可重写此方法以执行初始化逻辑。
        此方法在插件加载完成后被调用。

        Examples:
            >>> async def on_plugin_loaded(self) -> None:
            ...     print(f"插件 {self.plugin_name} 已加载")
        """
        pass

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时的钩子。

        子类可重写此方法以执行清理逻辑。
        此方法在插件卸载前被调用。

        Examples:
            >>> async def on_plugin_unloaded(self) -> None:
            ...     print(f"插件 {self.plugin_name} 即将卸载")
        """
        pass

    def __repr__(self) -> str:
        """返回插件的字符串表示。"""
        return f"<{self.__class__.__name__}(name={self.plugin_name}, version={self.plugin_version})>"
