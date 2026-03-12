"""测试 src.core.components.state_manager 模块。"""


from src.core.components.state_manager import StateManager
from src.core.components.types import ComponentState


class TestStateManager:
    """测试 StateManager 类。"""

    def test_state_manager_initialization(self):
        """测试状态管理器初始化。"""
        manager = StateManager()
        assert manager._states == {}
        assert manager._runtime_data == {}

    def test_set_state(self):
        """测试设置状态。"""
        manager = StateManager()
        signature = "my_plugin:action:send_message"

        manager.set_state(signature, ComponentState.ACTIVE)

        assert manager.get_state(signature) == ComponentState.ACTIVE

    def test_get_state_default(self):
        """测试获取未设置的状态（返回默认值）。"""
        manager = StateManager()
        signature = "nonexistent:component:name"

        state = manager.get_state(signature)

        assert state == ComponentState.UNLOADED

    def test_get_all_states(self):
        """测试获取所有状态。"""
        manager = StateManager()

        # 设置多个状态
        manager.set_state("plugin1:action:action1", ComponentState.ACTIVE)
        manager.set_state("plugin1:tool:tool1", ComponentState.LOADED)
        manager.set_state("plugin2:service:svc1", ComponentState.INACTIVE)

        all_states = manager.get_all_states()

        assert len(all_states) == 3
        assert all_states["plugin1:action:action1"] == ComponentState.ACTIVE
        assert all_states["plugin1:tool:tool1"] == ComponentState.LOADED
        assert all_states["plugin2:service:svc1"] == ComponentState.INACTIVE

    def test_remove_state(self):
        """测试移除状态。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        manager.set_state(signature, ComponentState.ACTIVE)
        assert manager.get_state(signature) == ComponentState.ACTIVE

        result = manager.remove_state(signature)

        assert result is True
        assert manager.get_state(signature) == ComponentState.UNLOADED

    def test_remove_state_nonexistent(self):
        """测试移除不存在的状态。"""
        manager = StateManager()
        result = manager.remove_state("nonexistent:component:name")
        assert result is False

    def test_set_runtime_data(self):
        """测试设置运行时数据。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        manager.set_runtime_data(signature, "call_count", 42)
        manager.set_runtime_data(signature, "last_call", "2024-01-01")

        assert manager.get_runtime_data(signature, "call_count") == 42
        assert manager.get_runtime_data(signature, "last_call") == "2024-01-01"

    def test_get_runtime_data_default(self):
        """测试获取不存在的运行时数据（返回默认值）。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        value = manager.get_runtime_data(signature, "nonexistent_key", "default_value")

        assert value == "default_value"

    def test_get_all_runtime_data(self):
        """测试获取所有运行时数据。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        manager.set_runtime_data(signature, "key1", "value1")
        manager.set_runtime_data(signature, "key2", 123)
        manager.set_runtime_data(signature, "key3", True)

        all_data = manager.get_all_runtime_data(signature)

        assert len(all_data) == 3
        assert all_data["key1"] == "value1"
        assert all_data["key2"] == 123
        assert all_data["key3"] is True

    def test_get_all_runtime_data_empty(self):
        """测试获取空运行时数据。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        all_data = manager.get_all_runtime_data(signature)

        assert all_data == {}

    def test_remove_runtime_data_key(self):
        """测试移除运行时数据的特定键。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        manager.set_runtime_data(signature, "key1", "value1")
        manager.set_runtime_data(signature, "key2", "value2")

        result = manager.remove_runtime_data(signature, "key1")

        assert result is True
        assert manager.get_runtime_data(signature, "key1") is None
        assert manager.get_runtime_data(signature, "key2") == "value2"

    def test_remove_runtime_data_all(self):
        """测试移除组件的所有运行时数据。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        manager.set_runtime_data(signature, "key1", "value1")
        manager.set_runtime_data(signature, "key2", "value2")

        result = manager.remove_runtime_data(signature)

        assert result is True
        assert manager.get_all_runtime_data(signature) == {}

    def test_remove_runtime_data_nonexistent(self):
        """测试移除不存在的运行时数据。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        # 移除不存在的键
        result1 = manager.remove_runtime_data(signature, "nonexistent_key")
        assert result1 is False

        # 移除不存在的组件
        result2 = manager.remove_runtime_data("nonexistent:component:name")
        assert result2 is False

    def test_set_state_async(self):
        """测试异步设置状态。"""
        import asyncio

        manager = StateManager()
        signature = "my_plugin:action:action1"

        async def set_and_get():
            await manager.set_state_async(signature, ComponentState.ACTIVE)
            return await manager.get_state_async(signature)

        state = asyncio.run(set_and_get())
        assert state == ComponentState.ACTIVE

    def test_multiple_components_data(self):
        """测试多个组件的数据管理。"""
        manager = StateManager()

        # 设置多个组件的状态和数据
        components = [
            "plugin1:action:action1",
            "plugin1:tool:tool1",
            "plugin2:service:service1",
            "plugin3:adapter:adapter1",
        ]

        for sig in components:
            manager.set_state(sig, ComponentState.ACTIVE)
            manager.set_runtime_data(sig, "initialized", True)

        # 验证每个组件
        for sig in components:
            assert manager.get_state(sig) == ComponentState.ACTIVE
            assert manager.get_runtime_data(sig, "initialized") is True

    def test_state_transitions(self):
        """测试状态转换。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        # 测试状态转换链
        transitions = [
            ComponentState.UNLOADED,
            ComponentState.LOADED,
            ComponentState.ACTIVE,
            ComponentState.INACTIVE,
            ComponentState.ERROR,
        ]

        for state in transitions:
            manager.set_state(signature, state)
            assert manager.get_state(signature) == state

    def test_complex_runtime_data(self):
        """测试复杂的运行时数据。"""
        manager = StateManager()
        signature = "my_plugin:action:action1"

        # 存储不同类型的数据
        complex_data = {
            "count": 42,
            "ratio": 3.14,
            "enabled": True,
            "list": [1, 2, 3],
            "nested": {"key": "value"},
        }

        for key, value in complex_data.items():
            manager.set_runtime_data(signature, key, value)

        # 验证数据
        for key, value in complex_data.items():
            assert manager.get_runtime_data(signature, key) == value
