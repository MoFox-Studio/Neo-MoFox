"""ActionManager 的单元测试。

测试覆盖：
- 初始化和 schema 缓存
- 获取所有 Action
- 获取插件的 Action
- 根据聊天上下文过滤 Action
- Action 类查询
- Action schema 生成和缓存
- Action 执行
- Schema 缓存管理
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

from src.core.managers.action_manager import ActionManager
from src.core.components.base.action import BaseAction
from src.core.components.types import ComponentType, ChatType


# 测试用 Action 类
class TestAction(BaseAction):
    """测试 Action 类。"""
    
    signature = "test_plugin:action:test_action"
    description = "Test action"
    supported_chat_types = [ChatType.ALL]
    associated_types = ["text"]
    
    async def execute(self, **kwargs: Any) -> tuple[bool, Any]:
        """执行 Action。"""
        return True, "Executed"
    
    @classmethod
    def get_llm_schema(cls) -> dict[str, Any]:
        """获取 LLM schema。"""
        return {
            "name": "test_action",
            "description": "Test action",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }


class TestActionManagerInit:
    """测试 ActionManager 初始化。"""
    
    def test_init_empty_schema_cache(self) -> None:
        """验证初始化时 schema 缓存为空。"""
        manager = ActionManager()
        
        assert manager._schema_cache == {}
    
    def test_init_completes_successfully(self) -> None:
        """验证初始化成功完成。"""
        manager = ActionManager()
        
        assert isinstance(manager._schema_cache, dict)


class TestActionManagerGetAllActions:
    """测试获取所有 Action 功能。"""
    
    def test_get_all_actions_empty(self) -> None:
        """测试无 Action 时返回空字典。"""
        manager = ActionManager()
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_actions()
            
            assert result == {}
            mock_registry.get_by_type.assert_called_once_with(ComponentType.ACTION)
    
    def test_get_all_actions_multiple(self) -> None:
        """测试返回多个 Action。"""
        manager = ActionManager()
        
        actions = {
            "plugin1:action:action1": TestAction,
            "plugin2:action:action2": TestAction,
        }
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = actions
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_actions()
            
            assert result == actions
            assert len(result) == 2


class TestActionManagerGetActionsForPlugin:
    """测试获取插件 Action 功能。"""
    
    def test_get_actions_for_plugin_exists(self) -> None:
        """测试获取已存在插件的 Action。"""
        manager = ActionManager()
        
        actions = {
            "test_plugin:action:action1": TestAction,
            "test_plugin:action:action2": TestAction,
        }
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = actions
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_actions_for_plugin("test_plugin")
            
            assert result == actions
            mock_registry.get_by_plugin_and_type.assert_called_once_with(
                "test_plugin",
                ComponentType.ACTION
            )
    
    def test_get_actions_for_plugin_not_exists(self) -> None:
        """测试获取不存在插件的 Action 返回空字典。"""
        manager = ActionManager()
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_actions_for_plugin("non_existent_plugin")
            
            assert result == {}


class TestActionManagerFilterComponents:
    """测试 Action 组件筛选功能。"""

    @pytest.mark.asyncio
    async def test_filter_actions_empty_returns_empty(self) -> None:
        """传入空列表时直接返回空列表，不访问注册表/聊天流。"""
        manager = ActionManager()

        with patch(
            "src.core.managers.utils.filtering.filter_component_classes",
            new=AsyncMock(return_value=[]),
        ), patch("src.core.managers.get_stream_manager") as mock_sm:
            result = await manager.filter_actions([])

        assert result == []
        mock_sm.assert_not_called()

    @pytest.mark.asyncio
    async def test_filter_actions_delegates_to_common_filter(self) -> None:
        """filter_actions 应委托给统一入口并传入对应筛选事件。"""
        from src.core.components.types import EventType

        manager = ActionManager()
        component_classes: list[Any] = [TestAction]
        expected = [TestAction]

        with patch(
            "src.core.managers.utils.filtering.filter_component_classes",
            new=AsyncMock(return_value=expected),
        ) as mock_filter, patch.object(
            manager, "_apply_action_activation", new=AsyncMock(return_value=expected),
        ) as mock_activate, patch(
            "src.core.managers.get_stream_manager"
        ) as mock_sm:
            mock_stream = MagicMock()
            mock_stream.context = MagicMock()
            mock_stream.context.check_types.return_value = True
            mock_sm.return_value.get_or_create_stream = AsyncMock(return_value=mock_stream)

            result = await manager.filter_actions(
                component_classes,
                stream_id="stream_123",
                chatter_name="my_chatter",
                chat_type=ChatType.GROUP,
                platform="test_platform",
            )

        assert result == expected
        mock_filter.assert_awaited_once_with(
            component_classes,
            event_type=EventType.BEFORE_ACTION_FILTER,
            stream_id="stream_123",
            chatter_name="my_chatter",
            chatter_signature="",
            chat_type=ChatType.GROUP,
            platform="test_platform",
        )
        mock_activate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_filter_actions_publishes_event_before_filtering(self) -> None:
        """筛选前应发布 BEFORE_ACTION_FILTER 事件，事件可替换组件类。"""
        from src.core.components.types import EventType
        from src.kernel.event import EventDecision, get_event_bus

        manager = ActionManager()
        replacement = [TestAction]

        event_bus = get_event_bus()

        async def _handler(event_name: str, params: dict) -> tuple[EventDecision, dict]:
            params["component_classes"] = replacement
            return EventDecision.SUCCESS, params

        unsubscribe = event_bus.subscribe(EventType.BEFORE_ACTION_FILTER, _handler)
        try:
            with patch.object(
                manager,
                "_apply_action_activation",
                new=AsyncMock(return_value=replacement),
            ) as mock_activate, patch(
                "src.core.managers.get_stream_manager"
            ) as mock_sm:
                mock_stream = MagicMock()
                mock_stream.context = MagicMock()
                mock_stream.context.check_types.return_value = True
                mock_sm.return_value.get_or_create_stream = AsyncMock(
                    return_value=mock_stream
                )

                result = await manager.filter_actions(
                    [TestAction], stream_id="stream_123"
                )

            assert result == replacement
            assert mock_activate.await_args is not None
            assert mock_activate.await_args.args[0] == replacement
        finally:
            unsubscribe()

    @pytest.mark.asyncio
    async def test_apply_action_activation_removes_deactivated_action(self) -> None:
        """go_activate 返回 False 的 Action 应被剔除。"""

        class _DeactivatedAction(BaseAction):
            name = "deactivated_action"
            description = "deactivated"
            associated_types = ["text"]
            _signature_ = "plugin:action:deactivated_action"

            async def execute(self) -> tuple[bool, str]:
                return True, "ok"

            async def go_activate(self) -> bool:
                return False

        manager = ActionManager()

        mock_stream = MagicMock()
        mock_stream.stream_id = "stream_123"
        mock_stream.context = MagicMock()
        mock_stream.context.current_message = None
        mock_stream.context.check_types.return_value = True

        plugin = MagicMock()
        plugin.plugin_name = "plugin"

        with patch("src.core.managers.get_stream_manager") as mock_sm, patch(
            "src.core.managers.get_plugin_manager"
        ) as mock_pm:
            mock_sm.return_value.get_or_create_stream = AsyncMock(return_value=mock_stream)
            mock_pm.return_value.get_plugin.return_value = plugin

            result = await manager._apply_action_activation(
                [_DeactivatedAction], stream_id="stream_123"
            )

        assert _DeactivatedAction not in result

    @pytest.mark.asyncio
    async def test_filter_actions_does_not_fetch_from_stream_without_stream_id(
        self,
    ) -> None:
        """未提供 stream_id 时不获取聊天流。"""
        manager = ActionManager()

        with patch(
            "src.core.managers.utils.filtering.filter_component_classes",
            new=AsyncMock(return_value=[TestAction]),
        ) as mock_filter, patch("src.core.managers.get_stream_manager") as mock_sm:
            result = await manager.filter_actions([TestAction])

        assert result == [TestAction]
        mock_filter.assert_awaited_once()
        mock_sm.assert_not_called()


class TestActionManagerGetActionClass:
    """测试获取 Action 类功能。"""
    
    def test_get_action_class_exists(self) -> None:
        """测试获取已存在的 Action 类。"""
        manager = ActionManager()
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = TestAction
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_action_class("test_plugin:action:test_action")
            
            assert result == TestAction
            mock_registry.get.assert_called_once_with("test_plugin:action:test_action")
    
    def test_get_action_class_not_exists(self) -> None:
        """测试获取不存在的 Action 类返回 None。"""
        manager = ActionManager()
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = None
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_action_class("non_existent_action")
            
            assert result is None


class TestActionManagerGetActionSchema:
    """测试获取 Action schema 功能。"""
    
    def test_get_action_schema_from_cache(self) -> None:
        """测试从缓存获取 schema。"""
        manager = ActionManager()
        cached_schema = {"name": "cached", "description": "Cached schema"}
        manager._schema_cache["test_plugin:action:test"] = cached_schema
        
        result = manager.get_action_schema("test_plugin:action:test")
        
        assert result == cached_schema
    
    def test_get_action_schema_generate_new(self) -> None:
        """测试生成新的 schema 并缓存。"""
        manager = ActionManager()
        
        with patch.object(manager, 'get_action_class') as mock_get_class:
            mock_get_class.return_value = TestAction
            
            result = manager.get_action_schema("test_plugin:action:test_action")
            
            # 应该生成并缓存 schema
            assert result is not None
            assert isinstance(result, dict)
            assert "test_plugin:action:test_action" in manager._schema_cache
    
    def test_get_action_schema_not_found(self) -> None:
        """测试 Action 不存在时返回 None。"""
        manager = ActionManager()
        
        with patch.object(manager, 'get_action_class') as mock_get_class:
            mock_get_class.return_value = None
            
            result = manager.get_action_schema("non_existent_action")
            
            assert result is None


class TestActionManagerGetActionSchemas:
    """测试批量获取 Action schemas 功能。"""
    
    def test_get_action_schemas_multiple(self) -> None:
        """测试获取多个 Action 的 schemas。"""
        manager = ActionManager()
        
        class _FakeAction:
            @classmethod
            def to_schema(cls) -> dict[str, Any]:
                return {"name": cls.__name__}
        
        result = manager.get_action_schemas([_FakeAction, _FakeAction])
        
        assert len(result) == 2
        assert result[0]["name"] == "_FakeAction"
    
    def test_get_action_schemas_empty(self) -> None:
        """测试传入空列表时返回空列表。"""
        manager = ActionManager()
        
        result = manager.get_action_schemas([])
        
        assert result == []


class TestActionManagerExecuteAction:
    """测试 Action 执行功能。"""
    
    @pytest.mark.asyncio
    async def test_execute_action_success(self) -> None:
        """测试成功执行 Action。"""
        manager = ActionManager()
        
        mock_plugin = MagicMock()
        mock_message = MagicMock()
        mock_message.stream_id = "stream_123"
        mock_message.extra = {}
        
        with patch.object(manager, 'get_action_class') as mock_get_class, \
             patch('src.core.managers.action_manager.get_stream_manager') as mock_get_sm:
            
            mock_action_class = MagicMock()
            mock_action_instance = MagicMock()
            mock_action_instance.execute = AsyncMock(return_value=(True, "Success"))
            mock_execution = MagicMock()
            mock_execution.wait_done = AsyncMock(return_value=(True, "Success"))
            mock_action_instance._wrap_execute = MagicMock(return_value=mock_execution)
            mock_action_class.return_value = mock_action_instance
            mock_get_class.return_value = mock_action_class
            
            mock_sm = MagicMock()
            mock_sm.activate_stream = AsyncMock(return_value=MagicMock())
            mock_get_sm.return_value = mock_sm
            
            result = await manager.execute_action(
                signature="test_plugin:action:test",
                plugin=mock_plugin,
                message=mock_message,
                param1="value1"
            )
            
            assert result == (True, "Success")
            mock_action_instance._wrap_execute.assert_called_once_with(param1="value1")
    
    @pytest.mark.asyncio
    async def test_execute_action_not_found(self) -> None:
        """测试执行不存在的 Action。"""
        manager = ActionManager()
        
        mock_plugin = MagicMock()
        mock_message = MagicMock()
        mock_message.stream_id = "stream_123"
        mock_message.extra = {}
        
        with patch.object(manager, 'get_action_class') as mock_get_class:
            mock_get_class.return_value = None
            
            with pytest.raises(ValueError):
                await manager.execute_action(
                    signature="non_existent_action",
                    plugin=mock_plugin,
                    message=mock_message
                )


class TestActionManagerClearSchemaCache:
    """测试清除 schema 缓存功能。"""
    
    def test_clear_specific_schema(self) -> None:
        """测试清除特定 Action 的 schema。"""
        manager = ActionManager()
        manager._schema_cache["action1"] = {"schema": 1}
        manager._schema_cache["action2"] = {"schema": 2}
        
        manager.clear_schema_cache("action1")
        
        assert "action1" not in manager._schema_cache
        assert "action2" in manager._schema_cache
    
    def test_clear_all_schemas(self) -> None:
        """测试清除所有 schemas。"""
        manager = ActionManager()
        manager._schema_cache["action1"] = {"schema": 1}
        manager._schema_cache["action2"] = {"schema": 2}
        
        manager.clear_schema_cache(None)
        
        assert manager._schema_cache == {}


class TestActionManagerEdgeCases:
    """测试边界条件。"""
    
    def test_get_action_schema_empty_signature(self) -> None:
        """测试空签名获取 schema。"""
        manager = ActionManager()
        
        with patch.object(manager, 'get_action_class') as mock_get_class:
            mock_get_class.return_value = None
            
            result = manager.get_action_schema("")
            
            assert result is None
    
    @pytest.mark.asyncio
    async def test_execute_action_with_exception(self) -> None:
        """测试 Action 执行异常处理。"""
        manager = ActionManager()
        
        mock_plugin = MagicMock()
        mock_message = MagicMock()
        mock_message.stream_id = "stream_123"
        mock_message.extra = {}
        
        with patch.object(manager, 'get_action_class') as mock_get_class, \
             patch('src.core.managers.action_manager.get_stream_manager') as mock_get_sm:
            
            mock_action_class = MagicMock()
            mock_action_instance = MagicMock()
            mock_action_instance.execute = AsyncMock(side_effect=Exception("Test error"))
            mock_action_class.return_value = mock_action_instance
            mock_get_class.return_value = mock_action_class
            
            mock_sm = MagicMock()
            mock_sm.activate_stream = AsyncMock(return_value=MagicMock())
            mock_get_sm.return_value = mock_sm
            
            # 执行应该捕获异常
            with pytest.raises(Exception):
                await manager.execute_action(
                    signature="test_plugin:action:test",
                    plugin=mock_plugin,
                    message=mock_message
                )
