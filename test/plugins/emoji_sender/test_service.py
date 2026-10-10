"""emoji_sender 服务层测试。"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import chromadb
import pytest

from plugins.emoji_sender.action import CollectEmojiMemeAction, RefreshEmojiMemeAction
from plugins.emoji_sender.config import EmojiSenderConfig
from plugins.emoji_sender.plugin import EmojiSenderPlugin
from plugins.emoji_sender.service import EmojiSenderService, MemeCandidate
from plugins.emoji_sender.tool import SearchEmojiMemesTool
from src.app.plugin_system.api.storage_api import PluginDatabase
from src.app.plugin_system.types import ChatStream, Message, StreamContext
from src.core.managers.media_manager.cache import MediaCache
from src.core.managers.media_manager.utils import compute_media_hash
from src.core.managers.service_manager import ServiceManager
from src.core.models.sql_alchemy import ImageDescriptions, Images
from src.kernel.concurrency import get_task_manager


def _make_service(*, temperature: float = 0.12) -> EmojiSenderService:
    """创建一个带最小配置的 EmojiSenderService。"""
    config = EmojiSenderConfig()
    config.vector.temperature = temperature
    return EmojiSenderService(plugin=EmojiSenderPlugin(config=config))


def test_select_candidate_returns_best_when_temperature_disabled() -> None:
    """temperature <= 0 时应固定返回距离最近的候选。"""
    service = _make_service(temperature=0.0)
    candidates = [
        MemeCandidate("m2", "开心", "/tmp/2.png", "第二张", 0.18),
        MemeCandidate("m1", "开心", "/tmp/1.png", "第一张", 0.04),
    ]

    selected = service._select_candidate(candidates)

    assert selected is not None
    assert selected.meme_id == "m1"


def test_select_candidate_uses_temperature_weights() -> None:
    """temperature > 0 时应按距离权重调用随机采样。"""
    service = _make_service(temperature=0.2)
    candidates = [
        MemeCandidate("m2", "开心", "/tmp/2.png", "第二张", 0.18),
        MemeCandidate("m1", "开心", "/tmp/1.png", "第一张", 0.04),
        MemeCandidate("m3", "开心", "/tmp/3.png", "第三张", 0.31),
    ]

    with patch("plugins.emoji_sender.service.random.choices", return_value=[candidates[1]]) as choices_mock:
        selected = service._select_candidate(candidates)

    assert selected is candidates[1]
    ordered_candidates = choices_mock.call_args.kwargs["population"] if "population" in choices_mock.call_args.kwargs else choices_mock.call_args.args[0]
    weights = choices_mock.call_args.kwargs["weights"]

    assert [candidate.meme_id for candidate in ordered_candidates] == ["m1", "m2", "m3"]
    assert weights[0] > weights[1] > weights[2]


@pytest.mark.asyncio
async def test_search_best_samples_within_threshold() -> None:
    """阈值内存在多个候选时，应交给温度采样函数决定。"""
    service = _make_service(temperature=0.12)
    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.query = AsyncMock(
        return_value={
            "ids": [["m1:开心", "m2:开心", "m3:开心"]],
            "distances": [[0.04, 0.08, 0.42]],
            "metadatas": [[
                {"meme_id": "m1", "tag": "开心", "path": "/tmp/1.png", "description": "第一张"},
                {"meme_id": "m2", "tag": "开心", "path": "/tmp/2.png", "description": "第二张"},
                {"meme_id": "m3", "tag": "开心", "path": "/tmp/3.png", "description": "第三张"},
            ]],
        }
    )

    embedding_request = MagicMock()
    embedding_request.send = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))

    chosen = MemeCandidate("m2", "开心", "/tmp/2.png", "第二张", 0.08)

    with (
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_embedding_request", return_value=embedding_request),
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
        patch.object(service, "_select_candidate", return_value=chosen) as select_mock,
    ):
        result = await service.search_best("开心地笑", ["开心"])

    assert result is not None
    assert result["meme_id"] == "m2"
    assert result["fallback_used"] is False
    sampled_candidates = select_mock.call_args.args[0]
    assert [candidate.meme_id for candidate in sampled_candidates] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_search_best_uses_temperature_sampling_for_tagged_fallback() -> None:
    """阈值外但带有效标签时，fallback 也应走温度采样而不是固定第一名。"""
    service = _make_service(temperature=0.2)
    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.query = AsyncMock(
        return_value={
            "ids": [["m1:开心", "m2:开心"]],
            "distances": [[0.44, 0.49]],
            "metadatas": [[
                {"meme_id": "m1", "tag": "开心", "path": "/tmp/1.png", "description": "第一张"},
                {"meme_id": "m2", "tag": "开心", "path": "/tmp/2.png", "description": "第二张"},
            ]],
        }
    )

    embedding_request = MagicMock()
    embedding_request.send = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))
    chosen = MemeCandidate("m2", "开心", "/tmp/2.png", "第二张", 0.49)

    with (
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_embedding_request", return_value=embedding_request),
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
        patch.object(service, "_select_candidate", return_value=chosen) as select_mock,
    ):
        result = await service.search_best("开心地笑", ["开心"])

    assert result is not None
    assert result["meme_id"] == "m2"
    assert result["fallback_used"] is True
    sampled_candidates = select_mock.call_args.args[0]
    assert [candidate.meme_id for candidate in sampled_candidates] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_search_best_without_tags_still_requires_threshold_match() -> None:
    """未指定有效标签时，阈值外结果不应触发 fallback。"""
    service = _make_service(temperature=0.2)
    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.query = AsyncMock(
        return_value={
            "ids": [["m1:开心"]],
            "distances": [[0.44]],
            "metadatas": [[
                {"meme_id": "m1", "tag": "开心", "path": "/tmp/1.png", "description": "第一张"},
            ]],
        }
    )

    embedding_request = MagicMock()
    embedding_request.send = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))

    with (
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_embedding_request", return_value=embedding_request),
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
        patch.object(service, "_select_candidate") as select_mock,
    ):
        result = await service.search_best("开心地笑", None)

    assert result is None
    select_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_once_skips_alignment_when_storage_is_full(tmp_path: Any) -> None:
    """达到表情包上限时应直接跳过，避免周期任务执行重型对齐。"""
    service = _make_service()
    memes_dir = tmp_path / "memes"
    memes_dir.mkdir()
    (memes_dir / "exists.png").write_bytes(b"payload")
    service._cfg().storage.data_dir = str(memes_dir)
    service._cfg().storage.max_memes = 1

    with patch.object(service, "_align_data_dir_with_db", new=AsyncMock()) as align_mock:
        await service.ingest_once()

    align_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_job_advances_past_rejected_meme(
    ingest_service: EmojiSenderService,
) -> None:
    """连续调度应跨服务跳过拒绝素材，重建插件后可重新评估。"""
    service = ingest_service
    plugin = cast(EmojiSenderPlugin, service.plugin)
    ingest_job = cast(Callable[[], Awaitable[None]], plugin._ingest_job)
    manager = ServiceManager()
    manual_dir = service._manual_memes_dir()
    rejected_source = manual_dir / "a_rejected.png"
    duplicate_source = manual_dir / "b_rejected.png"
    accepted_source = manual_dir / "c_accepted.png"
    rejected_source.write_bytes(b"rejected")
    duplicate_source.write_bytes(b"rejected")
    accepted_source.write_bytes(b"accepted")
    config = service._cfg()

    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.get = AsyncMock(return_value={"ids": []})
    mock_vdb.add = AsyncMock()
    label_mock = AsyncMock(
        side_effect=[
            {"keep": False, "description": "", "emotion_tags": []},
            {"keep": True, "description": "开心的表情", "emotion_tags": ["开心"]},
        ]
    )

    with (
        patch("src.app.plugin_system.api.service_api._get_service_manager", return_value=manager),
        patch.object(manager, "get_service_class", return_value=EmojiSenderService),
        patch(
            "src.core.managers.get_plugin_manager",
            return_value=SimpleNamespace(get_plugin=lambda plugin_name: plugin),
        ),
        patch.object(EmojiSenderService, "_align_data_dir_with_db", new=AsyncMock()),
        patch.object(EmojiSenderService, "_vlm_decide_and_label", new=label_mock),
        patch.object(
            EmojiSenderService,
            "_compress_image_for_vlm",
            return_value=(b"vlm-image", "image/png", False),
        ) as compress_mock,
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch(
            "plugins.emoji_sender.service.create_embedding_request",
            return_value=_make_embedding_mocks(),
        ),
    ):
        await ingest_job()
        mock_vdb.add.assert_not_awaited()
        await ingest_job()
        another_service = EmojiSenderService(plugin=plugin)
        assert await another_service._pick_next_manual_meme_file() == accepted_source
        restarted_service = EmojiSenderService(plugin=EmojiSenderPlugin(config=config))
        assert await restarted_service._pick_next_manual_meme_file() == rejected_source

    assert [call.args[0] for call in compress_mock.call_args_list] == [b"rejected", b"accepted"]
    mock_vdb.add.assert_awaited_once()
    metadata = mock_vdb.add.call_args.kwargs["metadatas"][0]
    assert metadata["source_hash"] == service._sha256_bytes(b"accepted")
    assert rejected_source.exists()
    assert duplicate_source.exists()
    assert accepted_source.exists()


@pytest.fixture
def ingest_service(tmp_path: Path) -> EmojiSenderService:
    """创建使用独立素材目录的入库服务。"""
    service = _make_service()
    config = service._cfg()
    config.ingest.manual_memes_dir = str(tmp_path / "manual")
    config.ingest.sample_from_media_cache = False
    config.storage.data_dir = str(tmp_path / "memes")
    config.storage.max_memes = 0
    return service


@pytest.mark.asyncio
async def test_ingest_once_skips_previously_rejected_media_cache(
    ingest_service: EmojiSenderService, tmp_path: Path
) -> None:
    """随机缓存抽到已拒绝内容时不重复压缩或调用 VLM。"""
    service = ingest_service
    service._cfg().ingest.sample_from_media_cache = True
    source = tmp_path / "cached.png"
    source.write_bytes(b"cached-rejection")

    with (
        patch.object(service, "_align_data_dir_with_db", new=AsyncMock()),
        patch.object(service, "_already_ingested", new=AsyncMock(return_value=False)),
        patch.object(service, "_pick_random_media_cache_file", new=AsyncMock(return_value=source)),
        patch.object(
            service, "_vlm_decide_and_label", new=AsyncMock(return_value={"keep": False})
        ) as label_mock,
        patch.object(
            service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)
        ) as compress_mock,
    ):
        await service.ingest_once()
        await service.ingest_once()

    label_mock.assert_awaited_once()
    compress_mock.assert_called_once()
    assert source.exists()
    assert not list(service._data_dir().glob("*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["pending", "unreadable", "processed", "empty"])
async def test_ingest_once_selects_pending_media_cache_file(
    ingest_service: EmojiSenderService, tmp_path: Path, cache_state: str
) -> None:
    """缓存选图应跳过已处理内容，单轮只评估一张待处理图片。"""
    service = ingest_service
    service._cfg().ingest.sample_from_media_cache = True
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    rejected_source = cache_dir / "a_rejected.png"
    ingested_source = cache_dir / "b_ingested.png"
    pending_source = cache_dir / "c_pending.png"
    candidates: list[Path] = []
    if cache_state != "empty":
        rejected_source.write_bytes(b"rejected")
        ingested_source.write_bytes(b"ingested")
        candidates.extend([rejected_source, ingested_source])
        service._rejected_hashes.add(service._sha256_bytes(b"rejected"))
    if cache_state in ("pending", "unreadable"):
        pending_source.write_bytes(b"pending")
        candidates.append(pending_source)
        another_pending_source = cache_dir / "d_pending.png"
        another_pending_source.write_bytes(b"another-pending")
        candidates.append(another_pending_source)
    if cache_state == "unreadable":
        candidates.insert(0, cache_dir / "missing.png")

    with (
        patch.object(service, "_align_data_dir_with_db", new=AsyncMock()),
        patch.object(service, "_pick_next_manual_meme_file", new=AsyncMock(return_value=None)),
        patch.object(service, "_media_cache_dir", return_value=cache_dir),
        patch.object(service, "_list_meme_files", return_value=candidates),
        patch.object(
            service,
            "_already_ingested",
            new=AsyncMock(side_effect=lambda source_hash: source_hash == service._sha256_bytes(b"ingested")),
        ),
        patch("plugins.emoji_sender.service.random.shuffle"),
        patch("plugins.emoji_sender.service.random.choice", side_effect=lambda files: files[0]),
        patch.object(
            service, "_vlm_decide_and_label", new=AsyncMock(return_value={"keep": False})
        ) as label_mock,
        patch.object(
            service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)
        ) as compress_mock,
    ):
        await service.ingest_once()

    if cache_state in ("pending", "unreadable"):
        label_mock.assert_awaited_once()
        compress_mock.assert_called_once_with(b"pending", "image/png")
    else:
        label_mock.assert_not_awaited()
        compress_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_response", "expected_attempts"),
    [
        pytest.param('{"keep": false}', 1, id="explicit-rejection"),
        pytest.param(None, 2, id="model-failure"),
        pytest.param("not JSON", 2, id="invalid-json"),
        pytest.param("{}", 2, id="missing-decision"),
        pytest.param('{"keep": "false"}', 2, id="string-decision"),
        pytest.param('{"keep": 0}', 2, id="numeric-decision"),
        pytest.param('{"keep": null}', 2, id="null-decision"),
        pytest.param(
            '{"keep": true, "description": "", "emotion_tags": ["开心"]}',
            2,
            id="missing-description",
        ),
        pytest.param(
            '{"keep": true, "description": "test meme", "emotion_tags": ["unknown"]}',
            2,
            id="invalid-tags",
        ),
        pytest.param(
            '{"keep": true, "description": "test meme", "emotion_tags": "开心"}',
            2,
            id="invalid-tag-type",
        ),
    ],
)
async def test_ingest_once_only_skips_explicit_rejection(
    ingest_service: EmojiSenderService, raw_response: str | None, expected_attempts: int
) -> None:
    """只有明确拒绝才跳过后续评估，失败和不完整标注应重试。"""
    service = ingest_service
    source = service._manual_memes_dir() / "candidate.png"
    source.write_bytes(b"candidate")
    request = MagicMock()
    request.send = AsyncMock()
    if raw_response is None:
        request.send.side_effect = RuntimeError("test model failure")
    else:
        response: Any = asyncio.get_running_loop().create_future()
        response.message = raw_response
        response.set_result(None)
        request.send.return_value = response

    with (
        patch.object(service, "_align_data_dir_with_db", new=AsyncMock()),
        patch.object(service, "_already_ingested", new=AsyncMock(return_value=False)),
        patch.object(service, "_build_persona_prompt", return_value="test persona"),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_llm_request", return_value=request),
    ):
        await service.ingest_once()
        await service.ingest_once()

    assert request.send.await_count == expected_attempts
    rejected_hash = service._sha256_bytes(b"candidate")
    assert (rejected_hash in service._rejected_hashes) == (expected_attempts == 1)
    assert source.exists()
    assert not list(service._data_dir().glob("*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_prompt", ["", "请重点检查图中文字，不要沿用旧描述"])
async def test_vlm_labels_requested_meme_without_collection_decision(extra_prompt: str) -> None:
    """主动收藏与重新识别只生成标注，并向 VLM 传递可选提示。"""
    service = _make_service()
    response: Any = asyncio.get_running_loop().create_future()
    response.message = '{"description": "假装生气的表情", "emotion_tags": ["生气", "害羞"]}'
    response.set_result(None)
    request = MagicMock()
    request.send = AsyncMock(return_value=response)
    prompts: list[str] = []

    with (
        patch.object(service, "_build_persona_prompt", return_value="test persona"),
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_llm_request", return_value=request),
        patch("src.kernel.llm.Text", side_effect=lambda text: prompts.append(text) or text),
    ):
        labeled = await service._vlm_decide_and_label(
            image_base64="aW1hZ2U=",
            mime="image/png",
            decide_collection=False,
            extra_prompt=extra_prompt,
        )

    assert labeled == {
        "keep": True,
        "description": "假装生气的表情",
        "emotion_tags": ["生气", "害羞"],
    }
    request.send.assert_awaited_once()
    assert "是否愿意把它收藏" not in prompts[0]
    if extra_prompt:
        assert extra_prompt in prompts[0]


@pytest.fixture
def meme_collection() -> Any:
    """创建测试专用内存图库并在测试结束后清理。"""
    client = chromadb.EphemeralClient()
    collection = client.create_collection("emoji_sender_tool_test")
    try:
        yield collection
    finally:
        client.delete_collection(collection.name)


@pytest.fixture
async def media_database(tmp_path: Path) -> AsyncIterator[PluginDatabase]:
    """将媒体查询和公开数据库接口绑定到隔离的临时数据库。"""
    database = PluginDatabase(str(tmp_path / "media.db"), [Images, ImageDescriptions])
    await database.initialize()

    async def get_by(model: type[Any], **filters: Any) -> Any:
        """通过真实 CRUD 查询测试数据库。"""
        return await database.crud(model).get_by(**filters)

    async def create(model: type[Any], obj_in: dict[str, Any]) -> Any:
        """通过真实 CRUD 创建测试记录。"""
        return await database.crud(model).create(obj_in)

    async def update(model: type[Any], id: int, obj_in: dict[str, Any]) -> Any:
        """通过真实 CRUD 更新测试记录并失效查询缓存。"""
        return await database.crud(model).update(id=id, obj_in=obj_in)

    async def delete(model: type[Any], id: int) -> bool:
        """通过真实 CRUD 删除测试记录。"""
        return await database.crud(model).delete(id=id)

    async def get_media_info(media_id: str) -> dict[str, Any] | None:
        """返回测试数据库中图片媒体记录的完整字段。"""
        media = await database.crud(Images).get_by(image_id=media_id)
        if media is None:
            return None
        return {column.name: getattr(media, column.name) for column in Images.__table__.columns}

    try:
        with (
            patch("plugins.emoji_sender.service.database_api.get_by", new=get_by),
            patch("plugins.emoji_sender.service.database_api.create", new=create),
            patch("plugins.emoji_sender.service.database_api.update", new=update),
            patch("plugins.emoji_sender.service.database_api.delete", new=delete),
            patch("plugins.emoji_sender.service.get_media_info", new=get_media_info),
        ):
            yield database
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "note", ["", "这是把认真工作和放空的反差做成的梗。\n" + "适合表达已经走神但还在装作专注，不是在指责别人不认真。" * 20],
    ids=["without-note", "long-note"],
)
async def test_collect_meme_stores_original_and_skips_duplicate(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any, note: str
) -> None:
    """收藏保留原图和完整备注，重复调用不再识别或覆盖备注。"""
    service = ingest_service
    source = tmp_path / "source.gif"
    source.write_bytes(b"original-gif")
    meme_id = service._sha256_bytes(b"original-gif")
    media_id = compute_media_hash(base64.b64encode(b"original-gif").decode("ascii"))
    service._rejected_hashes.add(meme_id)
    vdb = SimpleNamespace(
        get_or_create_collection=AsyncMock(return_value=meme_collection),
        query=AsyncMock(side_effect=lambda **params: meme_collection.query(
            query_embeddings=params["query_embeddings"],
            n_results=min(params["n_results"], meme_collection.count()), where=params["where"],
        )),
    )
    label = {"keep": True, "description": "假装乖巧", "emotion_tags": ["害羞", "开心"]}
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch(
            "plugins.emoji_sender.service.get_media_info",
            new=AsyncMock(return_value={"type": "emoji", "path": str(source)}),
        ),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", True)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value=label)) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock(return_value=[0.1, 0.2, 0.3])),
    ):
        ok, result = await service.collect_meme(media_id=media_id, note=note)
        duplicate_ok, duplicate_result = await service.collect_meme(media_id=media_id, note="不应覆盖的备注")
        candidates = await service.search_candidates(description_query="假装乖巧")

    assert ok and duplicate_ok
    assert meme_id in result and "已经收藏" in duplicate_result
    vlm.assert_awaited_once()
    assert vlm.call_args.kwargs["decide_collection"] is False
    assert meme_id not in service._rejected_hashes
    assert source.read_bytes() == b"original-gif"
    assert (service._data_dir() / f"{meme_id}.gif").read_bytes() == b"original-gif"
    records = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    assert set(records["ids"]) == {f"{meme_id}:害羞", f"{meme_id}:开心"}
    assert records["documents"] == ["假装乖巧", "假装乖巧"]
    assert all(metadata["media_id"] == media_id for metadata in records["metadatas"])
    assert all(metadata.get("note", "") == note for metadata in records["metadatas"])
    assert all("note" in metadata for metadata in records["metadatas"]) == bool(note)
    assert all(embedding == pytest.approx([0.1, 0.2, 0.3]) for embedding in records["embeddings"].tolist())
    assert candidates is not None and candidates["total"] == 1
    assert candidates["candidates"][0].get("note", "") == note
    assert ("note" in candidates["candidates"][0]) == bool(note)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["unknown-media", "wrong-type", "missing-file", "wrong-hash", "full", "vlm", "embedding", "write", "invalid-id"],
)
async def test_collect_meme_failure_does_not_create_collection_entry(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any, failure: str
) -> None:
    """主动收藏失败不写入记录、不留下新副本，也不删除来源文件。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    meme_id = service._sha256_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    media_info: dict[str, Any] | None = {"type": "emoji", "path": str(source)}
    if failure == "unknown-media":
        media_info = None
    elif failure == "wrong-type":
        media_info = {"type": "voice", "path": str(source)}
    elif failure == "missing-file":
        media_info = {"type": "emoji", "path": str(tmp_path / "missing.png")}
    elif failure == "wrong-hash":
        media_id = "b" * 64
    elif failure == "invalid-id":
        media_id = "not-a-media-id"
    elif failure == "full":
        service._cfg().storage.max_memes = 1
        service._data_dir().mkdir()
        (service._data_dir() / "existing.png").write_bytes(b"existing")
    label = {"keep": True, "description": "test meme", "emotion_tags": ["开心"]}
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock(return_value=media_info)),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value=None if failure == "vlm" else label)),
        patch.object(service, "_embed_query", new=AsyncMock(return_value=None if failure == "embedding" else [0.1, 0.2, 0.3])),
        patch.object(service, "_store_meme_labels", wraps=service._store_meme_labels) as store,
    ):
        if failure == "write":
            store.side_effect = RuntimeError("test storage failure")
        ok, _ = await service.collect_meme(media_id=media_id)

    assert not ok
    assert meme_collection.count() == 0
    assert source.read_bytes() == b"original-image"
    assert not (service._data_dir() / f"{meme_id}.png").exists()


