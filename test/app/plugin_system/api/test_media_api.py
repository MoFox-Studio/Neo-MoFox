"""media_api 的单元测试。

测试覆盖：
- recognize_media（含 image / emoji / voice 路由）
- recognize_batch
- save_media_info（含 voice 路由）
- get_media_info（含 voice 回退查询）
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import media_api


class TestMediaAPI:
    """测试媒体 API。"""
    
    @pytest.mark.asyncio
    async def test_recognize_media(self) -> None:
        """测试识别媒体。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.recognize_media = AsyncMock(return_value="A picture of a cat")
            mock_get_mgr.return_value = mock_manager
            
            result = await media_api.recognize_media("base64data", "image", use_cache=True)
            
            assert result == "A picture of a cat"
    
    @pytest.mark.asyncio
    async def test_recognize_batch(self) -> None:
        """测试批量识别媒体。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.recognize_batch = AsyncMock(
                return_value=[(0, "Cat"), (1, "Dog")]
            )
            mock_get_mgr.return_value = mock_manager
            
            media_list = [("base64_1", "image"), ("base64_2", "image")]
            result = await media_api.recognize_batch(media_list)
            
            assert len(result) == 2
            assert result[0] == (0, "Cat")
    
    @pytest.mark.asyncio
    async def test_save_media_info(self) -> None:
        """测试保存媒体信息。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.save_media_info = AsyncMock()
            mock_get_mgr.return_value = mock_manager
            
            await media_api.save_media_info(
                "hash123",
                "image",
                file_path="/path/to/image.jpg",
                description="Test image",
                vlm_processed=True
            )
            
            mock_manager.save_media_info.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_media_info(self) -> None:
        """测试获取媒体信息。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            media_info = {
                "hash": "hash123",
                "media_type": "image",
                "description": "Test image"
            }
            mock_manager.get_media_info = AsyncMock(return_value=media_info)
            mock_get_mgr.return_value = mock_manager
            
            result = await media_api.get_media_info("hash123")
            
            assert result is not None
            assert result["hash"] == "hash123"

    @pytest.mark.asyncio
    async def test_recognize_media_voice(self) -> None:
        """测试识别语音（media_type='voice' 路由到 manager.recognize_media）。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.recognize_media = AsyncMock(return_value="你好，世界")
            mock_get_mgr.return_value = mock_manager

            result = await media_api.recognize_media("base64audio", "voice", use_cache=True)

            assert result == "你好，世界"
            mock_manager.recognize_media.assert_called_once_with(
                base64_data="base64audio", media_type="voice", use_cache=True
            )

    @pytest.mark.asyncio
    async def test_recognize_media_voice_empty_raises(self) -> None:
        """测试空语音数据抛出 ValueError。"""
        with pytest.raises(ValueError):
            await media_api.recognize_media("   ", "voice")

    @pytest.mark.asyncio
    async def test_recognize_media_invalid_type_raises(self) -> None:
        """测试非法 media_type 抛出 ValueError。"""
        with pytest.raises(ValueError):
            await media_api.recognize_media("base64data", "audio")

    @pytest.mark.asyncio
    async def test_save_media_info_voice(self) -> None:
        """测试保存语音信息（media_type='voice' 路由到 save_voice_info）。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.save_voice_info = AsyncMock()
            mock_get_mgr.return_value = mock_manager

            await media_api.save_media_info(
                "voicehash123",
                "voice",
                file_path="/path/to/voice.wav",
                description="你好",
                vlm_processed=True,
            )

            mock_manager.save_voice_info.assert_called_once_with(
                voice_hash="voicehash123",
                file_path="/path/to/voice.wav",
                description="你好",
                asr_processed=True,
            )

    @pytest.mark.asyncio
    async def test_save_media_info_voice_empty_hash_raises(self) -> None:
        """测试空 voice 哈希抛出 ValueError。"""
        with pytest.raises(ValueError):
            await media_api.save_media_info("", "voice")

    @pytest.mark.asyncio
    async def test_get_media_info_voice(self) -> None:
        """测试获取语音信息（manager.get_media_info 返回语音信息时直接返回）。"""
        with patch('src.app.plugin_system.api.media_api._get_media_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_media_info = AsyncMock(return_value={
                "voice_id": "voicehash123",
                "path": "/path/to/voice.wav",
                "description": "你好，世界",
            })
            mock_get_mgr.return_value = mock_manager

            result = await media_api.get_media_info("voicehash123")

            assert result is not None
            assert result["voice_id"] == "voicehash123"
