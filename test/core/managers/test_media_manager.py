"""MediaManager（拆分为子包后）的单元测试。

测试覆盖：
- 初始化和 VLM/ASR 配置
- VLM 跳过/恢复功能
- 媒体识别（图片和表情包）
- 语音识别（ASR）
- 批量识别
- 媒体信息保存和查询
- 缓存机制
- 边界条件和异常处理

拆分后的 patch 策略：
- 模块级符号（get_model_set_by_task / get_core_config / get_prompt_manager /
  get_db_session / ModelClientRegistry / logger）按所在子模块分别 patch
- 实例方法（_recognize_with_vlm → vlm_engine.recognize 等）改为 patch 子组件实例
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.managers.media_manager import MediaManager, get_media_manager
from src.core.managers.media_manager.config import MediaConfig
from src.core.managers.media_manager.repository import MediaRepository
from src.core.managers.media_manager.utils import (
    extract_gif_key_frames,
    extract_image_mime_type,
)
from src.core.models.sql_alchemy import Base, Images, Voices


# ──────────────────────────────────────────
# 通用 fixture
# ──────────────────────────────────────────


def _mock_chat_cfg() -> MagicMock:
    """构造一个默认值的 ChatSection mock，避免依赖真实 core_config。"""
    chat = MagicMock()
    chat.image_recognition_prompt = ""
    chat.emoji_recognition_prompt = ""
    chat.media_cache_cleanup_enabled = False
    chat.media_cache_cleanup_interval_hours = 0.5
    chat.media_file_cleanup_enabled = False
    chat.media_file_max_age_days = 7
    chat.media_file_max_total_size_mb = 500
    chat.media_file_cleanup_interval_hours = 1.0
    return chat


@pytest.fixture
def patched_core_config() -> Any:
    """patch 掉 _config.get_core_config，返回带默认 chat 配置的 mock。

    用于绕过 "Core config not initialized" 错误，让 MediaManager() 可正常构造。
    """
    mock_config = MagicMock()
    mock_config.chat = _mock_chat_cfg()
    with patch(
        "src.core.managers.media_manager.config.get_core_config",
        return_value=mock_config,
    ):
        yield mock_config


@pytest.fixture
def patched_no_model_set() -> Any:
    """patch 掉 _config.get_model_set_by_task 返回 None（无 VLM/ASR）。"""
    with patch(
        "src.core.managers.media_manager.config.get_model_set_by_task",
        return_value=None,
    ):
        yield


@pytest.fixture
def make_manager(patched_core_config, patched_no_model_set):
    """提供可构造 MediaManager 的环境，并重置单例。"""
    global _media_manager
    import src.core.managers.media_manager.manager as mgr_mod

    mgr_mod._media_manager = None

    def _factory() -> MediaManager:
        return MediaManager()

    return _factory


# ──────────────────────────────────────────
# 初始化测试
# ──────────────────────────────────────────


class TestMediaManagerInit:
    """测试 MediaManager 初始化。"""

    def test_init_without_vlm(self, patched_core_config) -> None:
        """测试无 VLM 配置时的初始化。"""
        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            return_value=None,
        ):
            manager = MediaManager()

            assert manager._config.vlm_available is False
            assert manager._config.vlm_model_set is None
            assert manager._config.asr_available is False
            assert manager._config.asr_model_set is None

    def test_init_with_vlm(self, patched_core_config) -> None:
        """测试有 VLM 配置时的初始化（ASR 未配置）。"""

        def side_effect(task: str):
            if task == "vlm":
                return MagicMock()
            return None

        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            side_effect=side_effect,
        ):
            manager = MediaManager()

            assert manager._config.vlm_available is True
            assert manager._config.asr_available is False

    def test_init_with_asr(self, patched_core_config) -> None:
        """测试有 ASR 配置时的初始化。"""

        def side_effect(task: str):
            if task == "voice":
                return MagicMock()
            return None

        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            side_effect=side_effect,
        ):
            manager = MediaManager()

            assert manager._config.asr_available is True
            assert manager._config.asr_model_set is not None

    def test_singleton_pattern(self, patched_core_config, patched_no_model_set) -> None:
        """验证单例模式实现。"""
        m1 = get_media_manager()
        m2 = get_media_manager()
        assert m1 is m2


# ──────────────────────────────────────────
# 跳过识别测试
# ──────────────────────────────────────────


class TestMediaManagerSkipRecognition:
    """测试识别跳过功能。"""

    def test_skip_recognition_for_stream(self, make_manager) -> None:
        manager = make_manager()
        manager.skip_recognition_for_stream("stream_123")
        assert manager.should_skip_recognition("stream_123") is True

    def test_unskip_recognition_for_stream(self, make_manager) -> None:
        manager = make_manager()
        manager.skip_recognition_for_stream("stream_123")
        assert manager.should_skip_recognition("stream_123") is True

        manager.unskip_recognition_for_stream("stream_123")
        assert manager.should_skip_recognition("stream_123") is False

    def test_should_skip_recognition_not_in_list(self, make_manager) -> None:
        manager = make_manager()
        assert manager.should_skip_recognition("stream_456") is False

    def test_skip_recognition_for_stream_with_media_types(self, make_manager) -> None:
        manager = make_manager()
        manager.skip_recognition_for_stream("stream_dfc", media_types=("image",))

        # 整流粒度查询：只要注册过任意类型，都视为跳过
        assert manager.should_skip_recognition("stream_dfc") is True
        # 类型粒度查询
        assert manager.should_skip_recognition("stream_dfc", "image") is True
        assert manager.should_skip_recognition("stream_dfc", "emoji") is False
        assert manager.should_skip_recognition("stream_dfc", "voice") is False

    def test_skip_recognition_for_stream_default_skips_all_types(
        self, make_manager
    ) -> None:
        manager = make_manager()
        manager.skip_recognition_for_stream("stream_kfc")

        assert manager.should_skip_recognition("stream_kfc") is True
        assert manager.should_skip_recognition("stream_kfc", "image") is True
        assert manager.should_skip_recognition("stream_kfc", "emoji") is True
        assert manager.should_skip_recognition("stream_kfc", "voice") is True

    def test_unskip_recognition_clears_typed_skip(self, make_manager) -> None:
        manager = make_manager()
        manager.skip_recognition_for_stream("stream_dfc", media_types=("image",))
        manager.unskip_recognition_for_stream("stream_dfc")

        assert manager.should_skip_recognition("stream_dfc") is False
        assert manager.should_skip_recognition("stream_dfc", "image") is False


# ──────────────────────────────────────────
# 媒体识别测试
# ──────────────────────────────────────────


class TestMediaManagerRecognizeMedia:
    """测试媒体识别功能。"""

    @pytest.mark.asyncio
    async def test_recognize_media_with_cache(self, make_manager) -> None:
        """测试使用缓存的媒体识别。"""
        manager = make_manager()
        test_data = base64.b64encode(b"test_image_data").decode()

        with patch.object(
            manager._cache, "get_cached_description", new_callable=AsyncMock
        ) as mock_cache:
            mock_cache.return_value = "Cached description"

            result = await manager.recognize_media(
                base64_data=test_data,
                media_type="image",
            )

            assert result == "Cached description"

    @pytest.mark.asyncio
    async def test_recognize_media_without_cache(self, patched_core_config) -> None:
        """测试无缓存时进行 VLM 识别（patch 子组件实例）。"""
        mock_model_set = MagicMock()
        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            return_value=mock_model_set,
        ):
            manager = MediaManager()
            test_data = base64.b64encode(b"test_image_data").decode()

            with (
                patch.object(
                    manager._cache, "get_cached_description", new_callable=AsyncMock
                ) as mock_cache,
                patch.object(
                    manager._vlm_engine, "recognize", new_callable=AsyncMock
                ) as mock_vlm,
                patch.object(
                    manager._cache, "save_description_cache", new_callable=AsyncMock
                ),
            ):
                mock_cache.return_value = None
                mock_vlm.return_value = "VLM description"

                result = await manager.recognize_media(
                    base64_data=test_data,
                    media_type="image",
                )

                assert result == "VLM description"

    @pytest.mark.asyncio
    async def test_recognize_media_vlm_not_available(self, make_manager) -> None:
        """测试 VLM 不可用时的降级处理。"""
        manager = make_manager()
        test_data = base64.b64encode(b"test_image_data").decode()

        with patch.object(
            manager._cache, "get_cached_description", new_callable=AsyncMock
        ) as mock_cache:
            mock_cache.return_value = None

            result = await manager.recognize_media(
                base64_data=test_data,
                media_type="image",
            )

            assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_recognize_media_skip_for_stream(self, make_manager) -> None:
        """测试跳过特定流的识别。"""
        manager = make_manager()
        manager.skip_recognition_for_stream("stream_123")
        test_data = base64.b64encode(b"test_image_data").decode()

        with patch.object(
            manager._cache, "get_cached_description", new_callable=AsyncMock
        ) as mock_cache:
            mock_cache.return_value = None

            result = await manager.recognize_media(
                base64_data=test_data,
                media_type="image",
                use_cache=True,
                stream_id="stream_123",
                skip_recognition=manager.should_skip_recognition("stream_123", "image"),
            )

            assert result is None


# ──────────────────────────────────────────
# 批量识别测试
# ──────────────────────────────────────────


class TestMediaManagerRecognizeBatch:
    """测试批量识别功能。"""

    @pytest.mark.asyncio
    async def test_recognize_batch_empty_list(self, make_manager) -> None:
        manager = make_manager()
        results = await manager.recognize_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_recognize_batch_multiple_items(self, make_manager) -> None:
        manager = make_manager()
        items = [
            (base64.b64encode(b"data1").decode(), "image"),
            (base64.b64encode(b"data2").decode(), "emoji"),
        ]

        with patch.object(
            manager._recognition, "recognize_media", new_callable=AsyncMock
        ) as mock_recognize:
            mock_recognize.side_effect = ["Description 1", "Description 2"]

            results = await manager.recognize_batch(items)

            assert len(results) == 2
            assert results[0] == (0, "Description 1")
            assert results[1] == (1, "Description 2")


# ──────────────────────────────────────────
# 数据库读写测试
# ──────────────────────────────────────────


class TestMediaManagerSaveAndGetMediaInfo:
    """测试媒体信息保存和查询功能。"""

    @pytest.mark.asyncio
    async def test_save_media_info(self, make_manager) -> None:
        """测试保存媒体信息。"""
        manager = make_manager()

        with patch(
            "src.core.managers.media_manager.repository.get_db_session"
        ) as mock_session:
            mock_session_ctx = MagicMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session_ctx.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_ctx

            await manager.save_media_info(
                media_hash="abc123",
                media_type="image",
                file_path="/path/to/image.jpg",
                description="Test image",
                vlm_processed=True,
            )

    @pytest.mark.asyncio
    async def test_get_media_info_exists(self, make_manager) -> None:
        """测试获取已存在的媒体信息。"""
        manager = make_manager()

        mock_media = MagicMock()
        mock_media.id = 1
        mock_media.image_id = "abc123"
        mock_media.path = "/p.jpg"
        mock_media.type = "image"
        mock_media.description = "Test image"
        mock_media.count = 1
        mock_media.timestamp = 0.0
        mock_media.vlm_processed = True

        with patch.object(
            manager._repository, "get_media_info", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = {
                "id": mock_media.id,
                "image_id": mock_media.image_id,
                "path": mock_media.path,
                "type": mock_media.type,
                "description": mock_media.description,
                "count": mock_media.count,
                "timestamp": mock_media.timestamp,
                "vlm_processed": mock_media.vlm_processed,
            }
            result = await manager.get_media_info("abc123")

            assert result is not None
            assert result["image_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_media_info_not_exists(self, make_manager) -> None:
        """测试获取不存在的媒体信息。"""
        manager = make_manager()

        with patch.object(
            manager._repository, "get_media_info", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = None
            result = await manager.get_media_info("non_existent_hash")
            assert result is None


# ──────────────────────────────────────────
# 边界条件测试
# ──────────────────────────────────────────


class TestMediaManagerEdgeCases:
    """测试边界条件。"""

    def test_extract_image_mime_type_prefers_data_url_value(self) -> None:
        """data URL 中存在图片 MIME 时应优先使用真实类型。"""
        mime_type = extract_image_mime_type("data:image/jpeg;base64,dGVzdA==")
        assert mime_type == "image/jpeg"

    def test_extract_image_mime_type_falls_back_to_png(self) -> None:
        """无法提取图片 MIME 时保持原有 png 默认值。"""
        mime_type = extract_image_mime_type("dGVzdA==")
        assert mime_type == "image/png"

    @pytest.mark.asyncio
    async def test_recognize_empty_base64_data(self, make_manager) -> None:
        """测试空 base64 数据。"""
        manager = make_manager()
        result = await manager.recognize_media(
            base64_data="",
            media_type="image",
        )
        # 空数据应该返回 None
        assert result is None

    @pytest.mark.asyncio
    async def test_recognize_invalid_media_type(self, make_manager) -> None:
        """测试无效的媒体类型。"""
        manager = make_manager()
        test_data = base64.b64encode(b"test_data").decode()

        with patch.object(
            manager._cache, "get_cached_description", new_callable=AsyncMock
        ) as mock_cache:
            mock_cache.return_value = None
            result = await manager.recognize_media(
                base64_data=test_data,
                media_type="invalid_type",
            )
            assert isinstance(result, (str, type(None)))


# ──────────────────────────────────────────
# 语音识别测试
# ──────────────────────────────────────────


class TestMediaManagerRecognizeVoice:
    """测试语音识别（ASR）功能。"""

    @pytest.mark.asyncio
    async def test_recognize_voice_asr_not_available(self, make_manager) -> None:
        """测试 ASR 不可用时返回 None。"""
        manager = make_manager()
        audio_b64 = base64.b64encode(b"fake_wav_data").decode()

        result = await manager.recognize_media(audio_b64, "voice")
        assert result is None

    @pytest.mark.asyncio
    async def test_recognize_voice_success(self, patched_core_config) -> None:
        """测试 ASR 识别成功返回文字。"""
        mock_model_set = [
            {
                "model_identifier": "sensevoice-small",
                "api_key": "sk-test",
                "base_url": "http://localhost",
            }
        ]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            side_effect=side_effect,
        ):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"fake_wav_data").decode()

            with patch.object(
                manager._asr_engine, "recognize", new_callable=AsyncMock
            ) as mock_asr:
                mock_asr.return_value = "你好，世界"
                result = await manager.recognize_media(audio_b64, "voice")

                assert result == "你好，世界"
                mock_asr.assert_called_once_with(audio_b64)

    @pytest.mark.asyncio
    async def test_recognize_voice_asr_returns_none(self, patched_core_config) -> None:
        """测试 ASR 识别返回 None 时行为。"""
        mock_model_set = [
            {"model_identifier": "sensevoice-small", "api_key": "sk-test"}
        ]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            side_effect=side_effect,
        ):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"silence").decode()

            with patch.object(
                manager._asr_engine, "recognize", new_callable=AsyncMock
            ) as mock_asr:
                mock_asr.return_value = None
                result = await manager.recognize_media(audio_b64, "voice")
                assert result is None

    @pytest.mark.asyncio
    async def test_recognize_voice_exception_returns_none(
        self, patched_core_config
    ) -> None:
        """测试 ASR 识别抛出异常时返回 None。"""
        mock_model_set = [
            {"model_identifier": "sensevoice-small", "api_key": "sk-test"}
        ]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            side_effect=side_effect,
        ):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"bad_data").decode()

            with patch.object(
                manager._asr_engine, "recognize", new_callable=AsyncMock
            ) as mock_asr:
                mock_asr.side_effect = RuntimeError("ASR 连接失败")
                result = await manager.recognize_media(audio_b64, "voice")
                assert result is None

    @pytest.mark.asyncio
    async def test_recognize_with_asr_calls_client(self, patched_core_config) -> None:
        """测试 ASREngine.recognize 正确调用 ASR client。"""
        mock_model_entry = {
            "model_identifier": "sensevoice-small",
            "api_key": "sk-test",
            "base_url": "http://localhost",
        }
        mock_model_set = [mock_model_entry]

        def side_effect(task: str):
            if task == "voice":
                return mock_model_set
            return None

        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            side_effect=side_effect,
        ):
            manager = MediaManager()
            audio_b64 = base64.b64encode(b"wav_bytes").decode()

            mock_client = AsyncMock()
            mock_client.create_transcription = AsyncMock(return_value="识别文字")

            with patch(
                "src.core.managers.media_manager.engines.ModelClientRegistry"
            ) as mock_registry_cls:
                mock_registry = MagicMock()
                mock_registry_cls.return_value = mock_registry
                mock_registry.get_asr_client_for_model.return_value = mock_client

                result = await manager._asr_engine.recognize(audio_b64)

                assert result == "识别文字"
                mock_registry.get_asr_client_for_model.assert_called_once_with(
                    mock_model_entry
                )
                mock_client.create_transcription.assert_called_once()

    @pytest.mark.asyncio
    async def test_recognize_with_asr_no_models(self, patched_core_config) -> None:
        """测试 model_set 中无模型时返回 None。"""
        with patch(
            "src.core.managers.media_manager.config.get_model_set_by_task",
            return_value=[],
        ):
            # voice 返回 []，但需保证 vlm 也返回 None 才不会跳过 voice 分支
            def side_effect(task: str):
                if task == "voice":
                    return []
                return None

            with patch(
                "src.core.managers.media_manager.config.get_model_set_by_task",
                side_effect=side_effect,
            ):
                manager = MediaManager()
                audio_b64 = base64.b64encode(b"wav_bytes").decode()

                result = await manager._asr_engine.recognize(audio_b64)
                assert result is None


# ──────────────────────────────────────────
# 唯一路径回归测试
# ──────────────────────────────────────────


class TestMediaManagerSaveUniquePathRegression:
    """回归测试：同一路径不同 hash 不应触发 UNIQUE constraint failed: images.path。"""

    @staticmethod
    def _make_manager() -> MediaManager:
        """构造一个绕过 __init__ 的 MediaManager，仅用于调用 save/get 方法。"""
        manager = MediaManager.__new__(MediaManager)
        manager._config = MediaConfig.__new__(MediaConfig)
        manager._repository = MediaRepository()
        return manager

    @pytest.fixture
    async def real_session_factory(self):
        """创建内存 SQLite 引擎与会话工厂，建好 Images/Voices 表。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all, tables=[Images.__table__, Voices.__table__]
            )
        factory = async_sessionmaker(
            bind=engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            yield factory
        finally:
            await engine.dispose()

    @staticmethod
    def _patch_session(factory):
        """把 _repository.get_db_session 替换为使用真实 factory 的实现。"""

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

        return patch(
            "src.core.managers.media_manager.repository.get_db_session",
            lambda: _real_session(),
        )

    @pytest.mark.asyncio
    async def test_same_path_different_hash_updates_existing(
        self, real_session_factory
    ) -> None:
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_media_info(
                media_hash="hash_A",
                media_type="image",
                file_path="/media/img.jpg",
                description="first",
            )
            await manager.save_media_info(
                media_hash="hash_B",
                media_type="image",
                file_path="/media/img.jpg",
                description="second",
                vlm_processed=True,
            )

        async with real_session_factory() as s:
            rows = (
                (await s.execute(__import__("sqlalchemy").select(Images)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
            row = rows[0]
            assert row.path == "/media/img.jpg"
            assert row.image_id == "hash_B"
            assert row.count == 2
            assert row.description == "second"
            assert row.vlm_processed is True

    @pytest.mark.asyncio
    async def test_same_hash_second_call_increments_count(
        self, real_session_factory
    ) -> None:
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_media_info("hash_X", "image", "/m/x.png")
            await manager.save_media_info("hash_X", "image", "/m/x.png")
            await manager.save_media_info("hash_X", "image", "/m/x.png")

        async with real_session_factory() as s:
            rows = (
                (await s.execute(__import__("sqlalchemy").select(Images)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].count == 3

    @pytest.mark.asyncio
    async def test_distinct_paths_create_separate_rows(
        self, real_session_factory
    ) -> None:
        manager = self._make_manager()
        with self._patch_session(real_session_factory):
            await manager.save_media_info("hash_1", "image", "/m/a.png")
            await manager.save_media_info("hash_2", "image", "/m/b.png")

        async with real_session_factory() as s:
            rows = (
                (await s.execute(__import__("sqlalchemy").select(Images)))
                .scalars()
                .all()
            )
            assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_voice_same_path_different_hash_updates(
        self, real_session_factory
    ) -> None:
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
            rows = (
                (await s.execute(__import__("sqlalchemy").select(Voices)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
            row = rows[0]
            assert row.voice_id == "vh_B"
            assert row.path == "/media/v1.wav"
            assert row.count == 2
            assert row.description == "second"
            assert row.asr_processed is True


# ──────────────────────────────────────────
# GIF 关键帧提取测试
# ──────────────────────────────────────────


class TestGifKeyFrameExtraction:
    """测试 GIF 关键帧提取与拼接功能（静态方法转发的纯工具函数）。"""

    @staticmethod
    def _make_gif_base64(num_frames: int = 3, size: tuple[int, int] = (50, 50)) -> str:
        """生成多帧 GIF 的 base64 字符串用于测试。"""
        import io

        from PIL import Image as PILImage

        frames: list[PILImage.Image] = []
        for i in range(num_frames):
            frame = PILImage.new("RGB", size, (i * 30 + 10, 100, 200))
            frames.append(frame)

        buf = io.BytesIO()
        frames[0].save(
            buf,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def test_extract_gif_returns_png_base64(self) -> None:
        """提取多帧 GIF 应返回非空 PNG base64 字符串。"""
        gif_b64 = self._make_gif_base64(num_frames=4)
        result = extract_gif_key_frames(gif_b64, max_frames=8)

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0
        raw = base64.b64decode(result)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_extract_gif_single_frame(self) -> None:
        """单帧 GIF 应直接转为 PNG 返回。"""
        gif_b64 = self._make_gif_base64(num_frames=1)
        result = extract_gif_key_frames(gif_b64, max_frames=8)

        assert result is not None
        raw = base64.b64decode(result)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_extract_gif_sampling_max_frames(self) -> None:
        """帧数超过 max_frames 时应均匀采样到 max_frames 帧。"""
        gif_b64 = self._make_gif_base64(num_frames=20)

        with patch("src.core.managers.media_manager.utils.logger") as mock_log:
            result = extract_gif_key_frames(gif_b64, max_frames=4)
            mock_log.warning.assert_not_called()

        assert result is not None
        import io

        from PIL import Image as PILImage

        raw = base64.b64decode(result)
        img = PILImage.open(io.BytesIO(raw))
        assert img.width == 200
        assert img.height == 50

    def test_extract_gif_invalid_data_returns_none(self) -> None:
        """无效 base64 数据应返回 None 并记录警告。"""
        with patch("src.core.managers.media_manager.utils.logger") as mock_log:
            result = extract_gif_key_frames("invalid_base64_data!!!", max_frames=8)

        assert result is None
        mock_log.warning.assert_called_once()

    def test_extract_gif_non_gif_data_returns_single_frame_png(self) -> None:
        """非 GIF 数据（如 PNG）应被当作单帧图像处理，返回有效 PNG。"""
        import io

        from PIL import Image as PILImage

        buf = io.BytesIO()
        PILImage.new("RGB", (10, 10)).save(buf, format="PNG")
        png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        result = extract_gif_key_frames(png_b64, max_frames=8)

        assert result is not None
        raw = base64.b64decode(result)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        img = PILImage.open(io.BytesIO(raw))
        assert img.width == 10
        assert img.height == 10


# ──────────────────────────────────────────
# 提示词配置测试
# ──────────────────────────────────────────


class TestEmojiPromptConfig:
    """测试表情包识别提示词可配置功能。"""

    @staticmethod
    def _make_mock_core_config(
        image_prompt: str = "", emoji_prompt: str = ""
    ) -> MagicMock:
        mock_config = MagicMock()
        mock_chat = MagicMock()
        mock_chat.image_recognition_prompt = image_prompt
        mock_chat.emoji_recognition_prompt = emoji_prompt
        mock_config.chat = mock_chat
        return mock_config

    def test_emoji_prompt_uses_custom_config(self) -> None:
        """自定义表情包提示词时应使用配置值。"""
        mock_core_config = self._make_mock_core_config(
            image_prompt="custom image prompt", emoji_prompt="custom emoji prompt"
        )

        with (
            patch(
                "src.core.managers.media_manager.config.get_model_set_by_task",
                return_value=None,
            ),
            patch(
                "src.core.managers.media_manager.config.get_core_config",
                return_value=mock_core_config,
            ),
            patch(
                "src.core.managers.media_manager.config.get_prompt_manager"
            ) as mock_pm,
        ):
            mock_manager = MagicMock()
            mock_pm.return_value = mock_manager

            MediaManager()

            assert mock_manager.register_template.call_count == 2
            emoji_template = mock_manager.register_template.call_args_list[1][0][0]
            assert emoji_template.template == "custom emoji prompt"

    def test_emoji_prompt_uses_default_when_empty(self) -> None:
        """表情包提示词配置为空时应使用内置默认值。"""
        mock_core_config = self._make_mock_core_config(image_prompt="", emoji_prompt="")

        with (
            patch(
                "src.core.managers.media_manager.config.get_model_set_by_task",
                return_value=None,
            ),
            patch(
                "src.core.managers.media_manager.config.get_core_config",
                return_value=mock_core_config,
            ),
            patch(
                "src.core.managers.media_manager.config.get_prompt_manager"
            ) as mock_pm,
        ):
            mock_manager = MagicMock()
            mock_pm.return_value = mock_manager

            MediaManager()

            assert mock_manager.register_template.call_count == 2
            emoji_template = mock_manager.register_template.call_args_list[1][0][0]
            assert (
                emoji_template.template
                == "描述这个表情包的画面内容。若有文字，完整转述。"
            )