@pytest.mark.asyncio
async def test_collection_actions_complete_real_media_id_workflow(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any, media_database: PluginDatabase
) -> None:
    """两个动作使用真实聊天 ID 完成收藏和带提示的重新识别。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    meme_id = service._sha256_bytes(b"original-image")
    await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": str(source),
        "description": "生气", "timestamp": 1.0, "vlm_processed": True,
    })
    context = StreamContext(stream_id="test-stream")
    context.unread_messages.append(
        Message(content={"media": [{"type": "emoji", "image_id": media_id}]})
    )
    stream = cast(ChatStream, SimpleNamespace(context=context))
    plugin = cast(EmojiSenderPlugin, service.plugin)
    collect = CollectEmojiMemeAction(stream, plugin)
    refresh = RefreshEmojiMemeAction(stream, plugin)
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch("plugins.emoji_sender.action.get_service", return_value=service),
        patch.object(service, "_vector_db", return_value=vdb),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(
            service, "_vlm_decide_and_label", new=AsyncMock(side_effect=[
                {"keep": True, "description": "生气", "emotion_tags": ["生气"]},
                {"keep": True, "description": "假装生气撒娇", "emotion_tags": ["害羞"]},
            ])
        ) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock(return_value=[0.1, 0.2, 0.3])) as embedding,
    ):
        collected, receipt = await collect.execute(media_id=media_id, note="用于轻松语境中的自嘲，不是指责对方。")
        refreshed, _ = await refresh.execute(meme_id=meme_id, extra_prompt="请核对是否在撒娇")
        assert meme_collection.get(include=["metadatas"])["metadatas"][0]["note"] == "用于轻松语境中的自嘲，不是指责对方。"
        edited, _ = await refresh.execute(meme_id=meme_id[:12], note="补充理解：这种假装生气的表情适合熟人之间开玩笑。", note_only=True)

    assert collected and refreshed and edited and meme_id in receipt
    assert vlm.await_count == 2
    assert embedding.await_count == 2
    assert vlm.call_args.kwargs["extra_prompt"] == "请核对是否在撒娇"
    records = meme_collection.get(include=["documents", "metadatas"])
    assert records["ids"] == [f"{meme_id}:害羞"]
    assert records["documents"] == ["假装生气撒娇"]
    assert records["metadatas"][0]["note"] == "补充理解：这种假装生气的表情适合熟人之间开玩笑。"
    assert source.read_bytes() == b"original-image"
    cached = await media_database.crud(ImageDescriptions).get_by(image_description_hash=media_id, type="emoji")
    assert cached is not None and cached.description == "假装生气撒娇"


@pytest.mark.asyncio
async def test_collect_meme_detects_existing_background_collection(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any
) -> None:
    """后台已有内容哈希记录时，主动收藏不重复调用模型或增加记录。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    meme_id = service._sha256_bytes(b"original-image")
    meme_collection.add(
        ids=[f"{meme_id}:开心"], embeddings=[[0.1, 0.2, 0.3]], documents=["已有表情"],
        metadatas=[{"meme_id": meme_id, "path": str(source), "tag": "开心", "description": "已有表情"}],
    )
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock(return_value={"type": "emoji", "path": str(source)})),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock()) as vlm,
    ):
        ok, result = await service.collect_meme(media_id=media_id, note="重复收藏不应添加备注")

    assert ok and "已经收藏" in result and meme_id in result
    assert meme_collection.count() == 1
    assert "note" not in meme_collection.get(include=["metadatas"])["metadatas"][0]
    vlm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("note", ["这是一个需要记住前因后果的梗。\n" + "认真和走神的反差才是笑点，可以用来自嘲，不是在批评对方。" * 20, ""], ids=["long-note", "clear"])
