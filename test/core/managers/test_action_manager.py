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
    
    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """执行 Action。"""
        return {"success": True, "result": "Executed"}
    
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


class TestActionManagerGetActionsForChat:
    """测试根据聊天上下文过滤 Action 功能。"""
    
    def test_get_actions_for_chat_all_type(self) -> None:
        """测试获取 ALL 类型的 Action。"""
        manager = ActionManager()
        
        TestAction.supported_chat_types = [ChatType.ALL]
        actions = {
            "test_plugin:action:test": TestAction,
        }
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = actions
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_actions_for_chat(
                chat_type=ChatType.GROUP,
                chatter_name="",
                platform=""
            )
            
            # ALL 类型应该匹配所有聊天类型
            assert len(result) > 0
    
    def test_get_actions_for_chat_specific_type(self) -> None:
        """测试获取特定聊天类型的 Action。"""
        manager = ActionManager()
        
        TestAction.supported_chat_types = [ChatType.GROUP]
        actions = {
            "test_plugin:action:test": TestAction,
        }
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = actions
            mock_get_registry.return_value = mock_registry
            
            result_group = manager.get_actions_for_chat(
                chat_type=ChatType.GROUP,
                chatter_name="",
                platform=""
            )
            
            result_private = manager.get_actions_for_chat(
                chat_type=ChatType.PRIVATE,
                chatter_name="",
                platform=""
            )
            
            # 应该只匹配 GROUP 类型
            assert len(result_group) > 0
            assert len(result_private) == 0
    
    def test_get_actions_for_chat_empty(self) -> None:
        """测试无匹配 Action 时返回空列表。"""
        manager = ActionManager()
        
        with patch('src.core.managers.action_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_actions_for_chat(
                chat_type=ChatType.GROUP,
                chatter_name="",
                platform=""
            )
            
            assert result == []


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
        
        actions = [TestAction, TestAction]
        
        with patch.object(manager, 'get_action_schema') as mock_get_schema:
            mock_get_schema.side_effect = [
                {"name": "schema1"},
                {"name": "schema2"},
            ]
            
            result = manager.get_action_schemas(
                chat_type=ChatType.GROUP,
                chatter_name="",
                platform=""
            )
            
            assert len(result) == 2
    
    def test_get_action_schemas_empty(self) -> None:
        """测试无 Action 时返回空列表。"""
        manager = ActionManager()
        
        with patch.object(manager, 'get_actions_for_chat') as mock_get_actions:
            mock_get_actions.return_value = []
            
            result = manager.get_action_schemas(
                chat_type=ChatType.GROUP,
                chatter_name="",
                platform=""
            )
            
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
            mock_action_instance.execute.assert_called_once()
    
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
