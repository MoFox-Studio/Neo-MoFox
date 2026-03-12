"""用于组件发现和查找的组件注册表。

本模块提供系统中所有组件的集中注册表。支持组件注册、按签名查找、
依赖关系跟踪，以及按插件或组件类型查询。
"""

from src.core.components.types import (
    ComponentType,
    build_signature,
    parse_signature,
)


class ComponentRegistry:
    """用于组件发现和查找的组件注册表。

    注册表维护所有已注册组件的集合，允许按签名、插件名称或组件类型查找。
    同时还跟踪组件之间的依赖关系以支持依赖解析。

    Attributes:
        _components: 将签名映射到组件类的字典
        _dependencies: 将签名映射到其依赖项的字典
        _by_plugin: 用于快速基于插件查找的嵌套字典
        _by_type: 用于快速基于类型查找的嵌套字典

    Examples:
        >>> registry = ComponentRegistry()
        >>> registry.register(MyAction, "my_plugin:action:send_message")
        >>> action_cls = registry.get("my_plugin:action:send_message")
        >>> actions = registry.get_by_plugin("my_plugin")
    """

    def __init__(self) -> None:
        """初始化组件注册表。"""
        self._components: dict[str, type] = {}
        self._dependencies: dict[str, list[str]] = {}
        self._by_plugin: dict[str, dict[ComponentType, dict[str, type]]] = {}
        self._by_type: dict[ComponentType, dict[str, dict[str, type]]] = {}

    def register(
        self,
        component_cls: type,
        signature: str,
        dependencies: list[str] | None = None,
    ) -> bool:
        """注册组件类。

        使用签名和可选依赖项注册组件类。签名必须采用
        'plugin_name:component_type:component_name' 格式。

        Args:
            component_cls: 要注册的组件类
            signature: 组件签名字符串
            dependencies: 此组件依赖的可选组件签名列表

        Returns:
            bool: 如果注册成功返回 True，否则返回 False

        Raises:
            ValueError: 如果签名格式无效或组件已注册

        Examples:
            >>> registry.register(MyAction, "my_plugin:action:send_message")
            True
            >>> registry.register(MyTool, "my_plugin:tool:calculator",
            ...                  dependencies=["my_plugin:action:send_message"])
            True
        """
        # 解析并验证签名
        try:
            sig = parse_signature(signature)
        except ValueError as e:
            raise ValueError(f"无效的签名 '{signature}': {e}")

        # 检查重复注册
        if signature in self._components:
            raise ValueError(f"组件 '{signature}' 已经注册")

        # 注册组件
        self._components[signature] = component_cls

        # 存储依赖项
        if dependencies:
            self._dependencies[signature] = dependencies.copy()
        else:
            self._dependencies[signature] = []

        # 更新插件索引
        plugin_name = sig["plugin_name"]
        component_type = sig["component_type"]
        component_name = sig["component_name"]

        if plugin_name not in self._by_plugin:
            self._by_plugin[plugin_name] = {}

        if component_type not in self._by_plugin[plugin_name]:
            self._by_plugin[plugin_name][component_type] = {}

        self._by_plugin[plugin_name][component_type][component_name] = component_cls

        # 更新类型索引
        if component_type not in self._by_type:
            self._by_type[component_type] = {}

        if plugin_name not in self._by_type[component_type]:
            self._by_type[component_type][plugin_name] = {}

        self._by_type[component_type][plugin_name][component_name] = component_cls

        return True

    def get(self, signature: str) -> type | None:
        """通过签名获取组件类。

        Args:
            signature: 组件签名字符串

        Returns:
            type | None: 如果找到返回组件类，否则返回 None

        Examples:
            >>> action_cls = registry.get("my_plugin:action:send_message")
        """
        return self._components.get(signature)

    def get_by_plugin(self, plugin_name: str) -> dict[str, type]:
        """获取特定插件的所有组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type]: 将签名映射到组件类的字典

        Examples:
            >>> components = registry.get_by_plugin("my_plugin")
            >>> {'my_plugin:action:send_message': <class MyAction>, ...}
        """
        if plugin_name not in self._by_plugin:
            return {}

        result = {}
        for component_type, components in self._by_plugin[plugin_name].items():
            for component_name, component_cls in components.items():
                signature = build_signature(plugin_name, component_type, component_name)
                result[signature] = component_cls

        return result

    def get_by_type(self, component_type: ComponentType) -> dict[str, type]:
        """获取特定类型的所有组件。

        Args:
            component_type: 要检索的组件类型

        Returns:
            dict[str, type]: 将签名映射到组件类的字典

        Examples:
            >>> from src.core.components.types import ComponentType
            >>> actions = registry.get_by_type(ComponentType.ACTION)
        """
        if component_type not in self._by_type:
            return {}

        result = {}
        for plugin_name, components in self._by_type[component_type].items():
            for component_name, component_cls in components.items():
                signature = build_signature(plugin_name, component_type, component_name)
                result[signature] = component_cls

        return result

    def get_by_plugin_and_type(
        self, plugin_name: str, component_type: ComponentType
    ) -> dict[str, type]:
        """获取特定插件和类型的组件。

        Args:
            plugin_name: 插件名称
            component_type: 要检索的组件类型

        Returns:
            dict[str, type]: 将组件名称映射到组件类的字典

        Examples:
            >>> from src.core.components.types import ComponentType
            >>> actions = registry.get_by_plugin_and_type("my_plugin", ComponentType.ACTION)
        """
        if plugin_name not in self._by_plugin:
            return {}

        if component_type not in self._by_plugin[plugin_name]:
            return {}

        return self._by_plugin[plugin_name][component_type].copy()

    def check_dependencies(self, signature: str) -> bool:
        """检查组件的所有依赖项是否已注册。

        Args:
            signature: 要检查的组件签名

        Returns:
            bool: 如果所有依赖项都已注册返回 True，否则返回 False

        Examples:
            >>> if registry.check_dependencies("my_plugin:action:send_message"):
            ...     print("所有依赖项已满足")
        """
        if signature not in self._dependencies:
            return True

        for dep_signature in self._dependencies[signature]:
            if dep_signature not in self._components:
                return False

        return True

    def get_dependencies(self, signature: str) -> list[str]:
        """获取组件的依赖项。

        Args:
            signature: 组件签名

        Returns:
            list[str]: 依赖项签名列表

        Examples:
            >>> deps = registry.get_dependencies("my_plugin:action:send_message")
            >>> ['other_plugin:tool:calculator']
        """
        return self._dependencies.get(signature, []).copy()

    def get_dependents(self, signature: str) -> list[str]:
        """获取依赖于指定组件的所有组件（反向依赖）。

        当禁用某个组件时，需要禁用所有依赖于它的组件。

        Args:
            signature: 组件签名

        Returns:
            list[str]: 依赖于该组件的组件签名列表

        Examples:
            >>> dependents = registry.get_dependents("other_plugin:tool:calculator")
            >>> ['my_plugin:action:send_message', 'my_plugin:tool:converter']
        """
        dependents = []
        for comp_sig, deps in self._dependencies.items():
            if signature in deps:
                dependents.append(comp_sig)
        return dependents

    def get_cascade_disable_list(self, signature: str) -> list[str]:
        """获取级联禁用列表。

        当禁用某个组件时，需要递归禁用所有依赖于它的组件。
        返回按拓扑排序的禁用顺序（从叶子节点到根节点）。

        Args:
            signature: 要禁用的组件签名

        Returns:
            list[str]: 需要级联禁用的组件签名列表（包括原始组件）

        Examples:
            >>> to_disable = registry.get_cascade_disable_list("base_plugin:service:database")
            >>> ['dependent_plugin:action:query', 'base_plugin:service:database']
        """
        # 使用 DFS 获取所有依赖者
        to_disable = []
        visited = set()

        def dfs(sig: str) -> None:
            if sig in visited:
                return
            visited.add(sig)

            # 先递归处理依赖者
            for dependent in self.get_dependents(sig):
                dfs(dependent)

            # 最后添加自己（后序遍历，确保依赖者先被禁用）
            to_disable.append(sig)

        dfs(signature)
        return to_disable

    def unregister(self, signature: str) -> bool:
        """注销组件。

        从注册表中移除组件。注意：这不会检查其他组件是否依赖于此组件。

        Args:
            signature: 要注销的组件签名

        Returns:
            bool: 如果组件已注销返回 True，如果未找到返回 False

        Examples:
            >>> registry.unregister("my_plugin:action:send_message")
            True
        """
        if signature not in self._components:
            return False

        # 解析签名
        sig = parse_signature(signature)
        plugin_name = sig["plugin_name"]
        component_type = sig["component_type"]
        component_name = sig["component_name"]

        # 从主注册表中移除
        del self._components[signature]
        del self._dependencies[signature]

        # 从插件索引中移除
        if (
            plugin_name in self._by_plugin
            and component_type in self._by_plugin[plugin_name]
            and component_name in self._by_plugin[plugin_name][component_type]
        ):
            del self._by_plugin[plugin_name][component_type][component_name]

        # 从类型索引中移除
        if (
            component_type in self._by_type
            and plugin_name in self._by_type[component_type]
            and component_name in self._by_type[component_type][plugin_name]
        ):
            del self._by_type[component_type][plugin_name][component_name]

        return True

    def list_all(self) -> list[str]:
        """列出所有已注册的组件签名。

        Returns:
            list[str]: 所有组件签名的列表

        Examples:
            >>> all_components = registry.list_all()
            >>> ['my_plugin:action:send_message', 'other_plugin:tool:calc']
        """
        return list(self._components.keys())

    def clear(self) -> None:
        """清除所有已注册的组件。

        从注册表中移除所有组件。主要用于测试目的。

        Examples:
            >>> registry.clear()
        """
        self._components.clear()
        self._dependencies.clear()
        self._by_plugin.clear()
        self._by_type.clear()

    def __len__(self) -> int:
        """获取已注册组件的数量。

        Returns:
            int: 已注册组件的数量
        """
        return len(self._components)

    def __contains__(self, signature: str) -> bool:
        """检查组件是否已注册。

        Args:
            signature: 组件签名

        Returns:
            bool: 如果组件已注册返回 True，否则返回 False
        """
        return signature in self._components


# 全局注册表实例
_global_registry = ComponentRegistry()


def get_global_registry() -> ComponentRegistry:
    """获取全局组件注册表实例。

    Returns:
        ComponentRegistry: 全局注册表实例

    Examples:
        >>> registry = get_global_registry()
        >>> registry.register(MyAction, "my_plugin:action:send_message")
    """
    return _global_registry
