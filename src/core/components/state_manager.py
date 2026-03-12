"""组件状态管理器。

本模块提供组件的状态管理，跟踪其生命周期状态和运行时数据。
支持线程安全操作，并提供管理组件状态的集中位置。
支持级联禁用：当禁用某个组件时，自动禁用依赖于它的所有组件。
"""

import asyncio
from typing import Any
from src.core.components.types import ComponentState
from src.core.components.registry import get_global_registry


class StateManager:
    """组件状态管理器。

    管理系统中所有组件的状态，包括生命周期状态跟踪和运行时数据存储。

    Attributes:
        _states: 将组件签名映射到其状态的字典
        _runtime_data: 用于运行时数据存储的嵌套字典
        _lock: 用于线程安全操作的异步锁

    Examples:
        >>> manager = StateManager()
        >>> manager.set_state("my_plugin:action:send_message", ComponentState.ACTIVE)
        >>> state = manager.get_state("my_plugin:action:send_message")
        >>> manager.set_runtime_data("my_plugin:action:send_message", "key", "value")
        >>> value = manager.get_runtime_data("my_plugin:action:send_message", "key")
    """

    def __init__(self) -> None:
        """初始化状态管理器。"""
        self._states: dict[str, ComponentState] = {}
        self._runtime_data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def set_state(self, signature: str, state: ComponentState) -> None:
        """设置组件的状态。

        Args:
            signature: 组件签名
            state: 要设置的新状态

        Examples:
            >>> manager.set_state("my_plugin:action:send", ComponentState.ACTIVE)
        """
        self._states[signature] = state

    def get_state(self, signature: str) -> ComponentState:
        """获取组件的状态。

        Args:
            signature: 组件签名

        Returns:
            ComponentState: 组件的当前状态。
                如果未找到组件则返回 ComponentState.UNLOADED。

        Examples:
            >>> state = manager.get_state("my_plugin:action:send")
            >>> <ComponentState.ACTIVE: 'active'>
        """
        return self._states.get(signature, ComponentState.UNLOADED)

    def get_all_states(self) -> dict[str, ComponentState]:
        """获取所有组件状态。

        Returns:
            dict[str, ComponentState]: 将签名映射到状态的字典

        Examples:
            >>> all_states = manager.get_all_states()
            >>> {'my_plugin:action:send': <ComponentState.ACTIVE>, ...}
        """
        return self._states.copy()

    def remove_state(self, signature: str) -> bool:
        """移除组件的状态。

        Args:
            signature: 组件签名

        Returns:
            bool: 如果状态已移除返回 True，如果未找到返回 False

        Examples:
            >>> manager.remove_state("my_plugin:action:send")
            True
        """
        if signature in self._states:
            del self._states[signature]
            return True
        return False

    def set_runtime_data(self, signature: str, key: str, value: Any) -> None:
        """为组件设置运行时数据。

        存储与组件关联的任意键值数据。可用于任何运行时信息需求。

        Args:
            signature: 组件签名
            key: 数据键
            value: 数据值

        Examples:
            >>> manager.set_runtime_data("my_plugin:action:send", "call_count", 42)
            >>> manager.set_runtime_data("my_plugin:action:send", "last_call", "2024-01-01")
        """
        if signature not in self._runtime_data:
            self._runtime_data[signature] = {}

        self._runtime_data[signature][key] = value

    def get_runtime_data(self, signature: str, key: str, default: Any = None) -> Any:
        """获取组件的运行时数据。

        Args:
            signature: 组件签名
            key: 数据键
            default: 如果键未找到时的默认值

        Returns:
            Any: 存储的值，如果未找到则返回默认值

        Examples:
            >>> count = manager.get_runtime_data("my_plugin:action:send", "call_count", 0)
            >>> 42
        """
        if signature not in self._runtime_data:
            return default

        return self._runtime_data[signature].get(key, default)

    def get_all_runtime_data(self, signature: str) -> dict[str, Any]:
        """获取组件的所有运行时数据。

        Args:
            signature: 组件签名

        Returns:
            dict[str, Any]: 组件的所有运行时数据字典

        Examples:
            >>> data = manager.get_all_runtime_data("my_plugin:action:send")
            >>> {'call_count': 42, 'last_call': '2024-01-01'}
        """
        return self._runtime_data.get(signature, {}).copy()

    def remove_runtime_data(self, signature: str, key: str | None = None) -> bool:
        """移除组件的运行时数据。

        Args:
            signature: 组件签名
            key: 要移除的特定键，或 None 以移除组件的所有数据

        Returns:
            bool: 如果数据已移除返回 True，如果未找到返回 False

        Examples:
            >>> # 移除特定键
            >>> manager.remove_runtime_data("my_plugin:action:send", "call_count")
            True

            >>> # 移除组件的所有数据
            >>> manager.remove_runtime_data("my_plugin:action:send")
            True
        """
        if signature not in self._runtime_data:
            return False

        if key is None:
            # 移除组件的所有数据
            del self._runtime_data[signature]
            return True

        if key in self._runtime_data[signature]:
            del self._runtime_data[signature][key]
            return True

        return False

    async def set_state_async(self, signature: str, state: ComponentState) -> None:
        """异步设置组件的状态。

        此方法是线程安全的，使用内部锁。

        Args:
            signature: 组件签名
            state: 要设置的新状态

        Examples:
            >>> await manager.set_state_async("my_plugin:action:send", ComponentState.ACTIVE)
        """
        async with self._lock:
            self._states[signature] = state

    async def get_state_async(self, signature: str) -> ComponentState:
        """异步获取组件的状态。

        此方法是线程安全的，使用内部锁。

        Args:
            signature: 组件签名

        Returns:
            ComponentState: 组件的当前状态

        Examples:
            >>> state = await manager.get_state_async("my_plugin:action:send")
        """
        async with self._lock:
            return self._states.get(signature, ComponentState.UNLOADED)

    def get_components_by_state(self, state: ComponentState) -> list[str]:
        """获取具有特定状态的所有组件。

        Args:
            state: 要过滤的状态

        Returns:
            list[str]: 具有给定状态的组件签名列表

        Examples:
            >>> from src.core.components.types import ComponentState
            >>> active = manager.get_components_by_state(ComponentState.ACTIVE)
            >>> ['my_plugin:action:send', 'other_plugin:tool:calc']
        """
        return [sig for sig, s in self._states.items() if s == state]

    async def disable_component_cascade(self, signature: str) -> list[str]:
        """级联禁用组件及其所有依赖者。

        当禁用某个组件时，自动禁用所有依赖于它的组件。
        使用 ComponentRegistry 的依赖图计算需要禁用的组件列表。

        Args:
            signature: 要禁用的组件签名

        Returns:
            list[str]: 实际被禁用的组件签名列表

        Examples:
            >>> disabled = await manager.disable_component_cascade("base_plugin:service:database")
            >>> ['dependent_plugin:action:query', 'base_plugin:service:database']
        """
        registry = get_global_registry()
        to_disable = registry.get_cascade_disable_list(signature)

        async with self._lock:
            for sig in to_disable:
                self._states[sig] = ComponentState.INACTIVE

        return to_disable

    async def enable_component_with_dependencies(self, signature: str) -> tuple[bool, list[str]]:
        """启用组件及其依赖项。

        启用组件时，先检查其所有依赖项是否可用。
        如果依赖项缺失或被禁用，则启用失败。

        Args:
            signature: 要启用的组件签名

        Returns:
            tuple[bool, list[str]]: (是否成功, 缺失或被禁用的依赖项列表)

        Examples:
            >>> success, missing = await manager.enable_component_with_dependencies(
            ...     "my_plugin:action:send"
            ... )
            >>> False, ["other_plugin:tool:database"]
        """
        registry = get_global_registry()
        dependencies = registry.get_dependencies(signature)

        # 检查所有依赖项是否可用
        missing_or_disabled = []
        for dep_sig in dependencies:
            dep_state = self._states.get(dep_sig, ComponentState.UNLOADED)
            if dep_state in (ComponentState.UNLOADED, ComponentState.INACTIVE):
                missing_or_disabled.append(dep_sig)

        if missing_or_disabled:
            return False, missing_or_disabled

        # 所有依赖项都可用，启用组件
        async with self._lock:
            self._states[signature] = ComponentState.ACTIVE

        return True, []

    def clear(self) -> None:
        """清除所有状态和运行时数据。

        移除所有存储的状态和运行时数据。主要用于测试目的。

        Examples:
            >>> manager.clear()
        """
        self._states.clear()
        self._runtime_data.clear()

    def __len__(self) -> int:
        """获取具有跟踪状态的组件数量。

        Returns:
            int: 具有状态的组件数量
        """
        return len(self._states)


# 全局状态管理器实例
_global_state_manager = StateManager()


def get_global_state_manager() -> StateManager:
    """获取全局状态管理器实例。

    Returns:
        StateManager: 全局状态管理器实例

    Examples:
        >>> manager = get_global_state_manager()
        >>> manager.set_state("my_plugin:action:send", ComponentState.ACTIVE)
    """
    return _global_state_manager