@pytest.mark.parametrize("has_note", [True, False], ids=["existing-note", "legacy"])
@pytest.mark.parametrize("identifier", ["full", "short", "media"])
async def test_refresh_note_only_preserves_labels_vectors_and_media(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    note: str, has_note: bool, identifier: str,
) -> None:
    """原图缺失时仍可修改全部标签的备注，不调用模型、媒体接口或改向量。"""
    service = ingest_service
    meme_id = service._sha256_bytes(b"missing-original")
    media_id = compute_media_hash(base64.b64encode(b"missing-original").decode("ascii"))
    tags = ["开心", "害羞"]
    meme_collection.add(
        ids=[f"{meme_id}:{tag}" for tag in tags], embeddings=[[0.1, 0.2, 0.3]] * 2,
        documents=["原描述"] * 2,
        metadatas=[{
            "meme_id": meme_id, "media_id": media_id, "tag": tag,
            "path": str(tmp_path / "missing.png"), "description": "原描述", "created_at": 1.0,
            **({"note": "旧备注"} if has_note else {}),
        } for tag in tags],
    )
    before = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    requested_id = {"full": meme_id, "short": meme_id[:12], "media": media_id}[identifier]
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch.object(service, "_read_file_with_hash") as read_file,
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock()) as media,
        patch("plugins.emoji_sender.service.database_api.get_by", new=AsyncMock()) as database,
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock()) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock()) as embedding,
    ):
        ok, result = await service.refresh_meme(meme_id=requested_id, note=note, note_only=True)

    assert ok and "备注" in result and meme_id in result
    read_file.assert_not_called()
    media.assert_not_awaited()
    database.assert_not_awaited()
    vlm.assert_not_awaited()
    embedding.assert_not_awaited()
    after = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    assert after["ids"] == before["ids"]
    assert after["documents"] == before["documents"]
    assert after["embeddings"].tolist() == before["embeddings"].tolist()
    assert after["metadatas"] == [{**metadata, "note": note} for metadata in before["metadatas"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing-note", "extra-prompt", "unknown", "ambiguous", "unavailable", "write"])
async def test_refresh_note_only_rejects_invalid_requests_without_side_effects(
    ingest_service: EmojiSenderService, meme_collection: Any, failure: str,
) -> None:
    """缺少备注、参数冲突、收藏缺失或不唯一及库不可用时不修改数据。"""
    service = ingest_service
    meme_id = "a" * 64
    meme_collection.add(
        ids=[f"{meme_id}:开心"], embeddings=[[0.1, 0.2, 0.3]], documents=["原描述"],
        metadatas=[{"meme_id": meme_id, "tag": "开心", "path": "missing.png", "note": "原备注"}],
    )
    if failure == "ambiguous":
        other_id = "a" * 63 + "b"
        meme_collection.add(
            ids=[f"{other_id}:开心"], embeddings=[[0.1, 0.2, 0.3]], documents=["另一张"],
            metadatas=[{"meme_id": other_id, "tag": "开心", "path": "missing.png", "note": "另一条"}],
        )
    before = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=None if failure == "unavailable" else meme_collection))
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock()) as media,
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock()) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock()) as embedding,
        patch.object(type(meme_collection), "update", side_effect=RuntimeError("test note write failure") if failure == "write" else meme_collection.update),
    ):
        ok, _ = await service.refresh_meme(
            meme_id="b" * 64 if failure == "unknown" else meme_id[:12] if failure == "ambiguous" else meme_id,
            note=None if failure == "missing-note" else "新备注", note_only=True,
            extra_prompt="重新识别" if failure == "extra-prompt" else "",
        )

    assert not ok
    media.assert_not_awaited()
    vlm.assert_not_awaited()
    embedding.assert_not_awaited()
    after = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    assert after["ids"] == before["ids"]
    assert after["metadatas"] == before["metadatas"]
    assert after["documents"] == before["documents"]
    assert after["embeddings"].tolist() == before["embeddings"].tolist()


