"""Collection 管理器。

本模块提供 Collection 管理器，负责 Collection 组件的注册、发现和解包。
Collection 是 LLMUsable 的集合体，可包含多个 Action、Tool 或嵌套的 Collection。
"""

from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger
from src.kernel.llm.payload.tooling import LLMUsable

from src.core.components.registry import get_global_registry
from src.core.components.types import ComponentType, parse_signature

if TYPE_CHECKING:
    from src.core.components.base.collection import BaseCollection

logger = get_logger("collection_manager")


class CollectionManager:
    """Collection 管理器。

    负责管理所有 Collection 组件，提供查询、解包和聚合接口。

    Attributes:
        _unpacked_cache: 解包后的组件缓存

    Examples:
        >>> manager = CollectionManager()
        >>> contents = manager.get_collection_contents("my_plugin:collection:my_collection")
        >>> all_components = manager.unpack_collection("my_plugin:collection:my_collection")
    """

    def __init__(self) -> None:
        """初始化 Collection 管理器。"""
        self._unpacked_cache: dict[str, list[type[LLMUsable]]] = {}
        logger.info("Collection 管理器初始化完成")

    def get_all_collections(self) -> dict[str, type["BaseCollection"]]:
        """获取所有已注册的 Collection 组件。"""
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.COLLECTION)

    def get_collections_for_plugin(self, plugin_name: str) -> dict[str, type["BaseCollection"]]:
        """获取指定插件的所有 Collection 组件。"""
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.COLLECTION)

    def get_collection_class(self, signature: str) -> type["BaseCollection"] | None:
        """通过签名获取 Collection 类。"""
        registry = get_global_registry()
        return registry.get(signature)

    async def get_collection_contents(self, signature: str) -> list[str]:
        """获取 Collection 内部包含的组件签名列表。"""
        collection_cls = self.get_collection_class(signature)
        if not collection_cls:
            raise ValueError(f"Collection 类未找到: {signature}")

        # 解析签名获取 plugin_name
        sig_info = parse_signature(signature)
        from src.core.managers.plugin_manager import get_plugin_manager
        plugin_manager = get_plugin_manager()
        plugin = plugin_manager.get_plugin(sig_info["plugin_name"])

        if not plugin:
            logger.warning(f"Plugin 未找到: {sig_info['plugin_name']}")
            return []

        # 创建临时实例
        collection_instance = collection_cls(plugin=plugin)
        contents = await collection_instance.get_contents()

        logger.debug(f"Collection '{signature}' 包含 {len(contents)} 个组件")
        return contents

    async def unpack_collection(
        self,
        signature: str,
        recursive: bool = False,
    ) -> list[type[LLMUsable]]:
        """解包 Collection，获取所有包含的 LLMUsable 组件类。

        Args:
            signature: Collection 组件签名
            recursive: 是否递归解包嵌套的 Collection

        Returns:
            list[type[LLMUsable]]: LLMUsable 组件类列表
        """
        # 检查缓存
        if signature in self._unpacked_cache:
            return self._unpacked_cache[signature]

        result: list[type[LLMUsable]] = []
        contents = await self.get_collection_contents(signature)

        registry = get_global_registry()

        for item_signature in contents:
            item_cls = registry.get(item_signature)

            if not item_cls:
                logger.warning(f"Collection 中的组件未找到: {item_signature}")
                continue

            # 检查是否是 LLMUsable（Action、Tool、Collection）
            # 通过检查类的基类来判断
            from src.core.components.base.action import BaseAction
            from src.core.components.base.tool import BaseTool
            from src.core.components.base.collection import BaseCollection

            is_llmusable = False
            try:
                if (issubclass(item_cls, BaseAction) or
                    issubclass(item_cls, BaseTool) or
                    issubclass(item_cls, BaseCollection)):
                    is_llmusable = True
            except TypeError:
                pass

            if is_llmusable:
                # 如果是 Collection 且需要递归
                if recursive and hasattr(item_cls, 'collection_name'):
                    # 递归解包
                    nested_components = await self.unpack_collection(item_signature, recursive=True)
                    result.extend(nested_components)
                else:
                    result.append(item_cls)

        # 缓存结果
        self._unpacked_cache[signature] = result

        logger.debug(f"解包 Collection '{signature}': {len(result)} 个组件")
        return result

    async def aggregate_collections(
        self,
        signatures: list[str],
    ) -> list[type[LLMUsable]]:
        """聚合多个 Collection，去重后返回所有组件。"""
        seen = set()
        result: list[type[LLMUsable]] = []

        for signature in signatures:
            components = await self.unpack_collection(signature, recursive=True)

            for component_cls in components:
                # 使用签名作为唯一标识
                component_sig = self._get_component_signature(component_cls)
                if component_sig and component_sig not in seen:
                    seen.add(component_sig)
                    result.append(component_cls)

        logger.debug(f"聚合 {len(signatures)} 个 Collection: {len(result)} 个唯一组件")
        return result

    def get_collection_schema(self, signature: str) -> dict[str, Any] | None:
        """获取 Collection 的 Tool Schema。"""
        collection_cls = self.get_collection_class(signature)
        if not collection_cls:
            return None

        return collection_cls.to_schema()

    def clear_cache(self, signature: str | None = None) -> None:
        """清除解包缓存。"""
        if signature:
            self._unpacked_cache.pop(signature, None)
        else:
            self._unpacked_cache.clear()

    def _get_component_signature(self, component_cls: type) -> str | None:
        """获取组件类的签名。"""
        # 优先使用 __signature__ 属性
        if hasattr(component_cls, "__signature__"):
            return getattr(component_cls, "__signature__")  # type: ignore[attr-defined]

        # 从注册表反向查找
        registry = get_global_registry()
        all_components = registry.get_by_type(ComponentType.ACTION)
        all_components.update(registry.get_by_type(ComponentType.TOOL))
        all_components.update(registry.get_by_type(ComponentType.COLLECTION))

        for sig, cls in all_components.items():
            if cls is component_cls:
                return sig

        return None


# 全局 Collection 管理器实例
_global_collection_manager: CollectionManager | None = None


def get_collection_manager() -> CollectionManager:
    """获取全局 Collection 管理器实例。"""
    global _global_collection_manager
    if _global_collection_manager is None:
        _global_collection_manager = CollectionManager()
    return _global_collection_manager