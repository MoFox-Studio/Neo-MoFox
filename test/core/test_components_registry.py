"""测试 src.core.components.registry 模块。"""

import pytest

from src.core.components.registry import ComponentRegistry
from src.core.components.types import ComponentType


class MockComponent:
    """模拟组件类。"""
    pass


class AnotherMockComponent:
    """另一个模拟组件类。"""
    pass


class TestComponentRegistry:
    """测试 ComponentRegistry 类。"""

    def test_registry_initialization(self):
        """测试注册表初始化。"""
        registry = ComponentRegistry()
        assert registry._components == {}
        assert registry._dependencies == {}
        assert registry._by_plugin == {}
        assert registry._by_type == {}

    def test_register_component(self):
        """测试注册组件。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:send_message"

        result = registry.register(MockComponent, signature)

        assert result is True
        assert registry.get(signature) == MockComponent

    def test_register_with_dependencies(self):
        """测试注册带依赖的组件。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:action1"
        dependencies = ["my_plugin:tool:tool1"]

        result = registry.register(MockComponent, signature, dependencies)

        assert result is True
        assert signature in registry._dependencies
        assert registry._dependencies[signature] == dependencies

    def test_register_duplicate(self):
        """测试重复注册。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:send_message"

        registry.register(MockComponent, signature)

        with pytest.raises(ValueError, match="已经注册"):
            registry.register(AnotherMockComponent, signature)

    def test_register_invalid_signature(self):
        """测试无效签名。"""
        registry = ComponentRegistry()

        with pytest.raises(ValueError, match="无效的签名"):
            registry.register(MockComponent, "invalid_signature_format")

    def test_get_component(self):
        """测试获取组件。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:send_message"

        registry.register(MockComponent, signature)

        assert registry.get(signature) == MockComponent
        assert registry.get("nonexistent") is None

    def test_get_by_plugin(self):
        """测试按插件获取组件。"""
        registry = ComponentRegistry()

        # 注册同一插件的多个组件
        registry.register(MockComponent, "my_plugin:action:action1")
        registry.register(AnotherMockComponent, "my_plugin:tool:tool1")

        components = registry.get_by_plugin("my_plugin")

        assert len(components) == 2
        assert "my_plugin:action:action1" in components
        assert "my_plugin:tool:tool1" in components
        assert components["my_plugin:action:action1"] == MockComponent

    def test_get_by_plugin_empty(self):
        """测试获取不存在插件的组件。"""
        registry = ComponentRegistry()
        components = registry.get_by_plugin("nonexistent")
        assert components == {}

    def test_get_by_type(self):
        """测试按类型获取组件。"""
        registry = ComponentRegistry()

        # 注册不同插件的同类型组件
        registry.register(MockComponent, "plugin1:action:action1")
        registry.register(AnotherMockComponent, "plugin2:action:action2")

        components = registry.get_by_type(ComponentType.ACTION)

        assert len(components) == 2
        assert "plugin1:action:action1" in components
        assert "plugin2:action:action2" in components

    def test_get_by_plugin_and_type(self):
        """测试按插件和类型获取组件。"""
        registry = ComponentRegistry()

        # 注册多个组件
        registry.register(MockComponent, "plugin1:action:action1")
        registry.register(AnotherMockComponent, "plugin1:tool:tool1")
        registry.register(MockComponent, "plugin1:action:action2")

        components = registry.get_by_plugin_and_type("plugin1", ComponentType.ACTION)

        # 返回的是组件名称到组件类的映射
        assert len(components) == 2
        assert "action1" in components
        assert "action2" in components
        assert components["action1"] == MockComponent

    def test_get_by_plugin_and_type_empty(self):
        """测试获取不存在的插件/类型组合。"""
        registry = ComponentRegistry()
        components = registry.get_by_plugin_and_type("nonexistent", ComponentType.ACTION)
        assert components == {}

    def test_get_dependencies(self):
        """测试获取依赖。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:action1"
        dependencies = ["my_plugin:tool:tool1", "other_plugin:service:svc1"]

        registry.register(MockComponent, signature, dependencies)

        deps = registry.get_dependencies(signature)
        assert deps == dependencies

    def test_get_dependencies_no_deps(self):
        """测试获取没有依赖的组件。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:action1"

        registry.register(MockComponent, signature)

        deps = registry.get_dependencies(signature)
        assert deps == []

    def test_get_dependencies_nonexistent(self):
        """测试获取不存在组件的依赖。"""
        registry = ComponentRegistry()
        deps = registry.get_dependencies("nonexistent")
        assert deps == []

    def test_unregister(self):
        """测试注销组件。"""
        registry = ComponentRegistry()
        signature = "my_plugin:action:action1"

        registry.register(MockComponent, signature)
        assert registry.get(signature) == MockComponent

        registry.unregister(signature)
        assert registry.get(signature) is None

    def test_clear(self):
        """测试清空注册表。"""
        registry = ComponentRegistry()

        # 注册多个组件
        registry.register(MockComponent, "plugin1:action:action1")
        registry.register(AnotherMockComponent, "plugin2:tool:tool1")

        assert len(registry._components) == 2

        registry.clear()

        assert len(registry._components) == 0
        assert len(registry._dependencies) == 0
        assert len(registry._by_plugin) == 0
        assert len(registry._by_type) == 0

    def test_all_component_types(self):
        """测试所有组件类型。"""
        registry = ComponentRegistry()

        # 测试不同类型的组件
        types_to_test = [
            ComponentType.ACTION,
            ComponentType.AGENT,
            ComponentType.TOOL,
            ComponentType.ADAPTER,
            ComponentType.CHATTER,
            ComponentType.COMMAND,
            ComponentType.CONFIG,
            ComponentType.EVENT_HANDLER,
            ComponentType.SERVICE,
            ComponentType.ROUTER,
        ]

        for comp_type in types_to_test:
            signature = f"my_plugin:{comp_type.value}:component1"
            registry.register(MockComponent, signature)

        # 验证所有类型都已注册
        for comp_type in types_to_test:
            components = registry.get_by_type(comp_type)
            assert len(components) == 1


