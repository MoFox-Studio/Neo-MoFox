"""MediaManager 的单元测试。

测试覆盖：
- 初始化和 VLM/ASR 配置
- VLM 跳过/恢复功能
- 媒体识别（图片和表情包）
- 语音识别（ASR）
- 批量识别
- 媒体信息保存和查询
- 缓存机制
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import base64

from src.core.managers.media_manager import MediaManager, get_media_manager


class TestMediaManagerInit:
    """测试 MediaManager 初始化。"""
    
    def test_init_without_vlm(self) -> None:
        """测试无 VLM 配置时的初始化。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task') as mock_get_model:
            mock_get_model.return_value = None
            
            manager = MediaManager()
            
            assert manager._vlm_available is False
            assert manager._vlm_model_set is None
            assert manager._asr_available is False
            assert manager._asr_model_set is None
    
    def test_init_with_vlm(self) -> None:
        """测试有 VLM 配置时的初始化（ASR 未配置）。"""
        def side_effect(task: str):
            if task == "vlm":
                return MagicMock()
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()
            
            assert manager._vlm_available is True
            assert manager._asr_available is False

    def test_init_with_asr(self) -> None:
        """测试有 ASR 配置时的初始化。"""
        def side_effect(task: str):
            if task == "voice":
                return MagicMock()
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()

            assert manager._asr_available is True
            assert manager._asr_model_set is not None
    
    def test_singleton_pattern(self) -> None:
        """验证单例模式实现。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager1 = get_media_manager()
            manager2 = get_media_manager()
            
            assert manager1 is manager2


class TestMediaManagerSkipVLM:
    """测试 VLM 跳过功能。"""
    
    def test_skip_vlm_for_stream(self) -> None:
        """测试为特定流跳过 VLM。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            manager.skip_vlm_for_stream("stream_123")
            
            assert manager.should_skip_vlm("stream_123") is True
    
    def test_unskip_vlm_for_stream(self) -> None:
        """测试恢复特定流的 VLM。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            manager.skip_vlm_for_stream("stream_123")
            assert manager.should_skip_vlm("stream_123") is True
            
            manager.unskip_vlm_for_stream("stream_123")
            assert manager.should_skip_vlm("stream_123") is False
    
    def test_should_skip_vlm_not_in_list(self) -> None:
        """测试未跳过的流。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            assert manager.should_skip_vlm("stream_456") is False

    def test_skip_vlm_for_stream_with_media_types(self) -> None:
        """指定 media_types 时只对该类型生效，其余类型仍走 VLM。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.skip_vlm_for_stream("stream_dfc", media_types=("image",))

            # 整流粒度查询：只要注册过任意类型，都视为跳过
            assert manager.should_skip_vlm("stream_dfc") is True
            # 类型粒度查询
            assert manager.should_skip_vlm("stream_dfc", "image") is True
            assert manager.should_skip_vlm("stream_dfc", "emoji") is False
            assert manager.should_skip_vlm("stream_dfc", "voice") is False

    def test_skip_vlm_for_stream_default_skips_all_types(self) -> None:
        """不指定 media_types 时跳过所有媒体类型，保持向后兼容。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.skip_vlm_for_stream("stream_kfc")

            assert manager.should_skip_vlm("stream_kfc") is True
            assert manager.should_skip_vlm("stream_kfc", "image") is True
            assert manager.should_skip_vlm("stream_kfc", "emoji") is True
            assert manager.should_skip_vlm("stream_kfc", "voice") is True

    def test_unskip_vlm_clears_typed_skip(self) -> None:
        """unskip 必须同时清掉按类型注册的跳过。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.skip_vlm_for_stream("stream_dfc", media_types=("image",))
            manager.unskip_vlm_for_stream("stream_dfc")

            assert manager.should_skip_vlm("stream_dfc") is False
            assert manager.should_skip_vlm("stream_dfc", "image") is False


class TestMediaManagerRecognizeMedia:
    """测试媒体识别功能。"""
    
    @pytest.mark.asyncio
    async def test_recognize_media_with_cache(self) -> None:
        """测试使用缓存的媒体识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            test_data = base64.b64encode(b"test_image_data").decode()
            
            with patch.object(manager, '_get_cached_description', new_callable=AsyncMock) as mock_cache:
                mock_cache.return_value = "Cached description"
                
                result = await manager.recognize_media(
                    base64_data=test_data,
                    media_type="image"
                )
                
                assert result == "Cached description"
    
    @pytest.mark.asyncio
    async def test_recognize_media_without_cache(self) -> None:
        """测试无缓存时进行 VLM 识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task') as mock_get_model:
            mock_model_set = MagicMock()
            mock_get_model.return_value = mock_model_set
            
            manager = MediaManager()
            test_data = base64.b64encode(b"test_image_data").decode()
            
            with patch.object(manager, '_get_cached_description', new_callable=AsyncMock) as mock_cache, \
                 patch.object(manager, '_recognize_with_vlm', new_callable=AsyncMock) as mock_vlm, \
                 patch.object(manager, '_save_description_cache', new_callable=AsyncMock):
                
                mock_cache.return_value = None
                mock_vlm.return_value = "VLM description"
                
                result = await manager.recognize_media(
                    base64_data=test_data,
                    media_type="image"
                )
                
                assert result == "VLM description"
    
    @pytest.mark.asyncio
    async def test_recognize_media_vlm_not_available(self) -> None:
        """测试 VLM 不可用时的降级处理。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task') as mock_get_model:
            mock_get_model.return_value = None
            
            manager = MediaManager()
            test_data = base64.b64encode(b"test_image_data").decode()
            
            with patch.object(manager, '_get_cached_description', new_callable=AsyncMock) as mock_cache:
                mock_cache.return_value = None
                
                result = await manager.recognize_media(
                    base64_data=test_data,
                    media_type="image"
                )
                
                # VLM 不可用时应返回默认描述或 None
                assert result is None or isinstance(result, str)
    
    @pytest.mark.asyncio
    async def test_recognize_media_skip_for_stream(self) -> None:
        """测试跳过特定流的识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            manager.skip_vlm_for_stream("stream_123")
            test_data = base64.b64encode(b"test_image_data").decode()
            
            # 跳过 VLM 识别的流使用缓存
            with patch.object(manager, '_get_cached_description', new_callable=AsyncMock) as mock_cache:
                mock_cache.return_value = None
                
                result = await manager.recognize_media(
                    base64_data=test_data,
                    media_type="image",
                    use_cache=True
                )
                
                # 应该跳过识别
                assert result is None


class TestMediaManagerRecognizeBatch:
    """测试批量识别功能。"""
    
    @pytest.mark.asyncio
    async def test_recognize_batch_empty_list(self) -> None:
        """测试空列表批量识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            results = await manager.recognize_batch([])
            
            assert results == []
    
    @pytest.mark.asyncio
    async def test_recognize_batch_multiple_items(self) -> None:
        """测试多个项目批量识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            items = [
                (base64.b64encode(b"data1").decode(), "image"),
                (base64.b64encode(b"data2").decode(), "emoji"),
            ]
            
            with patch.object(manager, 'recognize_media', new_callable=AsyncMock) as mock_recognize:
                mock_recognize.side_effect = ["Description 1", "Description 2"]
                
                results = await manager.recognize_batch(items)
                
                assert len(results) == 2
                assert results[0] == (0, "Description 1")
                assert results[1] == (1, "Description 2")


class TestMediaManagerSaveAndGetMediaInfo:
    """测试媒体信息保存和查询功能。"""
    
    @pytest.mark.asyncio
    async def test_save_media_info(self) -> None:
        """测试保存媒体信息。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            with patch('src.core.managers.media_manager.get_db_session') as mock_session:
                mock_session_ctx = MagicMock()
                mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
                mock_session_ctx.__aexit__ = AsyncMock()
                mock_session.return_value = mock_session_ctx
                
                await manager.save_media_info(
                    media_hash="abc123",
                    media_type="image",
                    file_path="/path/to/image.jpg",
                    description="Test image",
                    vlm_processed=True
                )
    
    @pytest.mark.asyncio
    async def test_get_media_info_exists(self) -> None:
        """测试获取已存在的媒体信息。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            with patch('src.core.managers.media_manager.CRUDBase') as mock_crud_class:
                mock_crud = MagicMock()
                
                mock_media = MagicMock()
                mock_media.media_hash = "abc123"
                mock_media.media_type = "image"
                mock_media.description = "Test image"
                
                mock_crud.get_by = AsyncMock(return_value=mock_media)
                mock_crud_class.return_value = mock_crud
                
                result = await manager.get_media_info("abc123")
                
                assert result is not None
                assert result["media_hash"] == "abc123"
    
    @pytest.mark.asyncio
    async def test_get_media_info_not_exists(self) -> None:
        """测试获取不存在的媒体信息。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            with patch('src.core.managers.media_manager.CRUDBase') as mock_crud_class:
                mock_crud = MagicMock()
                mock_crud.get_by = AsyncMock(return_value=None)
                mock_crud_class.return_value = mock_crud
                
                result = await manager.get_media_info("non_existent_hash")
                
                assert result is None


