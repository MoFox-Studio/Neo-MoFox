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

import base64
from collections.abc import Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.managers.media_manager import MediaManager, get_media_manager
from src.core.models.sql_alchemy import Base, Images, Voices


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


class TestMediaManagerSkipRecognition:
    """测试识别跳过功能。"""
    
    def test_skip_recognition_for_stream(self) -> None:
        """测试为特定流跳过识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            manager.skip_recognition_for_stream("stream_123")
            
            assert manager.should_skip_recognition("stream_123") is True
    
    def test_unskip_recognition_for_stream(self) -> None:
        """测试恢复特定流的识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            manager.skip_recognition_for_stream("stream_123")
            assert manager.should_skip_recognition("stream_123") is True
            
            manager.unskip_recognition_for_stream("stream_123")
            assert manager.should_skip_recognition("stream_123") is False
    
    def test_should_skip_recognition_not_in_list(self) -> None:
        """测试未跳过的流。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()
            
            assert manager.should_skip_recognition("stream_456") is False

    def test_skip_recognition_for_stream_with_media_types(self) -> None:
        """指定 media_types 时只对该类型生效，其余类型仍走识别。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.skip_recognition_for_stream("stream_dfc", media_types=("image",))

            # 整流粒度查询：只要注册过任意类型，都视为跳过
            assert manager.should_skip_recognition("stream_dfc") is True
            # 类型粒度查询
            assert manager.should_skip_recognition("stream_dfc", "image") is True
            assert manager.should_skip_recognition("stream_dfc", "emoji") is False
            assert manager.should_skip_recognition("stream_dfc", "voice") is False

    def test_skip_recognition_for_stream_default_skips_all_types(self) -> None:
        """不指定 media_types 时跳过所有媒体类型，保持向后兼容。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.skip_recognition_for_stream("stream_kfc")

            assert manager.should_skip_recognition("stream_kfc") is True
            assert manager.should_skip_recognition("stream_kfc", "image") is True
            assert manager.should_skip_recognition("stream_kfc", "emoji") is True
            assert manager.should_skip_recognition("stream_kfc", "voice") is True

    def test_unskip_recognition_clears_typed_skip(self) -> None:
        """unskip 必须同时清掉按类型注册的跳过。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.skip_recognition_for_stream("stream_dfc", media_types=("image",))
            manager.unskip_recognition_for_stream("stream_dfc")

            assert manager.should_skip_recognition("stream_dfc") is False
            assert manager.should_skip_recognition("stream_dfc", "image") is False


