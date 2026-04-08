"""llm_api 的单元测试。

测试覆盖：
- create_llm_request
- create_embedding_request
- create_rerank_request
- get_model_set_by_task
- get_model_set_by_name
- create_tool_registry
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.app.plugin_system.api import llm_api
from src.kernel.llm import ModelSet


class TestLLMAPI:
    """测试 LLM API。"""
    
    def test_create_llm_request(self) -> None:
        """测试创建 LLM 请求。"""
        with patch('src.app.plugin_system.api.llm_api.LLMRequest') as mock_request:
            mock_instance = MagicMock()
            mock_request.return_value = mock_instance
            mock_model_set = MagicMock(spec=ModelSet)
            
            result = llm_api.create_llm_request(mock_model_set, "test")
            
            assert result == mock_instance
            mock_request.assert_called_once()
    
    def test_create_embedding_request(self) -> None:
        """测试创建嵌入请求。"""
        with patch('src.app.plugin_system.api.llm_api.EmbeddingRequest') as mock_request:
            mock_instance = MagicMock()
            mock_request.return_value = mock_instance
            mock_model_set = MagicMock(spec=ModelSet)
            
            result = llm_api.create_embedding_request(
                mock_model_set,
                "test",
                inputs=["text1", "text2"]
            )
            
            assert result == mock_instance
    
    def test_create_rerank_request(self) -> None:
        """测试创建重排序请求。"""
        with patch('src.app.plugin_system.api.llm_api.RerankRequest') as mock_request:
            mock_instance = MagicMock()
            mock_request.return_value = mock_instance
            mock_model_set = MagicMock(spec=ModelSet)
            
            result = llm_api.create_rerank_request(
                mock_model_set,
                "test",
                query="query",
                documents=["doc1", "doc2"],
                top_n=5
            )
            
            assert result == mock_instance
    
    def test_get_model_set_by_task(self) -> None:
        """测试通过任务名获取 ModelSet。"""
        with patch('src.app.plugin_system.api.llm_api.get_model_config') as mock_config:
            mock_model_set = MagicMock(spec=ModelSet)
            mock_instance = MagicMock()
            mock_instance.get_task = MagicMock(return_value=mock_model_set)
            mock_config.return_value = mock_instance
            
            result = llm_api.get_model_set_by_task("chat")
            
            assert result == mock_model_set
            mock_instance.get_task.assert_called_once_with("chat")
    
    def test_get_model_set_by_name(self) -> None:
        """测试通过模型名获取 ModelSet。"""
        with patch('src.app.plugin_system.api.llm_api.get_model_config') as mock_config:
            mock_model_set = MagicMock(spec=ModelSet)
            mock_instance = MagicMock()
            mock_instance.get_model_set_by_name = MagicMock(return_value=mock_model_set)
            mock_config.return_value = mock_instance
            
            result = llm_api.get_model_set_by_name(
                "gpt-4",
                temperature=0.7,
                max_tokens=2000
            )
            
            assert result == mock_model_set
            mock_instance.get_model_set_by_name.assert_called_once_with(
                "gpt-4",
                temperature=0.7,
                max_tokens=2000
            )
    
    def test_create_tool_registry(self) -> None:
        """测试创建工具注册表。"""
        with patch('src.app.plugin_system.api.llm_api.ToolRegistry') as mock_registry:
            mock_instance = MagicMock()
            mock_registry.return_value = mock_instance
            
            result = llm_api.create_tool_registry()
            
            assert result == mock_instance
