"""log_api 的单元测试。

测试覆盖：
- get_logger
- 日志记录功能
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.app.plugin_system.api import log_api


class TestLogAPI:
    """测试日志 API。"""
    
    def test_get_logger(self) -> None:
        """测试获取 logger。"""
        with patch('src.app.plugin_system.api.log_api.kernel_get_logger') as mock_kernel_logger:
            mock_logger = MagicMock()
            mock_kernel_logger.return_value = mock_logger
            
            result = log_api.get_logger("test_module")
            
            assert result == mock_logger
            mock_kernel_logger.assert_called_once_with(
                name="test_module",
                display=None,
                color=None,
                enable_event_broadcast=True
            )
    
    def test_get_logger_with_display_name(self) -> None:
        """测试使用显示名称获取 logger。"""
        with patch('src.app.plugin_system.api.log_api.kernel_get_logger') as mock_kernel_logger:
            mock_logger = MagicMock()
            mock_kernel_logger.return_value = mock_logger
            
            result = log_api.get_logger("test_module", display="Test Module")
            
            assert result == mock_logger
            mock_kernel_logger.assert_called_once()
    
    def test_get_logger_with_color(self) -> None:
        """测试使用颜色获取 logger。"""
        with patch('src.app.plugin_system.api.log_api.kernel_get_logger') as mock_kernel_logger:
            mock_logger = MagicMock()
            mock_kernel_logger.return_value = mock_logger
            
            result = log_api.get_logger("test_module", color="blue")
            
            assert result == mock_logger
            mock_kernel_logger.assert_called_once()