@pytest.mark.asyncio
@pytest.mark.parametrize("note_only", [True, False])
@pytest.mark.parametrize("available", [True, False], ids=["uncollected", "unavailable"])
async def test_refresh_rejects_note_without_available_collection(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    note_only: bool, available: bool,
) -> None:
    """未收藏或图库不可用时明确拒绝备注，不丢弃输入后继续识别或自动收藏。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"uncollected-image")
    media_id = compute_media_hash(base64.b64encode(b"uncollected-image").decode("ascii"))
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection if available else None))
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock(return_value={"type": "image", "path": str(source)})),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock()) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock()) as embedding,
        patch.object(service, "_commit_refresh", new=AsyncMock()) as commit,
    ):
        ok, result = await service.refresh_meme(meme_id=media_id, note="不要丢弃这段备注", note_only=note_only)

    assert not ok and "备注" in result
    vlm.assert_not_awaited()
    embedding.assert_not_awaited()
    commit.assert_not_awaited()
    assert meme_collection.count() == 0
    assert source.read_bytes() == b"uncollected-image"
    assert not service._data_dir().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["image", "emoji"])
@pytest.mark.parametrize("has_cache", [True, False])
async def test_refresh_uncollected_media_without_embedding_or_collection(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase, media_type: str, has_cache: bool
) -> None:
    """未收藏的聊天图片可重新识别，不生成检索向量或自动收藏。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    media = await media_database.crud(Images).create({
        "image_id": media_id, "type": media_type, "path": str(source),
        "description": "旧描述", "timestamp": 1.0, "count": 4, "vlm_processed": True,
    })
    if has_cache:
        await media_database.crud(ImageDescriptions).create({
            "image_description_hash": media_id, "type": media_type,
            "description": "旧描述", "timestamp": 1.0,
        })
    with patch("src.core.managers.media_manager.cache.QueryBuilder", side_effect=media_database.query):
        assert await MediaCache().get_cached_description(media_id, media_type) == ("旧描述" if has_cache else None)
    label = {"keep": True, "description": "假装生气撒娇", "emotion_tags": ["害羞"]}
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value=label)) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock()) as embedding,
    ):
        ok, result = await service.refresh_meme(meme_id=media_id, extra_prompt="请核对是否在撒娇")

    assert ok, result
    assert label["description"] in result
    assert vlm.call_args.kwargs["extra_prompt"] == "请核对是否在撒娇"
    assert vlm.call_args.kwargs["decide_collection"] is False
    embedding.assert_not_awaited()
    assert meme_collection.count() == 0
    assert source.read_bytes() == b"original-image"
    assert not service._data_dir().exists()
    updated = await media_database.crud(Images).get(id=media.id)
    assert updated is not None and updated.description == label["description"] and updated.count == 4
    cached = await media_database.crud(ImageDescriptions).get_by(image_description_hash=media_id, type=media_type)
    assert cached is not None and cached.description == label["description"] and cached.timestamp > 1.0
    assert await media_database.crud(ImageDescriptions).count() == 1
    with patch("src.core.managers.media_manager.cache.QueryBuilder", side_effect=media_database.query):
        assert await MediaCache().get_cached_description(media_id, media_type) == label["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing-file", "missing-path", "wrong-hash", "vlm", "cache-write"])