class TestMediaManagerGlobalSkipRecognition:
    """测试全局默认跳过策略。"""

    @pytest.fixture(autouse=True)
    def _mock_core_config(self) -> Iterator[MagicMock]:
        """为所有测试 mock get_core_config，避免初始化依赖。"""
        with patch('src.core.managers.media_manager.get_core_config') as mock:
            mock.return_value = MagicMock()
            yield mock

    def test_global_skip_takes_effect_when_no_stream_rule(self) -> None:
        """全局策略生效：未注册 stream 级规则时，全局策略命中。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.set_global_skip_media_types(("image",))

            # 未注册 stream 级规则的 stream，回退到全局策略
            assert manager.should_skip_recognition("any_stream", "image") is True
            assert manager.should_skip_recognition("any_stream", "emoji") is False
            assert manager.should_skip_recognition("any_stream", "voice") is False

    def test_global_skip_all_types(self) -> None:
        """全局策略设为 None 时跳过所有媒体类型。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.set_global_skip_media_types(None)

            assert manager.should_skip_recognition("any_stream", "image") is True
            assert manager.should_skip_recognition("any_stream", "emoji") is True
            assert manager.should_skip_recognition("any_stream", "voice") is True
            # 整流粒度查询也返回 True
            assert manager.should_skip_recognition("any_stream") is True

    def test_global_skip_without_media_type_param(self) -> None:
        """全局策略注册后，省略 media_type 也返回 True。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.set_global_skip_media_types(("image",))

            # 整流粒度查询：只要全局注册过任意类型，都视为跳过
            assert manager.should_skip_recognition("any_stream") is True

    def test_stream_level_overrides_global(self) -> None:
        """stream 级规则优先于全局策略，不回退全局。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            # 全局跳过 image，stream 级跳过 voice
            manager.set_global_skip_media_types(("image",))
            manager.skip_recognition_for_stream("stream_42", ("voice",))

            # stream 级规则已注册，不回退全局
            assert manager.should_skip_recognition("stream_42", "image") is False
            assert manager.should_skip_recognition("stream_42", "voice") is True
            # 整流粒度：stream 级注册过任意类型
            assert manager.should_skip_recognition("stream_42") is True

    def test_clear_global_skip_restores_default(self) -> None:
        """清除全局策略后恢复默认行为（识别）。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.set_global_skip_media_types(("image",))
            assert manager.should_skip_recognition("any_stream", "image") is True

            manager.clear_global_skip_media_types()
            assert manager.should_skip_recognition("any_stream", "image") is False
            assert manager.should_skip_recognition("any_stream") is False

    def test_global_skip_default_is_empty(self) -> None:
        """初始化后全局策略默认为空（不跳过任何类型）。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            assert manager.should_skip_recognition("any_stream", "image") is False
            assert manager.should_skip_recognition("any_stream", "emoji") is False
            assert manager.should_skip_recognition("any_stream") is False

    def test_global_skip_does_not_affect_stream_with_its_own_rule(self) -> None:
        """已注册 stream 级 all-type 跳过规则不受全局策略影响。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task'):
            manager = MediaManager()

            manager.set_global_skip_media_types(("image",))
            manager.skip_recognition_for_stream("stream_kfc")  # 跳过所有类型

            assert manager.should_skip_recognition("stream_kfc", "image") is True
            assert manager.should_skip_recognition("stream_kfc", "emoji") is True


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
            
            manager.skip_recognition_for_stream("stream_123")
            test_data = base64.b64encode(b"test_image_data").decode()
            
            # 跳过识别 识别的流使用缓存
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

    def test_extract_image_mime_type_prefers_data_url_value(self) -> None:
        """data URL 中存在图片 MIME 时应优先使用真实类型。"""
        mime_type = MediaManager._extract_image_mime_type(
            "data:image/jpeg;base64,dGVzdA=="
        )

        assert mime_type == "image/jpeg"

    def test_extract_image_mime_type_falls_back_to_png(self) -> None:
        """无法提取图片 MIME 时保持原有 png 默认值。"""
        mime_type = MediaManager._extract_image_mime_type("dGVzdA==")

        assert mime_type == "image/png"
    
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
    """测试语音识别（ASR）功能（通过统一 recognize_media 入口，media_type='voice'）。"""

    @pytest.mark.asyncio
    async def test_recognize_voice_asr_not_available(self) -> None:
        """测试 ASR 不可用时返回 None。"""
        with patch('src.core.managers.media_manager.get_model_set_by_task') as mock_get_model:
            mock_get_model.return_value = None

            manager = MediaManager()
            audio_b64 = base64.b64encode(b"fake_wav_data").decode()

            result = await manager.recognize_media(audio_b64, "voice")

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

                result = await manager.recognize_media(audio_b64, "voice")

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

                result = await manager.recognize_media(audio_b64, "voice")

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

                result = await manager.recognize_media(audio_b64, "voice")

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


class TestMediaManagerSaveUniquePathRegression:
    """回归测试：同一路径不同 hash 不应触发 UNIQUE constraint failed: images.path。

    历史 bug：``save_media_info`` 仅按 ``image_id`` 查询存在性，而 ``Images.path``
    上有 UNIQUE 约束。同一 file_path 以不同 media_hash 写入时，image_id 查不到
    旧记录 → 走 INSERT → path 已存在 → IntegrityError。

    这些用例使用真实内存 SQLite + 真实表结构验证修复。
    """

    @staticmethod
    def _make_manager() -> MediaManager:
        """构造一个绕过 __init__ 的 MediaManager，仅用于调用 save/get 方法。"""
        return MediaManager.__new__(MediaManager)

    @pytest.fixture
    async def real_session_factory(self):
        """创建内存 SQLite 引擎与会话工厂，建好 Images/Voices 表。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[Images.__table__, Voices.__table__])
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        try:
            yield factory
        finally:
            await engine.dispose()

    @staticmethod
    def _patch_session(factory):
        """把 media_manager.get_db_session 替换为使用真实 factory 的实现。"""

        @asynccontextmanager
        async def _real_session():
            async with factory() as session:
                try:
                    yield session
                    if session.is_active:
                        await session.commit()
                except Exception:
                    if session.is_active:
                        await session.rollback()
                    raise
                finally:
                    await session.close()

        return patch("src.core.managers.media_manager.get_db_session", lambda: _real_session())

    @pytest.mark.asyncio
    async def test_same_path_different_hash_updates_existing(self, real_session_factory) -> None:
        """同一路径不同 hash 第二次写入应更新原记录而非触发 UNIQUE 冲突。"""
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_media_info(
                media_hash="hash_A",
                media_type="image",
                file_path="/media/img.jpg",
                description="first",
            )
            # 第二次：相同 path，不同 hash —— 修复前这里会抛 IntegrityError
            await manager.save_media_info(
                media_hash="hash_B",
                media_type="image",
                file_path="/media/img.jpg",
                description="second",
                vlm_processed=True,
            )

        async with real_session_factory() as s:
            rows = (await s.execute(__import__("sqlalchemy").select(Images))).scalars().all()
            assert len(rows) == 1  # 没有重复插入
            row = rows[0]
            assert row.path == "/media/img.jpg"
            assert row.image_id == "hash_B"  # image_id 同步为最新 hash
            assert row.count == 2  # 计数累加
            assert row.description == "second"
            assert row.vlm_processed is True

    @pytest.mark.asyncio
    async def test_same_hash_second_call_increments_count(self, real_session_factory) -> None:
        """相同 hash 第二次写入应走更新分支，count 累加。"""
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_media_info("hash_X", "image", "/m/x.png")
            await manager.save_media_info("hash_X", "image", "/m/x.png")
            await manager.save_media_info("hash_X", "image", "/m/x.png")

        async with real_session_factory() as s:
            rows = (await s.execute(__import__("sqlalchemy").select(Images))).scalars().all()
            assert len(rows) == 1
            assert rows[0].count == 3

    @pytest.mark.asyncio
    async def test_distinct_paths_create_separate_rows(self, real_session_factory) -> None:
        """不同路径应分别建行。"""
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_media_info("hash_1", "image", "/m/a.png")
            await manager.save_media_info("hash_2", "image", "/m/b.png")

        async with real_session_factory() as s:
            rows = (await s.execute(__import__("sqlalchemy").select(Images))).scalars().all()
            assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_voice_same_path_different_hash_updates(self, real_session_factory) -> None:
        """save_voice_info 同型 bug 回归：同路径不同 hash 应更新而非冲突。"""
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_voice_info(
                voice_hash="vh_A",
                file_path="/media/v1.wav",
                description="first",
            )
            await manager.save_voice_info(
                voice_hash="vh_B",
                file_path="/media/v1.wav",
                description="second",
                asr_processed=True,
            )

        async with real_session_factory() as s:
            rows = (await s.execute(__import__("sqlalchemy").select(Voices))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.voice_id == "vh_B"
            assert row.path == "/media/v1.wav"
            assert row.count == 2
            assert row.description == "second"
            assert row.asr_processed is True
