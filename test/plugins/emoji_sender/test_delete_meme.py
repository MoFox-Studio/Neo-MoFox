"""emoji_sender 表情包删除功能测试。

覆盖：完整 meme_id 校验、向量库记录与磁盘文件的删除、删除后残留检测、
文件缺失与文件删除失败的边界，以及删除 Action 的输出。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.emoji_sender.action import DeleteEmojiMemeByIdAction
from plugins.emoji_sender.config import EmojiSenderConfig
from plugins.emoji_sender.service import EmojiSenderService, _is_full_meme_id

# 合法完整 meme_id（64 位十六进制）
MEME_ID = "a" * 64
OTHER_ID = "b" * 64


def _make_service() -> EmojiSenderService:
    """创建带最小配置的服务实例。"""
    config = EmojiSenderConfig()
    # _rejected_hashes 由插件持有并共享给服务实例，构造 stub 时需一并提供
    plugin = SimpleNamespace(config=config, _rejected_hashes=set())
    return EmojiSenderService(plugin=cast(Any, plugin))


def _make_vdb(
    records: list[dict[str, Any]],
    *,
    deleted: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """构造向量库替身，模拟真实 get/add/delete 的分页语义。

    Args:
        records: 库中现存记录。
        deleted: 记录删除调用的列表；为 None 时不额外记录。

    Returns:
        MagicMock: 向量库替身。
    """
    vdb = MagicMock()
    vdb.get_or_create_collection = AsyncMock()

    state = {"records": list(records)}

    async def _get(**kwargs: Any) -> dict[str, Any]:
        """按 where 服务端过滤、按 limit/offset 分页返回记录。"""
        where = kwargs.get("where") or {}
        target = str(where.get("meme_id") or "").lower()
        if target:
            page_source = [
                r for r in state["records"] if str(r.get("meme_id") or "").lower() == target
            ]
        else:
            page_source = list(state["records"])
        limit = int(kwargs.get("limit") or 512)
        offset = int(kwargs.get("offset") or 0)
        page = page_source[offset : offset + limit]
        return {
            "ids": [f"{r.get('meme_id', '')}:{r.get('tag', '')}" for r in page],
            "metadatas": page,
        }

    async def _delete(**kwargs: Any) -> None:
        """按 meme_id 删除记录。"""
        if deleted is not None:
            deleted.append(kwargs)
        where = kwargs.get("where") or {}
        target = str(where.get("meme_id") or "").lower()
        state["records"] = [
            r for r in state["records"] if str(r.get("meme_id") or "").lower() != target
        ]

    vdb.get = AsyncMock(side_effect=_get)
    vdb.delete = AsyncMock(side_effect=_delete)
    return vdb


class TestFullMemeIdValidation:
    """完整 meme_id 校验。"""

    def test_valid(self) -> None:
        """64 位十六进制为合法。"""
        assert _is_full_meme_id(MEME_ID) is True

    @pytest.mark.parametrize(
        "value",
        ["", "abc", "a" * 63, "a" * 65, "z" * 64, "a" * 64 + "!"],
    )
    def test_invalid(self, value: str) -> None:
        """非 64 位十六进制为非法。"""
        assert _is_full_meme_id(value) is False


class TestDeleteByMemeId:
    """按完整 meme_id 删除。"""

    @pytest.mark.asyncio
    async def test_rejects_empty_id(self) -> None:
        """空 ID 被拒绝。"""
        service = _make_service()
        ok, result, reason = await service.delete_by_meme_id(meme_id="  ")
        assert ok is False
        assert result is None
        assert "不能为空" in reason

    @pytest.mark.asyncio
    async def test_rejects_prefix_id(self) -> None:
        """前缀 ID 被拒绝，不接受短 id。"""
        service = _make_service()
        ok, _, reason = await service.delete_by_meme_id(meme_id="a1b2c3")
        assert ok is False
        assert "完整 ID" in reason

    @pytest.mark.asyncio
    async def test_rejects_non_hex_id(self) -> None:
        """非十六进制 ID 被拒绝。"""
        service = _make_service()
        ok, _, reason = await service.delete_by_meme_id(meme_id="z" * 64)
        assert ok is False
        assert "完整 ID" in reason

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        """库中不存在时报告未找到。"""
        service = _make_service()
        vdb = _make_vdb([])
        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)
        assert ok is False
        assert result is None
        assert "未找到" in reason

    @pytest.mark.asyncio
    async def test_deletes_records_and_file(self, tmp_path: Path) -> None:
        """删除向量库记录与磁盘文件。"""
        service = _make_service()
        meme_file = tmp_path / f"{MEME_ID}.png"
        meme_file.write_bytes(b"payload")

        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(meme_file), "description": "第一张"},
            {"meme_id": MEME_ID, "tag": "兴奋", "path": str(meme_file), "description": "第一张"},
            {"meme_id": OTHER_ID, "tag": "开心", "path": str(tmp_path / "other.png"), "description": "别的"},
        ]
        vdb = _make_vdb(records)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert result is not None
        assert result["deleted_records"] == 2
        assert result["tags"] == sorted(["开心", "兴奋"])
        assert result["file_deleted"] is True
        assert not meme_file.exists()

    @pytest.mark.asyncio
    async def test_deletes_only_target_meme(self, tmp_path: Path) -> None:
        """只删除目标表情包，不影响其他条目。"""
        service = _make_service()
        other_file = tmp_path / f"{OTHER_ID}.png"
        other_file.write_bytes(b"other")

        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(tmp_path / "a.png"), "description": "A"},
            {"meme_id": OTHER_ID, "tag": "开心", "path": str(other_file), "description": "B"},
        ]
        vdb = _make_vdb(records)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, _, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert other_file.exists()

    @pytest.mark.asyncio
    async def test_missing_file_treated_as_deleted(self, tmp_path: Path) -> None:
        """文件已被手动删除时视为已删除，仍算成功。"""
        service = _make_service()
        missing = tmp_path / f"{MEME_ID}.png"
        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(missing), "description": "A"},
        ]
        vdb = _make_vdb(records)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, result, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert result is not None
        assert result["file_deleted"] is True

    @pytest.mark.asyncio
    async def test_file_delete_failure_reported(self, tmp_path: Path) -> None:
        """磁盘文件删除失败时明确报告，不静默成功。"""
        service = _make_service()
        meme_file = tmp_path / f"{MEME_ID}.png"
        meme_file.write_bytes(b"payload")

        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(meme_file), "description": "A"},
        ]
        vdb = _make_vdb(records)

        def boom(path_value: str) -> None:
            raise OSError("文件被占用")

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_delete_file", side_effect=boom),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "删除文件失败" in reason

    @pytest.mark.asyncio
    async def test_records_remaining_detected(self, tmp_path: Path) -> None:
        """向量库记录未真正消失时报告失败。"""
        service = _make_service()
        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(tmp_path / "a.png"), "description": "A"},
        ]
        vdb = _make_vdb(records)

        # 模拟 delete 无效：记录仍然存在
        async def _noop_delete(**kwargs: Any) -> None:
            return None

        vdb.delete = AsyncMock(side_effect=_noop_delete)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "残留" in reason

    @pytest.mark.asyncio
    async def test_matches_by_record_id_prefix(self, tmp_path: Path) -> None:
        """metadata 缺少 meme_id 时按记录 ID 前缀兜底匹配。"""
        service = _make_service()
        records = [
            {"tag": "开心", "path": str(tmp_path / "a.png"), "description": "A"},
        ]
        vdb = _make_vdb(records)

        # metadata 中没有 meme_id，只有记录 ID 前缀可依据
        async def _get(**kwargs: Any) -> dict[str, Any]:
            """按 offset 正确分页返回，metadata 中没有 meme_id。"""
            where = kwargs.get("where") or {}
            if where.get("meme_id"):
                # where 查询：服务端按 metadata 过滤，此处 metadata 无 meme_id
                return {"ids": [], "metadatas": []}
            offset = int(kwargs.get("offset") or 0)
            if offset >= 1:
                return {"ids": [], "metadatas": []}
            return {
                "ids": [f"{MEME_ID}:开心"],
                "metadatas": [records[0]],
            }

        vdb.get = AsyncMock(side_effect=_get)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, result, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert result is not None


    @pytest.mark.asyncio
    async def test_pagination_stuck_is_bounded(self, tmp_path: Path) -> None:
        """向量库分页未推进时应收敛，不能死循环。"""
        service = _make_service()
        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(tmp_path / "a.png"), "description": "A"},
        ]
        vdb = _make_vdb(records)

        # 模拟分页未推进：每次都返回同一页
        async def _stuck_get(**kwargs: Any) -> dict[str, Any]:
            """始终返回同一页，模拟分页未推进。"""
            return {
                "ids": [f"{MEME_ID}:开心"],
                "metadatas": [records[0]],
            }

        vdb.get = AsyncMock(side_effect=_stuck_get)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            # 关键：不因分页未推进而死循环；删除未生效时如实报告残留
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "残留" in reason

    @pytest.mark.asyncio
    async def test_where_query_failure_treated_as_no_remaining(
        self, tmp_path: Path
    ) -> None:
        """where 查询异常时不应误判为残留，也不应崩溃。"""
        service = _make_service()
        meme_file = tmp_path / f"{MEME_ID}.png"
        meme_file.write_bytes(b"payload")
        records = [
            {"meme_id": MEME_ID, "tag": "开心", "path": str(meme_file), "description": "A"},
        ]
        vdb = _make_vdb(records)

        async def _get(**kwargs: Any) -> dict[str, Any]:
            """where 查询抛异常，分页查询正常返回记录。"""
            if kwargs.get("where"):
                raise RuntimeError("where 不支持")
            offset = int(kwargs.get("offset") or 0)
            if offset >= len(records):
                return {"ids": [], "metadatas": []}
            page = records[offset : offset + 512]
            return {
                "ids": [f"{r['meme_id']}:{r.get('tag', '')}" for r in page],
                "metadatas": page,
            }

        vdb.get = AsyncMock(side_effect=_get)

        with patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb):
            ok, result, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert result is not None


class TestDeleteAction:
    """删除 Action。"""

    @pytest.mark.asyncio
    async def test_service_missing(self) -> None:
        """service 未加载时明确报错。"""
        action = DeleteEmojiMemeByIdAction.__new__(DeleteEmojiMemeByIdAction)
        with patch("plugins.emoji_sender.action.get_service", return_value=None):
            results = [item async for item in action.execute(meme_id=MEME_ID)]
        assert results[-1][0] is False
        assert "service 未加载" in results[-1][1]

    @pytest.mark.asyncio
    async def test_success_output(self) -> None:
        """成功时输出删除详情。"""
        action = DeleteEmojiMemeByIdAction.__new__(DeleteEmojiMemeByIdAction)
        service = _make_service()
        service.delete_by_meme_id = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                True,
                {
                    "meme_id": MEME_ID,
                    "tags": ["开心", "兴奋"],
                    "description": "挥手",
                },
                "删除成功",
            )
        )
        with patch("plugins.emoji_sender.action.get_service", return_value=service):
            results = [item async for item in action.execute(meme_id=MEME_ID)]

        assert results[-1][0] is True
        message = results[-1][1]
        assert "已删除表情包" in message
        assert "开心、兴奋" in message
        assert "挥手" in message

    @pytest.mark.asyncio
    async def test_failure_output(self) -> None:
        """失败时透传原因。"""
        action = DeleteEmojiMemeByIdAction.__new__(DeleteEmojiMemeByIdAction)
        service = _make_service()
        service.delete_by_meme_id = AsyncMock(  # type: ignore[method-assign]
            return_value=(False, None, "未找到 id=xxx 对应的表情包")
        )
        with patch("plugins.emoji_sender.action.get_service", return_value=service):
            results = [item async for item in action.execute(meme_id=MEME_ID)]

        assert results[-1][0] is False
        assert "未找到" in results[-1][1]