async def test_refresh_uncollected_media_failure_preserves_description(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase, failure: str
) -> None:
    """缺图、错误文件、识别或缓存写入失败时保留真实数据库中的旧值。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    media = await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": "" if failure == "missing-path" else str(source),
        "description": "旧描述", "timestamp": 1.0, "vlm_processed": False,
    })
    cached = await media_database.crud(ImageDescriptions).create({
        "image_description_hash": media_id, "type": "emoji", "description": "旧描述", "timestamp": 1.0,
    })
    if failure == "missing-file":
        source.unlink()
    elif failure == "wrong-hash":
        source.write_bytes(b"wrong-image")
    label = {"keep": True, "description": "新描述", "emotion_tags": ["害羞"]}
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))

    async def update(model: type[Any], id: int, obj_in: dict[str, Any]) -> Any:
        """仅使描述缓存写入失败，媒体记录仍能恢复。"""
        if failure == "cache-write" and model is ImageDescriptions:
            raise RuntimeError("test cache write failure")
        return await media_database.crud(model).update(id=id, obj_in=obj_in)

    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value=None if failure == "vlm" else label)) as vlm,
        patch("plugins.emoji_sender.service.database_api.update", new=update),
    ):
        ok, result = await service.refresh_meme(meme_id=media_id)

    assert not ok
    updated_media = await media_database.crud(Images).get(id=media.id)
    updated_cache = await media_database.crud(ImageDescriptions).get(id=cached.id)
    assert updated_media is not None and updated_media.description == "旧描述" and not updated_media.vlm_processed
    assert updated_cache is not None and updated_cache.description == "旧描述" and updated_cache.timestamp == 1.0
    assert meme_collection.count() == 0
    if failure in {"missing-file", "missing-path", "wrong-hash"}:
        vlm.assert_not_awaited()
    if failure in {"missing-file", "missing-path"}:
        assert "原文件已不可用" in result and "重新发送" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["legacy-record", "legacy-record-copy-cleaned", "chat-cache-cleaned", "collection-copy-cleaned", "cache-write-fails"])
async def test_refresh_chat_media_updates_existing_collection(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase, source_state: str
) -> None:
    """聊天 ID 与收藏库 ID 都可同步刷新，任一合法副本可用于识别。"""
    service = ingest_service
    source = tmp_path / "source.png"
    stored = tmp_path / "stored.png"
    source.write_bytes(b"original-image")
    stored.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    meme_id = service._sha256_bytes(b"original-image")
    await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": str(source), "description": "旧描述",
        "timestamp": 1.0, "vlm_processed": True,
    })
    await media_database.crud(ImageDescriptions).create({
        "image_description_hash": media_id, "type": "emoji", "description": "旧描述", "timestamp": 1.0,
    })
    metadata = {"meme_id": meme_id, "path": str(stored), "description": "旧描述", "tag": "生气"}
    metadata["source_cache_path"] = str(source)
    if source_state not in {"legacy-record", "legacy-record-copy-cleaned"}:
        metadata["media_id"] = media_id
    meme_collection.add(
        ids=[f"{meme_id}:生气"], embeddings=[[0.9, 0.8, 0.7]], documents=["旧描述"], metadatas=[metadata],
    )
    if source_state == "chat-cache-cleaned":
        source.unlink()
    elif source_state in {"collection-copy-cleaned", "legacy-record-copy-cleaned"}:
        stored.unlink()
    before = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))

    async def update(model: type[Any], id: int, obj_in: dict[str, Any]) -> Any:
        """在指定场景拒绝描述缓存更新，其余写入使用真实数据库。"""
        if source_state == "cache-write-fails" and model is ImageDescriptions:
            raise RuntimeError("test cache write failure")
        return await media_database.crud(model).update(id=id, obj_in=obj_in)

    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value={
            "keep": True, "description": "新描述", "emotion_tags": ["害羞"],
        })) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock(return_value=[0.1, 0.2, 0.3])),
        patch("plugins.emoji_sender.service.database_api.update", new=update),
    ):
        requested_id = meme_id if source_state == "legacy-record-copy-cleaned" else media_id
        ok, result = await service.refresh_meme(meme_id=requested_id, extra_prompt="请核对图中文字")

    vlm.assert_awaited_once()
    cached = await media_database.crud(ImageDescriptions).get_by(image_description_hash=media_id, type="emoji")
    records = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    if source_state == "cache-write-fails":
        assert not ok
        assert cached is not None and cached.description == "旧描述"
        assert records["ids"] == before["ids"] and records["metadatas"] == before["metadatas"]
        assert records["documents"] == before["documents"]
        assert records["embeddings"].tolist() == before["embeddings"].tolist()
    else:
        assert ok, result
        assert cached is not None and cached.description == "新描述"
        assert records["ids"] == [f"{meme_id}:害羞"] and records["documents"] == ["新描述"]


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["history", "unread", "current", "extra", "other-chat"])
async def test_refresh_action_only_updates_uncollected_media_in_current_chat(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase, location: str
) -> None:
    """重新识别入口只允许当前上下文中的未收藏图片，并转发复核提示。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": str(source), "description": "旧描述",
        "timestamp": 1.0, "vlm_processed": True,
    })
    context = StreamContext(stream_id="test-stream")
    media_content = {"media": [{"type": "emoji", "image_id": media_id}]}
    message = Message(media=media_content["media"]) if location == "extra" else Message(content=media_content)
    if location in {"unread", "extra"}:
        context.unread_messages.append(message)
    elif location == "history":
        context.history_messages.append(message)
    elif location == "current":
        context.current_message = message
    stream = cast(ChatStream, SimpleNamespace(context=context))
    action = RefreshEmojiMemeAction(stream, cast(EmojiSenderPlugin, service.plugin))
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch("plugins.emoji_sender.action.get_service", return_value=service),
        patch.object(service, "_vector_db", return_value=vdb),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value={
            "keep": True, "description": "新描述", "emotion_tags": ["害羞"],
        })) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock()) as embedding,
    ):
        ok, result = await action.execute(meme_id=media_id, extra_prompt="请核对图中文字")

    assert ok is (location != "other-chat"), result
    if location == "other-chat":
        vlm.assert_not_awaited()
        assert "当前聊天上下文中没有" in result
    else:
        vlm.assert_awaited_once()
        assert vlm.call_args.kwargs["extra_prompt"] == "请核对图中文字"
    embedding.assert_not_awaited()
    assert meme_collection.count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("collected", [False, True])
