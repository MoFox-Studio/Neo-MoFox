"""ChatterManager 的单元测试。

测试覆盖：
- 初始化
- 获取所有 Chatter
- 获取插件的 Chatter
- Chatter 类查询
- 活跃 Chatter 管理（注册、注销、查询）
- 为流获取或创建 Chatter
- Chatter 兼容性检查
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.core.managers.chatter_manager import ChatterManager
from src.core.components.base.chatter import BaseChatter
from src.core.components.types import ComponentType, ChatType


# 测试用 Chatter 类
class TestChatter(BaseChatter):
    """测试 Chatter 类。"""
    
    signature = "test_plugin:chatter:test_chatter"
    description = "Test chatter"
    supported_chat_types = [ChatType.ALL]
    
    async def process(self, message):
        """处理消息。"""
        return "Processed"


class TestChatterManagerInit:
    """测试 ChatterManager 初始化。"""
    
    def test_init_empty_active_chatters(self) -> None:
        """验证初始化时活跃 Chatter 为空。"""
        manager = ChatterManager()
        
        assert manager._active_chatters == {}
    
    def test_init_completes_successfully(self) -> None:
        """验证初始化成功完成。"""
        manager = ChatterManager()
        
        assert isinstance(manager._active_chatters, dict)


class TestChatterManagerGetAllChatters:
    """测试获取所有 Chatter 功能。"""
    
    def test_get_all_chatters_empty(self) -> None:
        """测试无 Chatter 时返回空字典。"""
        manager = ChatterManager()
        
        with patch('src.core.managers.chatter_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_chatters()
            
            assert result == {}
            mock_registry.get_by_type.assert_called_once_with(ComponentType.CHATTER)
    
    def test_get_all_chatters_multiple(self) -> None:
        """测试返回多个 Chatter。"""
        manager = ChatterManager()
        
        chatters = {
            "plugin1:chatter:chatter1": TestChatter,
            "plugin2:chatter:chatter2": TestChatter,
        }
        
        with patch('src.core.managers.chatter_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = chatters
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_chatters()
            
            assert result == chatters
            assert len(result) == 2


class TestChatterManagerGetChattersForPlugin:
    """测试获取插件 Chatter 功能。"""
    
    def test_get_chatters_for_plugin_exists(self) -> None:
        """测试获取已存在插件的 Chatter。"""
        manager = ChatterManager()
        
        chatters = {
            "test_plugin:chatter:chatter1": TestChatter,
            "test_plugin:chatter:chatter2": TestChatter,
        }
        
        with patch('src.core.managers.chatter_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = chatters
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_chatters_for_plugin("test_plugin")
            
            assert result == chatters
            mock_registry.get_by_plugin_and_type.assert_called_once_with(
                "test_plugin",
                ComponentType.CHATTER
            )
    
    def test_get_chatters_for_plugin_not_exists(self) -> None:
        """测试获取不存在插件的 Chatter 返回空字典。"""
        manager = ChatterManager()
        
        with patch('src.core.managers.chatter_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_chatters_for_plugin("non_existent_plugin")
            
            assert result == {}


class TestChatterManagerGetChatterClass:
    """测试获取 Chatter 类功能。"""
    
    def test_get_chatter_class_exists(self) -> None:
        """测试获取已存在的 Chatter 类。"""
        manager = ChatterManager()
        
        with patch('src.core.managers.chatter_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = TestChatter
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_chatter_class("test_plugin:chatter:test_chatter")
            
            assert result == TestChatter
            mock_registry.get.assert_called_once_with("test_plugin:chatter:test_chatter")
    
    def test_get_chatter_class_not_exists(self) -> None:
        """测试获取不存在的 Chatter 类返回 None。"""
        manager = ChatterManager()
        
        with patch('src.core.managers.chatter_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = None
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_chatter_class("non_existent_chatter")
            
            assert result is None


class TestChatterManagerActiveChatterManagement:
    """测试活跃 Chatter 管理功能。"""
    
    def test_register_active_chatter(self) -> None:
        """测试注册活跃 Chatter。"""
        manager = ChatterManager()
        mock_chatter = MagicMock(spec=TestChatter)
        
        manager.register_active_chatter("stream_123", mock_chatter)
        
        assert "stream_123" in manager._active_chatters
        assert manager._active_chatters["stream_123"] == mock_chatter
    
    def test_unregister_active_chatter_exists(self) -> None:
        """测试注销已存在的活跃 Chatter。"""
        manager = ChatterManager()
        mock_chatter = MagicMock(spec=TestChatter)
        manager._active_chatters["stream_123"] = mock_chatter
        
        result = manager.unregister_active_chatter("stream_123")
        
        assert result is True
        assert "stream_123" not in manager._active_chatters
    
    def test_unregister_active_chatter_not_exists(self) -> None:
        """测试注销不存在的活跃 Chatter。"""
        manager = ChatterManager()
        
        result = manager.unregister_active_chatter("non_existent_stream")
        
        assert result is False
    
    def test_get_active_chatters_empty(self) -> None:
        """测试无活跃 Chatter 时返回空字典。"""
        manager = ChatterManager()
        
        result = manager.get_active_chatters()
        
        assert result == {}
    
    def test_get_active_chatters_multiple(self) -> None:
        """测试返回多个活跃 Chatter。"""
        manager = ChatterManager()
        mock_chatter1 = MagicMock(spec=TestChatter)
        mock_chatter2 = MagicMock(spec=TestChatter)
        
        manager._active_chatters["stream_1"] = mock_chatter1
        manager._active_chatters["stream_2"] = mock_chatter2
        
        result = manager.get_active_chatters()
        
        assert len(result) == 2
        assert result["stream_1"] == mock_chatter1
        assert result["stream_2"] == mock_chatter2
    
    def test_get_active_chatters_returns_copy(self) -> None:
        """测试返回的是副本而非原始字典。"""
        manager = ChatterManager()
        mock_chatter = MagicMock(spec=TestChatter)
        manager._active_chatters["stream_1"] = mock_chatter
        
        result = manager.get_active_chatters()
        
        # 修改返回的字典不应影响原始字典
        result["stream_2"] = MagicMock()
        assert "stream_2" not in manager._active_chatters


class TestChatterManagerGetChatterByStream:
    """测试根据流获取 Chatter 功能。"""
    
    def test_get_chatter_by_stream_exists(self) -> None:
        """测试获取已存在流的 Chatter。"""
        manager = ChatterManager()
        mock_chatter = MagicMock(spec=TestChatter)
        manager._active_chatters["stream_123"] = mock_chatter
        
        result = manager.get_chatter_by_stream("stream_123")
        
        assert result == mock_chatter
    
    def test_get_chatter_by_stream_not_exists(self) -> None:
        """测试获取不存在流的 Chatter 返回 None。"""
        manager = ChatterManager()
        
        result = manager.get_chatter_by_stream("non_existent_stream")
        
        assert result is None


class TestChatterManagerGetOrCreateChatterForStream:
    """测试为流获取或创建 Chatter 功能。"""
    
    def test_get_or_create_returns_existing(self) -> None:
        """测试返回已存在的 Chatter。"""
        manager = ChatterManager()
        mock_chatter = MagicMock(spec=TestChatter)
        manager._active_chatters["stream_123"] = mock_chatter
        
        result = manager.get_or_create_chatter_for_stream(
            stream_id="stream_123",
            chat_type="group",
            platform="qq"
        )
        
        assert result == mock_chatter
    
    def test_get_or_create_creates_new_chatter(self) -> None:
        """测试创建新的 Chatter。"""
        manager = ChatterManager()
        
        with patch.object(manager, '_select_chatter_class') as mock_select, \
             patch.object(manager, 'register_active_chatter') as mock_register:
            
            mock_chatter_class = MagicMock()
            mock_chatter_instance = MagicMock(spec=TestChatter)
            mock_chatter_class.return_value = mock_chatter_instance
            mock_select.return_value = mock_chatter_class
            
            result = manager.get_or_create_chatter_for_stream(
                stream_id="stream_new",
                chat_type="group",
                platform="qq"
            )
            
            assert result == mock_chatter_instance
            mock_register.assert_called_once_with("stream_new", mock_chatter_instance)
    
    def test_get_or_create_no_compatible_chatter(self) -> None:
        """测试无兼容 Chatter 时返回 None。"""
        manager = ChatterManager()
        
        with patch.object(manager, '_select_chatter_class') as mock_select:
            mock_select.return_value = None
            
            result = manager.get_or_create_chatter_for_stream(
                stream_id="stream_new",
                chat_type="group",
                platform="qq"
            )
            
            assert result is None


class TestChatterManagerEdgeCases:
    """测试边界条件。"""
    
    def test_register_chatter_empty_stream_id(self) -> None:
        """测试空 stream_id 注册。"""
        manager = ChatterManager()
        mock_chatter = MagicMock(spec=TestChatter)
        
        manager.register_active_chatter("", mock_chatter)
        
        # 应该能够注册空 stream_id
        assert "" in manager._active_chatters
    
    def test_replace_existing_chatter(self) -> None:
        """测试替换已存在的 Chatter。"""
        manager = ChatterManager()
        old_chatter = MagicMock(spec=TestChatter)
        new_chatter = MagicMock(spec=TestChatter)
        
        manager.register_active_chatter("stream_123", old_chatter)
        manager.register_active_chatter("stream_123", new_chatter)
        
        # 新 Chatter 应该替换旧的
        assert manager._active_chatters["stream_123"] == new_chatter
        assert manager._active_chatters["stream_123"] is not old_chatter
    
    def test_get_chatter_by_stream_empty_id(self) -> None:
        """测试空 stream_id 查询。"""
        manager = ChatterManager()
        
        result = manager.get_chatter_by_stream("")
        
        assert result is None
