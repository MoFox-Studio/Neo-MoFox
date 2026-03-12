"""测试 src.core.components 模块。"""

from unittest.mock import MagicMock

import pytest

from src.core.components import BasePlugin


class TestBasePlugin:
    """测试 BasePlugin 类。"""

    def test_plugin_initialization(self):
        """测试插件初始化。"""
        # 创建一个具体的插件实现
        class ConcretePlugin(BasePlugin):
            plugin_name = "test_plugin"
            plugin_description = "Test plugin description"
            plugin_version = "1.0.0"

            def get_components(self):
                return []

        plugin = ConcretePlugin(config=None)
        assert plugin.plugin_name == "test_plugin"
        assert plugin.plugin_description == "Test plugin description"
        assert plugin.plugin_version == "1.0.0"
        assert plugin.config is None

    def test_plugin_with_config(self, mock_plugin):
        """测试带配置的插件初始化。"""
        mock_config = MagicMock()
        mock_config.data = {"test": "value"}

        class ConcretePlugin(BasePlugin):
            plugin_name = "config_plugin"
            plugin_description = "Plugin with config"
            plugin_version = "2.0.0"

            def __init__(self, config):
                super().__init__(config)
                self.test_value = config.data["test"] if config else None

            def get_components(self):
                return []

        plugin = ConcretePlugin(config=mock_config)
        assert plugin.config == mock_config
        assert plugin.test_value == "value"

    def test_get_components_abstract_method(self):
        """测试 get_components 抽象方法。"""
        # 尝试直接实例化 BasePlugin 应该失败
        with pytest.raises(TypeError):
            BasePlugin(config=None)

    def test_on_plugin_loaded_hook(self):
        """测试 on_plugin_loaded 钩子。"""
        class ConcretePlugin(BasePlugin):
            plugin_name = "hook_test_plugin"
            plugin_description = "Test hook plugin"
            plugin_version = "1.0.0"

            def __init__(self, config=None):
                super().__init__(config)
                self.loaded_called = False

            def get_components(self):
                return []

            async def on_plugin_loaded(self):
                self.loaded_called = True
                await super().on_plugin_loaded()

        import asyncio

        plugin = ConcretePlugin()
        asyncio.run(plugin.on_plugin_loaded())
        assert plugin.loaded_called is True

    def test_on_plugin_unloaded_hook(self):
        """测试 on_plugin_unloaded 钩子。"""
        class ConcretePlugin(BasePlugin):
            plugin_name = "unload_hook_plugin"
            plugin_description = "Test unload hook"
            plugin_version = "1.0.0"

            def __init__(self, config=None):
                super().__init__(config)
                self.unloaded_called = False

            def get_components(self):
                return []

            async def on_plugin_unloaded(self):
                self.unloaded_called = True
                await super().on_plugin_unloaded()

        import asyncio

        plugin = ConcretePlugin()
        asyncio.run(plugin.on_plugin_unloaded())
        assert plugin.unloaded_called is True

    def test_dependent_components(self):
        """测试依赖组件属性。"""
        class ConcretePlugin(BasePlugin):
            plugin_name = "dependent_plugin"
            plugin_description = "Plugin with dependencies"
            plugin_version = "1.0.0"
            dependent_components = ["other_plugin:tool:calculator", "another_plugin:action:send_msg"]

            def get_components(self):
                return []

        plugin = ConcretePlugin()
        assert len(plugin.dependent_components) == 2
        assert "other_plugin:tool:calculator" in plugin.dependent_components

    def test_repr(self):
        """测试 __repr__ 方法。"""
        class ConcretePlugin(BasePlugin):
            plugin_name = "repr_plugin"
            plugin_description = "Test repr"
            plugin_version = "2.5.0"

            def get_components(self):
                return []

        plugin = ConcretePlugin()
        repr_str = repr(plugin)
        assert "ConcretePlugin" in repr_str
        assert "name=repr_plugin" in repr_str
        assert "version=2.5.0" in repr_str

    def test_default_hooks_do_nothing(self):
        """测试默认钩子方法不执行任何操作。"""
        class ConcretePlugin(BasePlugin):
            plugin_name = "default_hook_plugin"
            plugin_description = "Test default hooks"
            plugin_version = "1.0.0"

            def get_components(self):
                return []

        import asyncio

        plugin = ConcretePlugin()
        # 默认钩子应该不抛出异常
        asyncio.run(plugin.on_plugin_loaded())
        asyncio.run(plugin.on_plugin_unloaded())