@pytest.mark.parametrize("failure", ["unavailable", "open-error", "lookup-error"])
async def test_refresh_without_vector_db_defers_existing_collection_sync(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase, collected: bool, failure: str,
) -> None:
    """收藏库故障不阻止聊天图片刷新，恢复后只同步已有收藏。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    meme_id = service._sha256_bytes(b"original-image")
    media = await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": str(source),
        "description": "旧描述", "timestamp": 1.0, "vlm_processed": True,
    })
    await media_database.crud(ImageDescriptions).create({
        "image_description_hash": media_id, "type": "emoji", "description": "旧描述", "timestamp": 1.0,
    })
    if collected:
        meme_collection.add(
            ids=[f"{meme_id}:生气"], embeddings=[[0.9, 0.8, 0.7]], documents=["旧描述"],
            metadatas=[{"meme_id": meme_id, "path": str(source), "tag": "生气", "description": "旧描述"}],
        )
    unavailable = SimpleNamespace(get_or_create_collection=AsyncMock(
        return_value=meme_collection if failure == "lookup-error" else None,
        side_effect=RuntimeError("test vector open failure") if failure == "open-error" else None,
    ))
    available = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    label = {"description": "新描述", "emotion_tags": ["害羞"], "keep": True}
    with (
        patch.object(service, "_vector_db", return_value=unavailable),
        patch.object(type(meme_collection), "get", side_effect=RuntimeError("test vector lookup failure")),
        patch.object(service, "_recognize_requested_meme", new=AsyncMock(return_value=label)) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock()) as embedding,
    ):
        ok, result = await service.refresh_meme(meme_id=media_id, allow_chat_media=True)
    assert ok and "恢复后" in result
    vlm.assert_awaited_once()
    embedding.assert_not_awaited()
    updated = await media_database.crud(Images).get(id=media.id)
    cached = await media_database.crud(ImageDescriptions).get_by(image_description_hash=media_id, type="emoji")
    assert updated is not None and updated.description == "新描述"
    assert cached is not None and cached.description == "新描述"
    assert list(service._refresh_journal_dir().glob("*.json"))
    resumed = EmojiSenderService(plugin=service.plugin)
    with (
        patch.object(resumed, "_vector_db", return_value=available),
        patch.object(resumed, "_embed_query", new=AsyncMock(return_value=[0.1, 0.2, 0.3])) as embedding,
        patch.object(resumed, "_align_data_dir_with_db", new=AsyncMock()),
        patch.object(resumed, "_pick_next_manual_meme_file", new=AsyncMock(return_value=None)),
    ):
        await resumed.ingest_once()
    assert not resumed._refresh_journal_dir().exists()
    records = meme_collection.get(include=["documents"])
    assert records["ids"] == ([f"{meme_id}:害羞"] if collected else [])
    assert records["documents"] == (["新描述"] if collected else [])
    assert embedding.await_count == int(collected)
    assert source.read_bytes() == b"original-image"
    assert not service._data_dir().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_chat_media,length", [(False, 64), (True, 12)])
async def test_refresh_without_vector_db_rejects_unauthorized_or_short_ids(
    ingest_service: EmojiSenderService, allow_chat_media: bool, length: int,
) -> None:
    """离线刷新不接受其他会话媒体或无法解析的收藏短 ID。"""
    unavailable = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=None))
    with (
        patch.object(ingest_service, "_vector_db", return_value=unavailable),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock()) as media,
        patch.object(ingest_service, "_recognize_requested_meme", new=AsyncMock()) as vlm,
    ):
        ok, _ = await ingest_service.refresh_meme(meme_id="a" * length, allow_chat_media=allow_chat_media)
    assert not ok
    media.assert_not_awaited()
    vlm.assert_not_awaited()
    assert not ingest_service._refresh_journal_dir().exists()


@pytest.mark.asyncio
async def test_refresh_cancellation_waits_for_consistent_commit(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase,
) -> None:
    """调用方重复取消时等待保存完成，不留下半更新的媒体和收藏。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    meme_id = service._sha256_bytes(b"original-image")
    media = await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": str(source),
        "description": "旧描述", "timestamp": 1.0, "vlm_processed": True,
    })
    await media_database.crud(ImageDescriptions).create({
        "image_description_hash": media_id, "type": "emoji", "description": "旧描述", "timestamp": 1.0,
    })
    metadata = {"meme_id": meme_id, "path": str(source), "description": "旧描述", "tag": "生气"}
    meme_collection.add(
        ids=[f"{meme_id}:生气"], embeddings=[[0.9, 0.8, 0.7]], documents=["旧描述"], metadatas=[metadata],
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    store = service._store_meme_labels

    async def paused_store(*args: Any, **kwargs: Any) -> list[str]:
        """收藏写入后暂停，让调用方在媒体写入前发出取消。"""
        ids = await store(*args, **kwargs)
        entered.set()
        await release.wait()
        return ids

    available = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    with (
        patch.object(service, "_vector_db", return_value=available),
        patch.object(service, "_label_requested_meme", new=AsyncMock(return_value=(
            {"description": "新描述", "emotion_tags": ["害羞"]}, [0.1, 0.2, 0.3],
        ))),
        patch.object(service, "_store_meme_labels", new=paused_store),
    ):
        task = get_task_manager().create_task(service.refresh_meme(meme_id=meme_id)).task
        assert task is not None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    updated = await media_database.crud(Images).get(id=media.id)
    cached = await media_database.crud(ImageDescriptions).get_by(image_description_hash=media_id, type="emoji")
    assert updated is not None and updated.description == "新描述"
    assert cached is not None and cached.description == "新描述"
    records = meme_collection.get(include=["documents"])
    assert records["ids"] == [f"{meme_id}:害羞"] and records["documents"] == ["新描述"]
    assert not service._refresh_journal_dir().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_cache", [False, True])
@pytest.mark.parametrize("interruption", ["after-vector", "after-images", "after-cache"])
async def test_refresh_interrupted_commit_recovers_after_service_recreation(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any,
    media_database: PluginDatabase, interruption: str, has_cache: bool,
) -> None:
    """保存被中断后，新服务在入库前恢复真实 SQL 与 Chroma 的旧值。"""
    service = ingest_service
    source = tmp_path / "source.png"
    source.write_bytes(b"original-image")
    media_id = compute_media_hash(base64.b64encode(b"original-image").decode("ascii"))
    meme_id = service._sha256_bytes(b"original-image")
    media = await media_database.crud(Images).create({
        "image_id": media_id, "type": "emoji", "path": str(source),
        "description": "旧描述", "timestamp": 1.0, "vlm_processed": False,
    })
    if has_cache:
        await media_database.crud(ImageDescriptions).create({
            "image_description_hash": media_id, "type": "emoji", "description": "旧描述", "timestamp": 1.0,
        })
    metadata = {"meme_id": meme_id, "path": str(source), "description": "旧描述", "tag": "生气"}
    meme_collection.add(
        ids=[f"{meme_id}:生气"], embeddings=[[0.9, 0.8, 0.7]], documents=["旧描述"], metadatas=[metadata],
    )
    before = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    available = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))

    async def interrupted_update(model: type[Any], id: int, obj_in: dict[str, Any]) -> Any:
        """在指定提交边界中断保存，保留已经提交的真实数据。"""
        if model is Images and interruption == "after-vector":
            raise asyncio.CancelledError
        if model is ImageDescriptions and interruption == "after-images":
            raise asyncio.CancelledError
        updated = await media_database.crud(model).update(id=id, obj_in=obj_in)
        if model is ImageDescriptions and interruption == "after-cache":
            raise asyncio.CancelledError
        return updated

    async def interrupted_create(model: type[Any], obj_in: dict[str, Any]) -> Any:
        """中断原本没有识别缓存的创建操作。"""
        if model is ImageDescriptions and interruption == "after-images":
            raise asyncio.CancelledError
        created = await media_database.crud(model).create(obj_in)
        if model is ImageDescriptions and interruption == "after-cache":
            raise asyncio.CancelledError
        return created

    with (
        patch.object(service, "_vector_db", return_value=available),
        patch.object(service, "_label_requested_meme", new=AsyncMock(return_value=(
            {"description": "新描述", "emotion_tags": ["害羞"]}, [0.1, 0.2, 0.3],
        ))),
        patch("plugins.emoji_sender.service.database_api.update", new=interrupted_update),
        patch("plugins.emoji_sender.service.database_api.create", new=interrupted_create),
        pytest.raises(asyncio.CancelledError),
    ):
        await service.refresh_meme(meme_id=meme_id)
    assert list(service._refresh_journal_dir().glob("*.json"))
    resumed = EmojiSenderService(plugin=service.plugin)
    with (
        patch.object(resumed, "_vector_db", return_value=available),
        patch.object(resumed, "_align_data_dir_with_db", new=AsyncMock()),
        patch.object(resumed, "_pick_next_manual_meme_file", new=AsyncMock(return_value=None)),
    ):
        await resumed.ingest_once()
    assert not resumed._refresh_journal_dir().exists()
    updated = await media_database.crud(Images).get(id=media.id)
    cached = await media_database.crud(ImageDescriptions).get_by(image_description_hash=media_id, type="emoji")
    assert updated is not None and updated.description == "旧描述" and not updated.vlm_processed
    if has_cache:
        assert cached is not None and cached.description == "旧描述" and cached.timestamp == 1.0
    else:
        assert cached is None
    records = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    assert records["ids"] == before["ids"] and records["metadatas"] == before["metadatas"]
    assert records["documents"] == before["documents"]
    assert records["embeddings"].tolist() == before["embeddings"].tolist()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_succeeds", [True, False])
@pytest.mark.parametrize("note", [None, "新的理解与背景。\n" + "这一段需要保留完整前因后果，未来才知道怎么用这张图。" * 20, ""], ids=["retain", "replace", "clear"])
async def test_refresh_meme_replaces_labels_or_preserves_old_records(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any, model_succeeds: bool,
    note: str | None,
) -> None:
    """重新识别更新标签及可选备注，未传备注或模型失败时保留原备注。"""
    service = ingest_service
    source = tmp_path / "stored.png"
    source.write_bytes(b"original-image")
    meme_id = service._sha256_bytes(b"original-image")
    old_metadata = {
        "meme_id": meme_id, "path": str(source), "source_hash": meme_id, "created_at": 1.0,
        "note": "原备注",
    }
    meme_collection.add(
        ids=[f"{meme_id}:生气", f"{meme_id}:开心"],
        embeddings=[[0.9, 0.8, 0.7], [0.9, 0.8, 0.7]],
        documents=["旧描述", "旧描述"],
        metadatas=[
            {**old_metadata, "tag": "生气", "description": "旧描述"},
            {**old_metadata, "tag": "开心", "description": "旧描述"},
        ],
    )
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    label = {"keep": True, "description": "撒娇而不是生气", "emotion_tags": ["害羞", "开心"]}
    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock(return_value=None)),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(
            service, "_vlm_decide_and_label", new=AsyncMock(return_value=label if model_succeeds else None)
        ) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock(return_value=[0.1, 0.2, 0.3])) as embedding,
    ):
        ok, result = await service.refresh_meme(meme_id=meme_id[:12], extra_prompt="请核对是否在撒娇", note=note)

    assert ok is model_succeeds
    assert vlm.call_args.kwargs["extra_prompt"] == "请核对是否在撒娇"
    assert vlm.call_args.kwargs["decide_collection"] is False
    assert source.read_bytes() == b"original-image"
    records = meme_collection.get(include=["metadatas", "documents", "embeddings"])
    expected_note = note if model_succeeds and note is not None else "原备注"
    assert all(metadata["note"] == expected_note for metadata in records["metadatas"])
    if model_succeeds:
        assert set(records["ids"]) == {f"{meme_id}:害羞", f"{meme_id}:开心"}
        assert records["documents"] == ["撒娇而不是生气", "撒娇而不是生气"]
        assert all(metadata["created_at"] == 1.0 for metadata in records["metadatas"])
        assert all(list(vector) == pytest.approx([0.1, 0.2, 0.3]) for vector in records["embeddings"])
        embedding.assert_awaited_once()
    else:
        assert "保留旧记录" in result
        assert set(records["ids"]) == {f"{meme_id}:生气", f"{meme_id}:开心"}
        assert records["documents"] == ["旧描述", "旧描述"]
        embedding.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["embedding", "write", "delete", "missing-file", "missing-path", "wrong-hash", "ambiguous-id", "unknown-id", "invalid-id"])
