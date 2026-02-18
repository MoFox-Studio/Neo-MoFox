"""Collection 管理器。

本模块提供 Collection 管理器，负责 Collection 组件的注册、发现和解包。
Collection 是 LLMUsable 的集合体，可包含多个 Action、Tool 或嵌套的 Collection。
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

from src.kernel.logger import get_logger
from src.kernel.llm import LLMUsable
from src.kernel.concurrency import get_task_manager

from src.core.components.types import ComponentType, ComponentSignature, parse_signature
from src.core.components.registry import get_global_registry
from src.core.components.base.action import BaseAction
from src.core.components.base.tool import BaseTool
from src.core.components.base.collection import BaseCollection


if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin

logger = get_logger("collection_manager")


@lru_cache(maxsize=256)
def _cached_parse_signature(signature: str) -> ComponentSignature:
    """带缓存的签名解析，避免重复解析相同签名。"""
    return parse_signature(signature)


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
        # 注意：解包结果只与 collection 本身结构有关，和 stream_id 无关；但递归与否会影响结果
        self._unpacked_cache: dict[tuple[str, bool], list[type[LLMUsable]]] = {}

        # 记录“某个聊天流已解包的 collections”。
        # 门控关系（哪些组件被哪些 collection 约束）是全局静态的；
        # 但“解包”是按 stream_id 的运行时状态。
        self._unpacked_collections_by_stream: dict[str, set[str]] = {}
        # Collection 内容缓存：避免重复创建实例和调用 get_contents()
        self._contents_cache: dict[str, list[str]] = {}

        # 门控关系使用 set 存储，提升查找效率 O(1)
        self._gate_sets: dict[str, set[str]] = {}

    def _get_gate_set(self, component_signature: str) -> set[str]:
        """获取组件的门控 collection 集合，O(1) 查找。"""
        return self._gate_sets.get(component_signature, set())

    def _add_gate(self, component_signature: str, collection_signature: str) -> None:
        """添加门控关系，O(1) 操作。"""
        if component_signature not in self._gate_sets:
            self._gate_sets[component_signature] = set()
        self._gate_sets[component_signature].add(collection_signature)

    def _remove_gate(self, component_signature: str, collection_signature: str) -> bool:
        """移除某个 collection 的门控。

        Returns:
            bool: 移除后是否仍然被其他 collection 门控。
        """
        if component_signature in self._gate_sets:
            self._gate_sets[component_signature].discard(collection_signature)
            return len(self._gate_sets[component_signature]) > 0
        return False

    def _get_unpacked_collections(self, stream_id: str) -> set[str]:
        if stream_id not in self._unpacked_collections_by_stream:
            self._unpacked_collections_by_stream[stream_id] = set()
        return self._unpacked_collections_by_stream[stream_id]

    def is_component_available(self, component_signature: str, stream_id: str) -> bool:
        """判断组件在指定聊天流中是否“可用”。

        规则：
        - 若组件未被任何 collection 门控，则可用。
        - 若组件被若干 collections 门控，只要其中【任意一个】collection 在该 stream 中已解包，该组件即为可用。
        """
        gates = self._get_gate_set(component_signature)
        if not gates:
            return True
        unpacked = self._get_unpacked_collections(stream_id)
        return not gates.isdisjoint(unpacked)

    def repack(self, stream_id: str) -> None:
        """在一轮对话结束后，恢复该聊天流的“初始门控状态”。

        语义：清空该 stream 的解包记录，使所有被门控的内部组件重新变为不可用。
        """
        self._unpacked_collections_by_stream.pop(stream_id, None)

    def get_all_collections(self) -> dict[str, type["BaseCollection"]]:
        """获取所有已注册的 Collection 组件。"""
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.COLLECTION)

    def get_collections_for_plugin(
        self, plugin_name: str
    ) -> dict[str, type["BaseCollection"]]:
        """获取指定插件的所有 Collection 组件。"""
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.COLLECTION)

    def get_collection_class(self, signature: str) -> type["BaseCollection"] | None:
        """通过签名获取 Collection 类。"""
        registry = get_global_registry()
        return registry.get(signature)

    async def get_collection_contents(
        self,
        signature: str,
        plugin: BasePlugin | None = None,
        use_cache: bool = True,
    ) -> list[str]:
        """获取 Collection 内部包含的组件签名列表。

        Args:
            signature: Collection 组件签名
            plugin: 所属插件实例
            use_cache: 是否使用缓存（默认 True）

        Returns:
            list[str]: 包含的组件签名列表
        """
        # 检查缓存
        if use_cache and signature in self._contents_cache:
            return self._contents_cache[signature].copy()

        collection_cls = self.get_collection_class(signature)
        if not collection_cls:
            raise ValueError(f"Collection 类未找到: {signature}")

        # 使用缓存的签名解析
        sig_info = _cached_parse_signature(signature)
        if plugin is None:
            from src.core.managers import get_plugin_manager

            plugin_manager = get_plugin_manager()
            plugin = plugin_manager.get_plugin(sig_info["plugin_name"])

        if not plugin:
            logger.warning(f"Plugin 未找到: {sig_info['plugin_name']}")
            return []

        # 创建临时实例
        collection_instance = collection_cls(plugin=plugin)
        contents = await collection_instance.get_contents()

        # 存入缓存
        self._contents_cache[signature] = contents

        logger.debug(f"Collection '{signature}' 包含 {len(contents)} 个组件")
        return contents.copy()

    async def seal_collection_components(
        self,
        signature: str,
        plugin: BasePlugin | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """建立 collection → 内部组件 的“门控关系”。

        注意：门控只影响“某个 stream 中是否可用”，不再通过全局 ComponentState 改写为 INACTIVE。

        Returns:
            list[str]: 实际被门控的组件签名列表（不含 collection 本身）。
        """

        contents = await self.get_collection_contents(signature, plugin=plugin)
        registry = get_global_registry()

        gated: list[str] = []

        for item_signature in contents:
            parts = item_signature.split(":")
            if len(parts) != 3:
                continue
            item_type = parts[1]

            # 递归处理嵌套 collection
            if recursive and item_type == ComponentType.COLLECTION.value:
                gated.extend(
                    await self.seal_collection_components(
                        item_signature,
                        plugin=plugin,
                        recursive=True,
                    )
                )
                continue

            item_cls = registry.get(item_signature)
            if not item_cls:
                continue

            try:
                if not (
                    issubclass(item_cls, BaseAction) or issubclass(item_cls, BaseTool)
                ):
                    continue
            except TypeError:
                continue

            self._add_gate(item_signature, signature)
            gated.append(item_signature)

        if gated:
            logger.debug(f"已门控 Collection '{signature}' 内部组件: {len(gated)} 个")

        return gated

    async def unpack_collection(
        self,
        signature: str,
        stream_id: str,
        recursive: bool = False,
        plugin: BasePlugin | None = None,
    ) -> list[type[LLMUsable]]:
        """解包 Collection，获取所有包含的 LLMUsable 组件类。

        Args:
            signature: Collection 组件签名
            recursive: 是否递归解包嵌套的 Collection

        Returns:
            list[type[LLMUsable]]: LLMUsable 组件类列表
        """
        # 记录该 stream 已解包当前 collection（解包是按聊天流隔离的）
        self._get_unpacked_collections(stream_id).add(signature)

        # 检查缓存（缓存与 stream_id 无关，但要区分 recursive）
        cache_key = (signature, recursive)
        cached = self._unpacked_cache.get(cache_key)

        result: list[type[LLMUsable]]

        if cached is not None:
            result = cached
        else:
            result = []
            contents = await self.get_collection_contents(signature, plugin=plugin)

            registry = get_global_registry()

            # 检查是否是 LLMUsable（Action、Tool、Collection）
            for item_signature in contents:
                item_cls_any = registry.get(item_signature)

                if item_cls_any is None:
                    logger.warning(f"Collection 中的组件未找到: {item_signature}")
                    continue

                item_cls = cast(type, item_cls_any)

                is_llmusable = False
                is_collection = False
                try:
                    is_collection = issubclass(item_cls, BaseCollection)
                    if (
                        issubclass(item_cls, BaseAction)
                        or issubclass(item_cls, BaseTool)
                        or is_collection
                    ):
                        is_llmusable = True
                except TypeError:
                    is_llmusable = False

                if not is_llmusable:
                    continue

                # 如果是 Collection 且需要递归
                if recursive and is_collection:
                    nested_components = await self.unpack_collection(
                        item_signature,
                        recursive=True,
                        plugin=plugin,
                        stream_id=stream_id,
                    )
                    result.extend(nested_components)
                    continue

                result.append(cast(type[LLMUsable], item_cls))

            # 缓存结果
            self._unpacked_cache[cache_key] = result

        logger.debug(f"解包 Collection '{signature}': {len(result)} 个组件")
        return result

    async def aggregate_collections(
        self,
        signatures: list[str],
        stream_id: str,
    ) -> list[type[LLMUsable]]:
        """聚合多个 Collection，去重后返回所有组件。

        使用并行解包提升效率。
        """
        if not signatures:
            return []

        # 并行解包所有 Collection
        tasks = [
            self.unpack_collection(sig, recursive=True, stream_id=stream_id)
            for sig in signatures
        ]
        all_components_lists = await get_task_manager().gather(*tasks)

        seen: set[str] = set()
        result: list[type[LLMUsable]] = []

        for components in all_components_lists:
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
        """清除解包缓存和内容缓存。

        Args:
            signature: 指定签名则清除该 Collection 的缓存，None 则清除全部
        """
        if signature:
            # 清除该 signature 的全部递归形态缓存
            self._unpacked_cache.pop((signature, True), None)
            self._unpacked_cache.pop((signature, False), None)
            # 清除内容缓存
            self._contents_cache.pop(signature, None)
        else:
            self._unpacked_cache.clear()
            self._contents_cache.clear()

    def clear_contents_cache(self, signature: str | None = None) -> None:
        """仅清除内容缓存（用于 Collection 内容动态变化时）。"""
        if signature:
            self._contents_cache.pop(signature, None)
        else:
            self._contents_cache.clear()

    def _get_component_signature(self, component_cls: type) -> str | None:
        """获取组件类的签名。

        优化：优先使用 _signature_ 属性（O(1)），避免注册表遍历。
        """
        if issubclass(component_cls, BaseAction | BaseTool | BaseCollection):
            sig = component_cls.get_signature()  # type: ignore[attr-defined]
            if isinstance(sig, str) and sig:
                return sig

        # 最后才从注册表反向查找（O(n)）
        registry = get_global_registry()
        all_components = registry.get_by_type(ComponentType.ACTION)
        all_components.update(registry.get_by_type(ComponentType.TOOL))
        all_components.update(registry.get_by_type(ComponentType.COLLECTION))

        for found_sig, cls in all_components.items():
            if cls is component_cls:
                return found_sig

        return None


# 全局 Collection 管理器实例
_global_collection_manager: CollectionManager | None = None


def get_collection_manager() -> CollectionManager:
    """获取全局 Collection 管理器实例。"""
    global _global_collection_manager
    if _global_collection_manager is None:
        _global_collection_manager = CollectionManager()
    return _global_collection_manager
