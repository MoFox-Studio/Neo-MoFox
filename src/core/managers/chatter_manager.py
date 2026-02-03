"""Chatter 管理器。

本模块提供 Chatter 管理器，负责 Chatter 组件的注册、发现和生命周期管理。
Chatter 是 Bot 的智能核心，定义对话逻辑和 LLMUsable 过滤。
管理器维护 Chatter 组件的全局集合，并提供查询接口。
"""

from typing import TYPE_CHECKING

from src.kernel.logger import get_logger
from src.kernel.llm.payload.tooling import LLMUsable

from src.core.components.registry import get_global_registry
from src.core.components.types import ComponentType

if TYPE_CHECKING:
    from src.core.components.base.chatter import BaseChatter
    from src.core.models.message import Message


logger = get_logger("chatter_manager")


class ChatterManager:
    """Chatter 管理器。

    负责管理所有 Chatter 组件，提供查询和过滤接口。
    根据 stream_id 等条件过滤可用的 Chatter。

    Attributes:
        _active_chatters: 当前活跃的 Chatter 实例字典

    Examples:
        >>> manager = ChatterManager()
        >>> chatters = manager.get_all_chatters()
        >>> chatter = manager.get_chatter("my_plugin:chatter:my_chatter")
    """

    def __init__(self) -> None:
        """初始化 Chatter 管理器。"""
        self._active_chatters: dict[str, "BaseChatter"] = {}
        logger.info("Chatter 管理器初始化完成")

    def get_all_chatters(self) -> dict[str, type["BaseChatter"]]:
        """获取所有已注册的 Chatter 组件。

        Returns:
            dict[str, type[BaseChatter]]: 将签名映射到 Chatter 类的字典

        Examples:
            >>> chatters = manager.get_all_chatters()
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.CHATTER)

    def get_chatters_for_plugin(self, plugin_name: str) -> dict[str, type["BaseChatter"]]:
        """获取指定插件的所有 Chatter 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseChatter]]: 将签名映射到 Chatter 类的字典

        Examples:
            >>> chatters = manager.get_chatters_for_plugin("my_plugin")
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.CHATTER)

    def get_chatter_class(self, signature: str) -> type["BaseChatter"] | None:
        """通过签名获取 Chatter 类。

        Args:
            signature: Chatter 组件签名

        Returns:
            type[BaseChatter] | None: Chatter 类，如果未找到则返回 None

        Examples:
            >>> chatter_cls = manager.get_chatter_class("my_plugin:chatter:my_chatter")
        """
        registry = get_global_registry()
        return registry.get(signature)

    def get_active_chatters(self) -> dict[str, "BaseChatter"]:
        """获取当前活跃的 Chatter 实例。

        Returns:
            dict[str, BaseChatter]: 将 stream_id 映射到 Chatter 实例的字典

        Examples:
            >>> active = manager.get_active_chatters()
        """
        return self._active_chatters.copy()

    def register_active_chatter(self, stream_id: str, chatter: "BaseChatter") -> None:
        """注册活跃的 Chatter 实例。

        Args:
            stream_id: 聊天流 ID
            chatter: Chatter 实例

        Examples:
            >>> manager.register_active_chatter("stream_1", chatter_instance)
        """
        self._active_chatters[stream_id] = chatter
        logger.debug(f"注册活跃 Chatter: stream_id={stream_id}, chatter={chatter.chatter_name}")

    def unregister_active_chatter(self, stream_id: str) -> bool:
        """注销活跃的 Chatter 实例。

        Args:
            stream_id: 聊天流 ID

        Returns:
            bool: 是否成功注销

        Examples:
            >>> success = manager.unregister_active_chatter("stream_1")
        """
        if stream_id in self._active_chatters:
            del self._active_chatters[stream_id]
            logger.debug(f"注销活跃 Chatter: stream_id={stream_id}")
            return True
        return False

    def get_chatter_by_stream(self, stream_id: str) -> "BaseChatter | None":
        """获取指定聊天流的活跃 Chatter 实例。

        Args:
            stream_id: 聊天流 ID

        Returns:
            BaseChatter | None: Chatter 实例，如果不存在则返回 None

        Examples:
            >>> chatter = manager.get_chatter_by_stream("stream_1")
        """
        return self._active_chatters.get(stream_id)

# 全局 Chatter 管理器实例
_global_chatter_manager: ChatterManager | None = None


def get_chatter_manager() -> ChatterManager:
    """获取全局 Chatter 管理器实例。

    Returns:
        ChatterManager: 全局 Chatter 管理器单例

    Examples:
        >>> manager = get_chatter_manager()
        >>> chatters = manager.get_all_chatters()
    """
    global _global_chatter_manager
    if _global_chatter_manager is None:
        _global_chatter_manager = ChatterManager()
    return _global_chatter_manager