async def test_refresh_meme_failure_keeps_collection_records(
    ingest_service: EmojiSenderService, tmp_path: Path, meme_collection: Any, failure: str
) -> None:
    """重新识别的准备或写入失败、ID 不明确时不能丢掉旧记录。"""
    service = ingest_service
    source = tmp_path / "stored.png"
    source.write_bytes(b"original-image")
    full_id = service._sha256_bytes(b"original-image")
    requested_id = full_id[:12]
    metadata = {"meme_id": full_id, "path": str(source), "description": "旧描述", "tag": "生气", "note": "原备注"}
    if failure == "missing-path":
        metadata.pop("path")
    meme_collection.add(
        ids=[f"{full_id}:生气"], embeddings=[[0.9, 0.8, 0.7]],
        documents=["旧描述"], metadatas=[metadata],
    )
    if failure == "ambiguous-id":
        other_id = full_id[:12] + "b" * 52
        meme_collection.add(
            ids=[f"{other_id}:生气"], embeddings=[[0.9, 0.8, 0.7]], documents=["另一张图"],
            metadatas=[{**metadata, "meme_id": other_id}],
        )
    elif failure == "unknown-id":
        requested_id = "c" * 64
    elif failure == "invalid-id":
        requested_id = "bad-id"
    elif failure == "missing-file":
        source.unlink()
    elif failure == "wrong-hash":
        source.write_bytes(b"different-image")
    before = meme_collection.get(include=["metadatas", "documents"])
    label = {"keep": True, "description": "新描述", "emotion_tags": ["害羞"]}
    vdb = SimpleNamespace(get_or_create_collection=AsyncMock(return_value=meme_collection))
    original_delete = meme_collection.delete
    delete_attempts = 0

    def delete_labels(*, ids: list[str]) -> None:
        """模拟一次旧标签清理失败，其后的恢复操作可正常执行。"""
        nonlocal delete_attempts
        delete_attempts += 1
        if failure == "delete" and delete_attempts == 1:
            raise RuntimeError("test delete failure")
        original_delete(ids=ids)

    with (
        patch.object(service, "_vector_db", return_value=vdb),
        patch("plugins.emoji_sender.service.get_media_info", new=AsyncMock(return_value=None)),
        patch.object(service, "_compress_image_for_vlm", return_value=(b"image", "image/png", False)),
        patch.object(service, "_vlm_decide_and_label", new=AsyncMock(return_value=label)) as vlm,
        patch.object(service, "_embed_query", new=AsyncMock(return_value=None if failure == "embedding" else [0.1, 0.2, 0.3])),
        patch.object(service, "_store_meme_labels", wraps=service._store_meme_labels) as store,
        patch.object(type(meme_collection), "delete", side_effect=delete_labels),
    ):
        if failure == "write":
            store.side_effect = RuntimeError("test storage failure")
        ok, result = await service.refresh_meme(meme_id=requested_id, note="不应保存的备注")

    assert not ok
    if failure in {"missing-file", "missing-path"}:
        assert "原文件已不可用" in result
        assert "重新发送" in result
        vlm.assert_not_awaited()
    assert meme_collection.get(include=["metadatas", "documents"]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["unread", "history", "current", "other-chat"])
@pytest.mark.parametrize("note", ["", "保留这个梗的特别含义。\n以后适合用来自嘲，不要用来指责别人。"])
async def test_collect_action_only_accepts_current_chat_media(location: str, note: str) -> None:
    """收藏动作可引用当前与历史消息中的媒体，但不能操作其他会话。"""
    media_id = "a" * 64
    message = Message(content={"media": [{"type": "emoji", "image_id": media_id}]})
    context = StreamContext(stream_id="test-stream")
    if location == "unread":
        context.unread_messages.append(message)
    elif location == "history":
        context.history_messages.append(message)
    elif location == "current":
        context.current_message = message
    stream = cast(ChatStream, SimpleNamespace(context=context))
    action = CollectEmojiMemeAction(stream, EmojiSenderPlugin(EmojiSenderConfig()))
    service = SimpleNamespace(collect_meme=AsyncMock(return_value=(True, "已收藏")))
    with patch("plugins.emoji_sender.action.get_service", return_value=service):
        ok, _ = await action.execute(media_id=media_id, note=note)

    assert ok is (location != "other-chat")
    if location != "other-chat":
        service.collect_meme.assert_awaited_once_with(media_id=media_id, note=note)
    else:
        service.collect_meme.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("note, note_only", [(None, False), ("新的理解。\n附上来龙去脉。", False), ("仅修改备注", True), ("", True)])
async def test_refresh_action_forwards_extra_prompt(note: str | None, note_only: bool) -> None:
    """刷新动作完整传递识别提示、备注和模式，备注参数不必填。"""
    action = RefreshEmojiMemeAction(cast(ChatStream, SimpleNamespace()), EmojiSenderPlugin(EmojiSenderConfig()))
    service = SimpleNamespace(refresh_meme=AsyncMock(return_value=(True, "已重新识别")))
    with patch("plugins.emoji_sender.action.get_service", return_value=service):
        ok, result = await action.execute(
            meme_id="a" * 12, extra_prompt="" if note_only else "请核对图中文字", note=note, note_only=note_only,
        )

    assert ok and result == "已重新识别"
    service.refresh_meme.assert_awaited_once_with(
        meme_id="a" * 12, extra_prompt="" if note_only else "请核对图中文字",
        allow_chat_media=False, note=note, note_only=note_only,
    )
    schema = RefreshEmojiMemeAction.to_schema()["function"]["parameters"]
    assert "extra_prompt" in schema["properties"]
    assert "extra_prompt" not in schema["required"]
    assert {"note", "note_only"} <= schema["properties"].keys()
    assert not {"note", "note_only"} & set(schema["required"])
    assert schema["properties"]["note_only"]["type"] == "boolean"


@pytest.mark.parametrize("action", [CollectEmojiMemeAction, RefreshEmojiMemeAction])
def test_collection_actions_pass_registration_validation(action: type) -> None:
    """收藏和重新识别动作须声明图片类型并通过插件注册时的校验。"""
    assert action.validate_associated_types() == ["image", "emoji"]


@pytest.mark.parametrize("mode", ["direct", "picker"])
@pytest.mark.parametrize("inject_reminder", [True, False])
def test_collection_guidance_encourages_personal_selection(mode: str, inject_reminder: bool) -> None:
    """两种模式均保留主动择图的工具说明，关闭提醒不影响收藏指导。"""
    from plugins.emoji_sender.plugin import build_emoji_sender_actor_reminder

    config = EmojiSenderConfig()
    config.plugin.interaction_mode = mode
    config.plugin.inject_system_prompt = inject_reminder
    plugin = EmojiSenderPlugin(config)
    assert CollectEmojiMemeAction in plugin.get_components()
    description = CollectEmojiMemeAction.to_schema()["function"]["description"]
    guidance = ("自己喜欢", "人设和表达习惯", "以后用得上", "不必等用户要求", "有选择地收藏", "不是看到图片就收", "发送频率限制")
    assert all(phrase in description for phrase in guidance)
    schema = CollectEmojiMemeAction.to_schema()["function"]["parameters"]
    assert "note" in schema["properties"] and "note" not in schema["required"]
    assert "不是每张图片都需要备注" in schema["properties"]["note"]["description"]
    assert "不限制句数" in schema["properties"]["note"]["description"]
    reminder = build_emoji_sender_actor_reminder(plugin)
    if inject_reminder:
        assert all(phrase in reminder for phrase in guidance)
        assert "不用每张图" in reminder and "不限一两句话" in reminder and "note_only=true" in reminder
    else:
        assert reminder == ""


def test_manifest_and_reminder_include_collection_actions() -> None:
    """声明、提示和可用组件保持一致，关闭提示或插件时遵循配置。"""
    import json

    from plugins.emoji_sender.plugin import build_emoji_sender_actor_reminder

    manifest_path = Path(__file__).parents[3] / "plugins" / "emoji_sender" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = {item["component_name"] for item in manifest["include"] if item["enabled"]}
    config = EmojiSenderConfig()
    plugin = EmojiSenderPlugin(config)
    assert {component.name for component in plugin.get_components()} <= declared
    reminder = build_emoji_sender_actor_reminder(plugin)
    assert "collect_emoji_meme" in reminder and "refresh_emoji_meme" in reminder
    assert "不需要先收藏" in reminder and "重新发送图片" in reminder
    assert manifest["api_version"]["database_api"] == "1.0.0"
    config.plugin.inject_system_prompt = False
    assert build_emoji_sender_actor_reminder(plugin) == ""
    config.plugin.enabled = False
    assert plugin.get_components() == []


# ── picker 模式：search_candidates / send_by_id ──────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("note", [None, "", "这张图有一段值得记住的来龙去脉。\n" + "可以用它表达故作镇定的情绪，不一定局限于图上的文字。" * 20], ids=["legacy", "empty", "long-note"])
async def test_search_tool_displays_complete_optional_note(note: str | None) -> None:
    """候选有备注时完整显示长文本与换行，无备注时不输出占位行。"""
    tool = SearchEmojiMemesTool(EmojiSenderPlugin(EmojiSenderConfig()))
    candidate = {"short_id": "a" * 12, "tag": "开心", "description": "故作镇定", "distance": 0.1}
    if note is not None:
        candidate["note"] = note
    service = SimpleNamespace(search_candidates=AsyncMock(return_value={"candidates": [candidate], "page": 1, "total": 1}))
    with patch("plugins.emoji_sender.tool.get_service", return_value=service):
        ok, result = await tool.execute(description="故作镇定")

    assert ok and "a" * 12 in result
    if note:
        assert note in result and "备注:" in result
    else:
        assert "备注" not in result


def _make_vdb_query_mock(records: list[tuple[str, float, dict[str, Any]]]) -> MagicMock:
    """构建向量库 query mock，records 为 (record_id, distance, metadata) 列表。"""
    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.query = AsyncMock(
        return_value={
            "ids": [[record_id for record_id, _, _ in records]],
            "distances": [[distance for _, distance, _ in records]],
            "metadatas": [[meta for _, _, meta in records]],
        }
    )
    return mock_vdb


