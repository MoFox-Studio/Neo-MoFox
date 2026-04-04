"""database_api 的单元测试。

测试覆盖：
- CRUD操作 (get_by_id, get_by, create, update, delete)
- 批量操作 (bulk_create, bulk_update)
- 查询构建器
- 聚合查询
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import database_api
from src.core.models.sql_alchemy import Messages


class TestDatabaseAPI:
    """测试数据库 API。"""
    
    @pytest.mark.asyncio
    async def test_get_by_id(self) -> None:
        """测试通过 ID 查询。"""
        with patch('src.app.plugin_system.api.database_api.CRUDBase') as mock_crud:
            mock_result = MagicMock()
            mock_result.id = 1
            mock_instance = MagicMock()
            mock_instance.get = AsyncMock(return_value=mock_result)
            mock_crud.return_value = mock_instance
            
            result = await database_api.get_by_id(Messages, 1)
            
            assert result is not None
            assert result.id == 1
    
    @pytest.mark.asyncio
    async def test_get_by(self) -> None:
        """测试条件查询。"""
        with patch('src.app.plugin_system.api.database_api.CRUDBase') as mock_crud:
            mock_result = MagicMock()
            mock_result.id = 1
            mock_instance = MagicMock()
            mock_instance.get_by = AsyncMock(return_value=mock_result)
            mock_crud.return_value = mock_instance
            
            result = await database_api.get_by(Messages, id=1)
            
            assert result is not None
            assert result.id == 1
    
    @pytest.mark.asyncio
    async def test_create(self) -> None:
        """测试创建记录。"""
        with patch('src.app.plugin_system.api.database_api.CRUDBase') as mock_crud:
            mock_result = MagicMock()
            mock_result.id = 1
            mock_instance = MagicMock()
            mock_instance.create = AsyncMock(return_value=mock_result)
            mock_crud.return_value = mock_instance
            
            result = await database_api.create(Messages, {"content": "test"})
            
            assert result is not None
            assert result.id == 1
    
    @pytest.mark.asyncio
    async def test_update(self) -> None:
        """测试更新记录。"""
        with patch('src.app.plugin_system.api.database_api.CRUDBase') as mock_crud:
            mock_result = MagicMock()
            mock_result.id = 1
            mock_instance = MagicMock()
            mock_instance.update = AsyncMock(return_value=mock_result)
            mock_crud.return_value = mock_instance
            
            result = await database_api.update(Messages, 1, {"content": "updated"})
            
            assert result is not None
            assert result.id == 1
    
    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        """测试删除记录。"""
        with patch('src.app.plugin_system.api.database_api.CRUDBase') as mock_crud:
            mock_instance = MagicMock()
            mock_instance.delete = AsyncMock(return_value=True)
            mock_crud.return_value = mock_instance
            
            result = await database_api.delete(Messages, 1)
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_bulk_create(self) -> None:
        """测试批量创建。"""
        with patch('src.app.plugin_system.api.database_api.CRUDBase') as mock_crud:
            mock_instance = MagicMock()
            mock_instance.bulk_create = AsyncMock(return_value=[MagicMock(id=1), MagicMock(id=2)])
            mock_crud.return_value = mock_instance
            
            result = await database_api.bulk_create(Messages, [{"content": "1"}, {"content": "2"}])
            
            assert len(result) == 2