class TestRegistryWithRealComponents:
    """测试真实场景的注册表使用。"""

    def test_complex_dependency_graph(self):
        """测试复杂的依赖图。"""
        registry = ComponentRegistry()

        # 构建依赖图
        # action1 依赖 tool1
        # tool1 依赖 service1
        # action2 依赖 tool1 和 service2
        registry.register(MockComponent, "my_plugin:action:action1",
                         dependencies=["my_plugin:tool:tool1"])
        registry.register(AnotherMockComponent, "my_plugin:tool:tool1",
                         dependencies=["my_plugin:service:service1"])
        registry.register(MockComponent, "my_plugin:service:service1")
        registry.register(AnotherMockComponent, "my_plugin:action:action2",
                         dependencies=["my_plugin:tool:tool1", "my_plugin:service:service2"])
        registry.register(MockComponent, "my_plugin:service:service2")

        # 验证依赖
        assert len(registry.get_dependencies("my_plugin:action:action1")) == 1
        assert len(registry.get_dependencies("my_plugin:tool:tool1")) == 1
        assert len(registry.get_dependencies("my_plugin:action:action2")) == 2

    def test_multi_plugin_scenario(self):
        """测试多插件场景。"""
        registry = ComponentRegistry()

        # 注册多个插件的组件
        registry.register(MockComponent, "plugin1:action:action1")
        registry.register(AnotherMockComponent, "plugin1:tool:tool1")
        registry.register(MockComponent, "plugin2:action:action1")
        registry.register(AnotherMockComponent, "plugin2:tool:tool1")
        registry.register(MockComponent, "plugin3:service:service1")

        # 获取所有插件的组件
        plugin1_components = registry.get_by_plugin("plugin1")
        plugin2_components = registry.get_by_plugin("plugin2")
        plugin3_components = registry.get_by_plugin("plugin3")

        assert len(plugin1_components) == 2
        assert len(plugin2_components) == 2
        assert len(plugin3_components) == 1

    def test_same_component_name_different_plugins(self):
        """测试不同插件中相同名称的组件。"""
        registry = ComponentRegistry()

        # 不同插件可以有相同名称的组件
        registry.register(MockComponent, "plugin1:action:send")
        registry.register(AnotherMockComponent, "plugin2:action:send")

        assert registry.get("plugin1:action:send") == MockComponent
        assert registry.get("plugin2:action:send") == AnotherMockComponent
