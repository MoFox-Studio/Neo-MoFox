"""emoji_sender 服务层测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.emoji_sender.config import EmojiSenderConfig
from plugins.emoji_sender.service import EmojiSenderService, MemeCandidate


def _make_service(*, temperature: float = 0.12) -> EmojiSenderService:
    """创建一个带最小配置的 EmojiSenderService。"""
    config = EmojiSenderConfig()
    config.vector.temperature = temperature
    plugin = SimpleNamespace(config=config)
    return EmojiSenderService(plugin=cast(Any, plugin))


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


# ── picker 模式：search_candidates / send_by_id ──────────────────────


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


def test_get_components_returns_direct_components_by_default() -> None:
    """默认（及非法值）应返回 direct 模式组件。"""
    from plugins.emoji_sender.plugin import EmojiSenderPlugin

    plugin = EmojiSenderPlugin(EmojiSenderConfig())
    names = {component.name for component in plugin.get_components()}
    assert "send_emoji_meme" in names
    assert "search_emoji_memes" not in names

    # 非法值按 direct 处理
    config = EmojiSenderConfig()
    config.plugin.interaction_mode = "bogus"
    plugin = EmojiSenderPlugin(config)
    names = {component.name for component in plugin.get_components()}
    assert "send_emoji_meme" in names
    assert "search_emoji_memes" not in names