class TestMediaManagerEdgeCases:
    """测试边界条件。"""
    
    @pytest.mark.asyncio
    async def test_recognize_empty_base64_data(self) -> None:
        """测试空 base64 数据。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            result = await manager.recognize_media(
                base64_data="",
                media_type="image"
            )
            
            # 空数据应该返回 None 或错误
            assert result is None or result == ""
    
    @pytest.mark.asyncio
    async def test_recognize_invalid_media_type(self) -> None:
        """测试无效的媒体类型。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            test_data = base64.b64encode(b"test_data").decode()
            
            with patch.object(manager, '_get_cached_description', new_callable=AsyncMock) as mock_cache:
                mock_cache.return_value = None
                
                result = await manager.recognize_media(
                    base64_data=test_data,
                    media_type="invalid_type"
                )
                
                # 应该能够处理无效类型
                assert isinstance(result, (str, type(None)))


class TestMediaManagerRecognizeVoice:
    """测试语音识别（ASR）功能。"""

    @pytest.mark.asyncio
    async def test_recognize_voice_asr_not_available(self) -> None:
        """测试 ASR 不可用时返回 None。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task') as mock_get_model:
            mock_get_model.return_value = None

            manager = MediaManager()
            audio_b64 = base64.b64encode(b"fake_wav_data").decode()

            result = await manager.recognize_voice(audio_b64)

            assert result is None

    @pytest.mark.asyncio
    async def test_recognize_voice_success(self) -> None:
        """测试 ASR 识别成功返回文字。"""
        # model_set 是 list[dict]，与 get_model_set_by_task 返回格式一致
        mock_model_set = [{"model_identifier": "sensevoice-small", "api_key": "sk-test", "base_url": "http://localhost"}]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"fake_wav_data").decode()

            with patch.object(manager, '_recognize_with_asr', new_callable=AsyncMock) as mock_asr:
                mock_asr.return_value = "你好，世界"

                result = await manager.recognize_voice(audio_b64)

                assert result == "你好，世界"
                mock_asr.assert_called_once_with(audio_b64)

    @pytest.mark.asyncio
    async def test_recognize_voice_asr_returns_none(self) -> None:
        """测试 ASR 识别返回 None 时行为。"""
        mock_model_set = [{"model_identifier": "sensevoice-small", "api_key": "sk-test"}]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"silence").decode()

            with patch.object(manager, '_recognize_with_asr', new_callable=AsyncMock) as mock_asr:
                mock_asr.return_value = None

                result = await manager.recognize_voice(audio_b64)

                assert result is None

    @pytest.mark.asyncio
    async def test_recognize_voice_exception_returns_none(self) -> None:
        """测试 ASR 识别抛出异常时返回 None。"""
        mock_model_set = [{"model_identifier": "sensevoice-small", "api_key": "sk-test"}]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"bad_data").decode()

            with patch.object(manager, '_recognize_with_asr', new_callable=AsyncMock) as mock_asr:
                mock_asr.side_effect = RuntimeError("ASR 连接失败")

                result = await manager.recognize_voice(audio_b64)

                assert result is None

    @pytest.mark.asyncio
    async def test_recognize_with_asr_calls_client(self) -> None:
        """测试 _recognize_with_asr 正确调用 ASR client。"""
        mock_model_entry = {"model_identifier": "sensevoice-small", "api_key": "sk-test", "base_url": "http://localhost"}
        mock_model_set = [mock_model_entry]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"wav_bytes").decode()

            mock_client = AsyncMock()
            mock_client.create_transcription = AsyncMock(return_value="识别文字")

            with patch('src.core.managers.media_manager.ModelClientRegistry') as mock_registry_cls:
                mock_registry = MagicMock()
                mock_registry_cls.return_value = mock_registry
                mock_registry.get_asr_client_for_model.return_value = mock_client

                result = await manager._recognize_with_asr(audio_b64)

                assert result == "识别文字"
                mock_registry.get_asr_client_for_model.assert_called_once_with(mock_model_entry)
                mock_client.create_transcription.assert_called_once()

    @pytest.mark.asyncio
    async def test_recognize_with_asr_no_models(self) -> None:
        """测试 model_set 中无模型时返回 None。"""
        mock_model_set = []  # 空列表

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch('src.core.managers.media_manager.get_model_set_by_task', side_effect=side_effect):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"wav_bytes").decode()

            result = await manager._recognize_with_asr(audio_b64)

            assert result is None