def _make_embedding_mocks() -> MagicMock:
    """构建 embedding 请求 mock。"""
    embedding_request = MagicMock()
    embedding_request.send = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))
    return embedding_request


@pytest.mark.asyncio
async def test_search_candidates_dedupes_by_meme_and_truncates_short_id() -> None:
    """同一 meme 多 tag 记录应去重保留最小距离，short_id 取前 12 位。"""
    service = _make_service()
    long_id_a = "a" * 64
    long_id_b = "b" * 64
    mock_vdb = _make_vdb_query_mock(
        [
            (
                f"{long_id_a}:开心",
                0.10,
                {"meme_id": long_id_a, "tag": "开心", "path": "/tmp/a.png", "description": "A 开心"},
            ),
            (
                f"{long_id_a}:兴奋",
                0.20,
                {"meme_id": long_id_a, "tag": "兴奋", "path": "/tmp/a.png", "description": "A 兴奋"},
            ),
            (
                f"{long_id_b}:无语",
                0.30,
                {"meme_id": long_id_b, "tag": "无语", "path": "/tmp/b.png", "description": "B 无语"},
            ),
        ]
    )

    with (
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_embedding_request", return_value=_make_embedding_mocks()),
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
    ):
        result = await service.search_candidates("开心地笑", ["开心", "兴奋"])

    assert result is not None
    candidates = result["candidates"]
    assert [item["meme_id"] for item in candidates] == [long_id_a, long_id_b]
    # 去重后 a 的记录保留 tag=开心（距离 0.10 < 0.20）
    assert candidates[0]["tag"] == "开心"
    assert candidates[0]["short_id"] == long_id_a[:12]
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_search_candidates_filters_recent_usage_with_fallback() -> None:
    """使用历史应过滤候选；全被过滤时回退全量。"""
    service = _make_service()
    long_id_a = "a" * 64
    long_id_b = "b" * 64
    mock_vdb = _make_vdb_query_mock(
        [
            (
                f"{long_id_a}:开心",
                0.10,
                {"meme_id": long_id_a, "tag": "开心", "path": "/tmp/a.png", "description": "A"},
            ),
            (
                f"{long_id_b}:开心",
                0.30,
                {"meme_id": long_id_b, "tag": "开心", "path": "/tmp/b.png", "description": "B"},
            ),
        ]
    )

    with (
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_embedding_request", return_value=_make_embedding_mocks()),
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
    ):
        # a 已发送过：候选只剩 b
        service._record_usage("stream1", long_id_a)
        result = await service.search_candidates("开心地笑", ["开心"], stream_id="stream1")
        assert result is not None
        assert [item["meme_id"] for item in result["candidates"]] == [long_id_b]

        # a、b 都发送过：回退全量（a、b 都在候选中）
        service._record_usage("stream1", long_id_b)
        result = await service.search_candidates("开心地笑", ["开心"], stream_id="stream1")
        assert result is not None
        assert [item["meme_id"] for item in result["candidates"]] == [long_id_a, long_id_b]


@pytest.mark.asyncio
async def test_search_candidates_paginates_without_overlap() -> None:
    """翻页切片正确且页间无重叠。"""
    service = _make_service()
    service._cfg().picker.page_size = 2
    records = []
    for i in range(5):
        meme_id = f"{i:064d}"
        records.append(
            (
                f"{meme_id}:开心",
                0.10 + i * 0.01,
                {"meme_id": meme_id, "tag": "开心", "path": f"/tmp/{i}.png", "description": f"第{i}张"},
            )
        )
    mock_vdb = _make_vdb_query_mock(records)

    with (
        patch("plugins.emoji_sender.service.get_model_set_by_task", return_value=object()),
        patch("plugins.emoji_sender.service.create_embedding_request", return_value=_make_embedding_mocks()),
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
    ):
        page1 = await service.search_candidates("开心地笑", ["开心"], page=1)
        page2 = await service.search_candidates("开心地笑", ["开心"], page=2)
        page3 = await service.search_candidates("开心地笑", ["开心"], page=3)
        page4 = await service.search_candidates("开心地笑", ["开心"], page=4)

    assert page1 is not None and [c["short_id"] for c in page1["candidates"]] == [f"{0:064d}"[:12], f"{1:064d}"[:12]]
    assert page2 is not None and [c["short_id"] for c in page2["candidates"]] == [f"{2:064d}"[:12], f"{3:064d}"[:12]]
    assert page3 is not None and [c["short_id"] for c in page3["candidates"]] == [f"{4:064d}"[:12]]
    assert page4 is None  # 超出总数


@pytest.mark.asyncio
async def test_send_by_id_prefix_match_and_records_usage(tmp_path: Any) -> None:
    """send_by_id 应按 id 前缀匹配并发送，成功后记录使用历史。"""
    service = _make_service()
    long_id = "c" * 64
    meme_path = tmp_path / "c.png"
    meme_path.write_bytes(b"payload")

    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.get = AsyncMock(
        return_value={
            "ids": [f"{long_id}:开心", f"{'d' * 64}:无语"],
            "metadatas": [
                {"meme_id": long_id, "tag": "开心", "path": str(meme_path), "description": "C"},
                {"meme_id": "d" * 64, "tag": "无语", "path": "/tmp/d.png", "description": "D"},
            ],
        }
    )

    with (
        patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb),
        patch("plugins.emoji_sender.service.send_emoji", new=AsyncMock(return_value=True)) as send_mock,
    ):
        ok, result, reason = await service.send_by_id(
            short_id=long_id[:12],
            stream_id="stream1",
            platform="qq",
        )

    assert ok is True
    assert result is not None and result["meme_id"] == long_id
    assert reason == "发送成功"
    assert long_id in service._recently_used("stream1")
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_by_id_unknown_prefix_returns_failure() -> None:
    """未知 id 前缀应返回失败并提示重新查询。"""
    service = _make_service()
    mock_vdb = MagicMock()
    mock_vdb.get_or_create_collection = AsyncMock()
    mock_vdb.get = AsyncMock(return_value={"ids": [], "metadatas": []})

    with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=mock_vdb):
        ok, result, reason = await service.send_by_id(
            short_id="ffffffffffff",
            stream_id="stream1",
            platform="qq",
        )

    assert ok is False
    assert result is None
    assert "未找到" in reason


@pytest.mark.asyncio
async def test_send_best_detailed_records_usage_history() -> None:
    """direct 模式发送成功后应记录使用历史，且下次检索过滤该表情。"""
    service = _make_service(temperature=0.0)
    long_id = "e" * 64
    searched = {
        "meme_id": long_id,
        "tag": "开心",
        "path": "/tmp/e.png",
        "description": "E",
        "distance": 0.1,
        "fallback_used": False,
    }

    with (
        patch.object(service, "search_best", new=AsyncMock(return_value=searched)),
        patch("plugins.emoji_sender.service.Path", side_effect=lambda p: MagicMock(exists=lambda: True, read_bytes=lambda: b"payload") if str(p) == "/tmp/e.png" else Path(p)),
        patch("plugins.emoji_sender.service.send_emoji", new=AsyncMock(return_value=True)),
    ):
        ok, result, reason = await service.send_best_detailed(
            stream_id="stream1",
            platform="qq",
            description_query="开心地笑",
        )

    assert ok is True
    assert reason == "发送成功"
    assert long_id in service._recently_used("stream1")


# ── 插件模式分支 ──────────────────────────────────────────────


def test_get_components_returns_picker_components_in_picker_mode() -> None:
    """picker 模式应返回 Tool + ById Action，而非 direct 的 Action。"""
    from plugins.emoji_sender.plugin import EmojiSenderPlugin

    config = EmojiSenderConfig()
    config.plugin.interaction_mode = "picker"
    plugin = EmojiSenderPlugin(config)

    components = plugin.get_components()

    names = {component.name for component in components}
    assert "search_emoji_memes" in names
    assert "send_emoji_meme_by_id" in names
    assert "send_emoji_meme" not in names
    assert {"collect_emoji_meme", "refresh_emoji_meme"} <= names


def test_get_components_returns_direct_components_by_default() -> None:
    """默认（及非法值）应返回 direct 模式组件。"""
    from plugins.emoji_sender.plugin import EmojiSenderPlugin

    plugin = EmojiSenderPlugin(EmojiSenderConfig())
    names = {component.name for component in plugin.get_components()}
    assert "send_emoji_meme" in names
    assert "search_emoji_memes" not in names
    assert {"collect_emoji_meme", "refresh_emoji_meme"} <= names

    # 非法值按 direct 处理
    config = EmojiSenderConfig()
    config.plugin.interaction_mode = "bogus"
    plugin = EmojiSenderPlugin(config)
    names = {component.name for component in plugin.get_components()}
    assert "send_emoji_meme" in names
    assert "search_emoji_memes" not in names