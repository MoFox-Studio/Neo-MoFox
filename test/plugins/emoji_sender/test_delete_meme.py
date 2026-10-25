"""emoji_sender 表情包删除功能测试。

覆盖：完整 meme_id 校验、向量库记录身份核实、删除结果确认、文件归属校验、
分页收敛边界、部分完成与重试补齐，以及删除 Action 的输出。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.emoji_sender.action import DeleteEmojiMemeByIdAction
from plugins.emoji_sender.config import EmojiSenderConfig
from plugins.emoji_sender.service import EmojiSenderService, _is_full_meme_id

def _make_service() -> EmojiSenderService:
    """创建带最小配置的服务实例。"""
    config = EmojiSenderConfig()
    # _rejected_hashes / _deleted_hashes 由插件持有并共享给服务实例，
    # 构造 stub 时需一并提供
    plugin = SimpleNamespace(
        config=config,
        _rejected_hashes=set(),
        _deleted_hashes=set(),
    )
    return EmojiSenderService(plugin=cast(Any, plugin))


def _sha256(data: bytes) -> str:
    """计算内容的 sha256 十六进制值。"""
    return hashlib.sha256(data).hexdigest()


# 文件内容与 meme_id 必须一致：入库时 meme_id 就是文件内容的 sha256，
# 删除流程会重新计算哈希校验文件归属，因此测试数据需自洽。
MEME_PAYLOAD = b"meme-payload"
OTHER_PAYLOAD = b"other-payload"
MEME_ID = _sha256(MEME_PAYLOAD)
OTHER_ID = _sha256(OTHER_PAYLOAD)


class _FakeVectorDB:
    """单一共享状态的向量库替身。

    ``records`` 是唯一真相来源，查询与删除都读写它，因此可以真实断言
    删除后的后置条件；同时提供故障注入开关模拟向量库的失败路径。
    """

    def __init__(
        self,
        records: list[dict[str, Any]],
        *,
        fail_get: bool = False,
        fail_delete: bool = False,
        fail_ids_get: bool = False,
        empty_dict_on_get: bool = False,
        empty_dict_on_ids_get: bool = False,
        ids_not_list: bool = False,
        missing_ids_key: bool = False,
        metadatas_missing_key: bool = False,
        metadatas_with_none: bool = False,
        metadatas_short: bool = False,
        page_sequence: list[list[str]] | None = None,
    ) -> None:
        """构造向量库替身。

        Args:
            records: 记录列表，每项含 record_id 与 metadata。
            fail_get: 任意查询是否抛异常。
            fail_delete: 删除是否抛异常。
            fail_ids_get: 按 ids 的复查查询是否抛异常。
            empty_dict_on_get: 任意查询是否返回空字典（真实后端的失败形态）。
            empty_dict_on_ids_get: 仅按 ids 的复查查询是否返回空字典。
            ids_not_list: 复查查询是否返回 ids 非列表。
            missing_ids_key: 复查查询是否缺少 ids 键。
            metadatas_missing_key: 响应是否缺少 metadatas 键。
            metadatas_with_none: metadatas 中是否含 None 元素。
            metadatas_short: metadatas 是否比 ids 短。
            page_sequence: 强制分页返回的记录 ID 序列，按调用顺序逐页下发。
        """
        self.records = [dict(item) for item in records]
        self.fail_get = fail_get
        self.fail_delete = fail_delete
        self.fail_ids_get = fail_ids_get
        self.empty_dict_on_get = empty_dict_on_get
        self.empty_dict_on_ids_get = empty_dict_on_ids_get
        self.ids_not_list = ids_not_list
        self.missing_ids_key = missing_ids_key
        self.metadatas_missing_key = metadatas_missing_key
        self.metadatas_with_none = metadatas_with_none
        self.metadatas_short = metadatas_short
        self.page_sequence = page_sequence
        self.deleted_ids: list[str] = []
        self.get_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.scan_call_count = 0

    def _rows(self) -> list[tuple[str, dict[str, Any]]]:
        """返回 (record_id, metadata) 列表。"""
        return [(str(item["record_id"]), dict(item["metadata"])) for item in self.records]

    def _matches(
        self,
        where: dict[str, Any] | None,
        ids: list[str] | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """按 where 与 ids 过滤记录。"""
        rows = self._rows()
        if where:
            target = str(where.get("meme_id") or "").lower()
            rows = [
                (record_id, meta)
                for record_id, meta in rows
                if str(meta.get("meme_id") or "").strip().lower() == target
            ]
        if ids is not None:
            wanted = set(ids)
            rows = [(record_id, meta) for record_id, meta in rows if record_id in wanted]
        return rows

    async def get(self, **kwargs: Any) -> dict[str, Any]:
        """模拟向量库查询。"""
        self.get_calls.append(
            {
                "where": kwargs.get("where"),
                "ids": kwargs.get("ids"),
                "offset": kwargs.get("offset"),
                "collection_name": kwargs.get("collection_name"),
            }
        )
        if self.fail_get:
            raise RuntimeError("注入的查询失败")
        if kwargs.get("ids") is not None and self.fail_ids_get:
            raise RuntimeError("注入的复查查询失败")
        if self.empty_dict_on_get:
            # 真实后端在任何查询异常下都返回空字典，插件据此区分
            # 「查询成功但无结果」与「查询失败、无法确认」。
            return {}
        if kwargs.get("ids") is not None:
            if self.empty_dict_on_ids_get:
                return {}
            if self.ids_not_list:
                return {"ids": "not-a-list", "metadatas": []}
            if self.missing_ids_key:
                return {"metadatas": []}

        where = kwargs.get("where")
        ids = kwargs.get("ids")
        limit = kwargs.get("limit")
        offset = kwargs.get("offset") or 0

        if self.page_sequence is not None and ids is None:
            # 仅作用于扫描查询（无 ids）。按 ids 的复查查询必须按 id 过滤，
            # 否则阶段 D 的删除确认拿到的是无关页面，确认逻辑就测不到。
            # 服务每页只推进 offset += len(ids)，用 offset // limit 索引会永远
            # 停在第一页，因此改按调用次数取页。
            index = self.scan_call_count
            self.scan_call_count += 1
            if index >= len(self.page_sequence):
                return {"ids": [], "metadatas": []}
            page_ids = self.page_sequence[index]
            rows = [(record_id, self._meta_for(record_id)) for record_id in page_ids]
        else:
            rows = self._matches(where, ids)
            rows = rows[offset:]
            if limit is not None:
                rows = rows[: int(limit)]

        response: dict[str, Any] = {
            "ids": [record_id for record_id, _ in rows],
        }
        if self.metadatas_missing_key:
            return response
        metadatas: list[Any] = [meta for _, meta in rows]
        if self.metadatas_with_none and metadatas:
            metadatas[0] = None
        if self.metadatas_short and metadatas:
            metadatas = metadatas[:-1]
        response["metadatas"] = metadatas
        return response

    def _meta_for(self, record_id: str) -> dict[str, Any]:
        """按记录 ID 查找对应 metadata。"""
        for record_id_value, meta in self._rows():
            if record_id_value == record_id:
                return meta
        return {}

    async def delete(self, **kwargs: Any) -> dict[str, Any]:
        """模拟向量库删除。"""
        self.delete_calls.append(
            {
                "ids": kwargs.get("ids"),
                "where": kwargs.get("where"),
                "collection_name": kwargs.get("collection_name"),
            }
        )
        if self.fail_delete:
            raise RuntimeError("注入的删除失败")
        ids = kwargs.get("ids")
        where = kwargs.get("where")
        if ids is not None:
            self.deleted_ids.extend(str(item) for item in ids)
            wanted = set(str(item) for item in ids)
            self.records = [
                item for item in self.records if str(item["record_id"]) not in wanted
            ]
            return
        if where is not None:
            target = str(where.get("meme_id") or "").lower()
            removed = [
                str(item["record_id"])
                for item in self.records
                if str(item["metadata"].get("meme_id") or "").strip().lower() == target
            ]
            self.deleted_ids.extend(removed)
            self.records = [
                item
                for item in self.records
                if str(item["metadata"].get("meme_id") or "").strip().lower() != target
            ]

    async def get_or_create_collection(self, name: str) -> Any:
        """模拟获取集合。"""
        return object()


def _make_vdb(
    records: list[dict[str, Any]],
    **kwargs: Any,
) -> _FakeVectorDB:
    """构造向量库替身。"""
    return _FakeVectorDB(records, **kwargs)


def _record(
    record_id: str,
    meme_id: str | None,
    *,
    tag: str = "开心",
    description: str = "描述",
    path: str = "",
) -> dict[str, Any]:
    """构造一条向量库记录。"""
    metadata: dict[str, Any] = {"tag": tag, "description": description}
    if meme_id is not None:
        metadata["meme_id"] = meme_id
    if path:
        metadata["path"] = path
    return {"record_id": record_id, "metadata": metadata}


def _write_meme(data_dir: Path, meme_id: str, payload: bytes = MEME_PAYLOAD) -> Path:
    """在 data 目录写入一张表情包文件。

    默认内容与 MEME_ID 自洽（meme_id 即内容 sha256），使文件归属校验可通过。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / f"{meme_id}.png"
    target.write_bytes(payload)
    return target


class TestFullMemeIdValidation:
    """完整 meme_id 校验。"""

    def test_valid(self) -> None:
        """64 位十六进制为合法。"""
        assert _is_full_meme_id(MEME_ID) is True

    def test_valid_uppercase(self) -> None:
        """大写十六进制亦为合法。"""
        assert _is_full_meme_id(MEME_ID.upper()) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "abc",
            "a" * 63,
            "a" * 65,
            "z" * 64,
            "a" * 64 + "!",
            "a" * 32 + "_" + "a" * 31,
            "+" + "a" * 63,
            "0x" + "a" * 62,
            "a" * 63 + "g",
        ],
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
    async def test_rejects_loose_hex_forms(self) -> None:
        """下划线、符号与 0x 前缀等宽松形式被拒绝。"""
        service = _make_service()
        for value in ("a" * 32 + "_" + "a" * 31, "+" + "a" * 63, "0x" + "a" * 62):
            ok, _, reason = await service.delete_by_meme_id(meme_id=value)
            assert ok is False
            assert "完整 ID" in reason

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path: Path) -> None:
        """库中不存在且无残留文件时报告未找到。"""
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
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        other_file = _write_meme(data_dir, OTHER_ID, OTHER_PAYLOAD)

        records = [
            _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
            _record(f"{MEME_ID}:兴奋", MEME_ID, tag="兴奋", path=str(meme_file)),
            _record(f"{OTHER_ID}:开心", OTHER_ID, path=str(other_file)),
        ]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert result is not None
        assert result["deleted_records"] == 2
        assert result["tags"] == sorted(["开心", "兴奋"])
        assert result["file_deleted"] is True
        assert not meme_file.exists()
        assert other_file.exists()
        assert [item["record_id"] for item in vdb.records] == [f"{OTHER_ID}:开心"]

    @pytest.mark.asyncio
    async def test_deletes_by_record_ids_not_where(self, tmp_path: Path) -> None:
        """删除使用已核实的记录 ID，而不是 metadata 的 where 条件。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert vdb.deleted_ids == [f"{MEME_ID}:开心"]

    @pytest.mark.asyncio
    async def test_deletes_only_target_meme(self, tmp_path: Path) -> None:
        """只删除目标表情包，不影响其他条目。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        other_file = _write_meme(data_dir, OTHER_ID, OTHER_PAYLOAD)

        records = [
            _record(f"{MEME_ID}:开心", MEME_ID, path=str(tmp_path / "a.png")),
            _record(f"{OTHER_ID}:开心", OTHER_ID, path=str(other_file)),
        ]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert other_file.exists()
        assert [item["record_id"] for item in vdb.records] == [f"{OTHER_ID}:开心"]

    @pytest.mark.asyncio
    async def test_missing_file_treated_as_deleted(self, tmp_path: Path) -> None:
        """文件已被手动删除时视为已删除，仍算成功。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        data_dir.mkdir(parents=True, exist_ok=True)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(data_dir / "x.png"))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert result is not None
        assert result["file_deleted"] is True

    @pytest.mark.asyncio
    async def test_file_delete_failure_reported(self, tmp_path: Path) -> None:
        """磁盘文件删除失败时明确报告部分完成。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        def boom(path_value: str) -> None:
            raise OSError("文件被占用")

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
            patch.object(service, "_delete_file", side_effect=boom),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert "删除文件失败" in reason
        assert meme_file.exists()
        # 部分完成状态携带详情
        assert result is not None
        assert result["file_deleted"] is False

    @pytest.mark.asyncio
    async def test_records_remaining_detected(self, tmp_path: Path) -> None:
        """向量库记录未真正消失时报告失败且不删文件。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        async def _noop_delete(**kwargs: Any) -> None:
            return None

        vdb.delete = AsyncMock(side_effect=_noop_delete)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "残留" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_delete_failed_confirmation_ok_reports_residual(self, tmp_path: Path) -> None:
        """删除失败但复查成功时报告残留且不删文件。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        async def _noop_delete(**kwargs: Any) -> None:
            return None

        vdb.delete = AsyncMock(side_effect=_noop_delete)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "残留" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_confirmation_empty_dict_is_unconfirmed(self, tmp_path: Path) -> None:
        """复查返回空字典时视为无法确认，不删文件也不报告成功。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            empty_dict_on_ids_get=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "无法确认" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_confirmation_exception_is_unconfirmed(self, tmp_path: Path) -> None:
        """复查抛异常时视为无法确认，不删文件。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            fail_ids_get=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "无法确认" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_confirmation_invalid_shape_is_unconfirmed(self, tmp_path: Path) -> None:
        """复查响应结构无效时视为无法确认。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)

        for kwargs in ({"ids_not_list": True}, {"missing_ids_key": True}):
            vdb = _make_vdb(
                [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
                **kwargs,
            )
            with (
                patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
                patch.object(service, "_data_dir", return_value=data_dir),
            ):
                ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

            assert ok is False
            assert result is None
            assert "无法确认" in reason
            assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_delete_and_confirmation_both_fail(self, tmp_path: Path) -> None:
        """删除与复查同时失败时不删文件、不报告成功。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            fail_delete=True,
            empty_dict_on_ids_get=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        # 删除阶段已失败，无需进入确认阶段；无论哪种失败都必须保留文件
        assert "删除向量库记录失败" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_record_id_and_metadata_conflict_rejected(self, tmp_path: Path) -> None:
        """记录 ID 与 metadata 冲突时在删除前拒绝，无任何副作用。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        other_file = _write_meme(data_dir, OTHER_ID, OTHER_PAYLOAD)

        records = [
            _record(f"{MEME_ID}:开心", OTHER_ID, path=str(other_file)),
        ]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "身份冲突" in reason
        assert other_file.exists()
        assert len(vdb.records) == 1
        assert vdb.deleted_ids == []

    @pytest.mark.asyncio
    async def test_record_without_meme_id_deleted_completely(self, tmp_path: Path) -> None:
        """metadata 缺 meme_id 时按记录 ID 删除，不留残留。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", None, path=str(meme_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert result is not None
        assert result["deleted_records"] == 1
        assert vdb.records == []
        assert not meme_file.exists()

    @pytest.mark.asyncio
    async def test_metadata_none_element_rejected(self, tmp_path: Path) -> None:
        """metadatas 含 None 元素时不抛异常，明确失败且无副作用。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            metadatas_with_none=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "无效" in reason
        assert meme_file.exists()
        assert len(vdb.records) == 1

    @pytest.mark.asyncio
    async def test_metadata_short_list_rejected(self, tmp_path: Path) -> None:
        """metadatas 长度短于 ids 时明确失败，不抛异常。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        records = [
            _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
            _record(f"{MEME_ID}:兴奋", MEME_ID, path=str(meme_file)),
        ]
        vdb = _make_vdb(records, metadatas_short=True)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "无效" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_metadata_path_outside_data_dir_ignored(self, tmp_path: Path) -> None:
        """metadata.path 指向目录外时不删除该文件，按 meme_id 推导仍正确删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"outside")
        meme_file = _write_meme(data_dir, MEME_ID)

        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(outside))],
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert result is not None
        assert outside.exists()
        assert not meme_file.exists()

    @pytest.mark.asyncio
    async def test_file_hash_mismatch_rejected(self, tmp_path: Path) -> None:
        """data 目录内文件哈希与目标不符时拒绝删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        data_dir.mkdir(parents=True, exist_ok=True)
        wrong_file = data_dir / f"{MEME_ID}.png"
        wrong_file.write_bytes(b"not-the-target")

        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(wrong_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "拒绝删除" in reason
        assert wrong_file.exists()
        assert len(vdb.records) == 1

    @pytest.mark.asyncio
    async def test_symlink_candidate_not_followed(self, tmp_path: Path) -> None:
        """符号链接候选不跟随、不删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        data_dir.mkdir(parents=True, exist_ok=True)
        real_file = tmp_path / "real.png"
        real_file.write_bytes(MEME_PAYLOAD)
        link = data_dir / f"{MEME_ID}.png"
        try:
            link.symlink_to(real_file)
        except (OSError, NotImplementedError):
            pytest.skip("当前平台不支持创建符号链接")

        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(link))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "符号链接" in reason
        assert real_file.exists()

    @pytest.mark.asyncio
    async def test_multiple_suffix_copies_all_deleted(self, tmp_path: Path) -> None:
        """同一表情包存在多个后缀副本时全部删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        data_dir.mkdir(parents=True, exist_ok=True)
        payload = MEME_PAYLOAD
        png_file = data_dir / f"{MEME_ID}.png"
        jpg_file = data_dir / f"{MEME_ID}.jpg"
        png_file.write_bytes(payload)
        jpg_file.write_bytes(payload)

        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(png_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert result is not None
        assert len(result["deleted_files"]) == 2
        assert not png_file.exists()
        assert not jpg_file.exists()

    @pytest.mark.asyncio
    async def test_pagination_repeated_page_rejected(self, tmp_path: Path) -> None:
        """分页返回重复页时收敛失败，不执行删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            page_sequence=[[f"{MEME_ID}:开心"], [f"{MEME_ID}:开心"]],
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "分页未推进" in reason
        assert meme_file.exists()
        assert len(vdb.records) == 1

    @pytest.mark.asyncio
    async def test_pagination_alternating_pages_rejected(self, tmp_path: Path) -> None:
        """分页交替返回两页时判定不完整，不执行删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        other_file = _write_meme(data_dir, OTHER_ID, OTHER_PAYLOAD)

        vdb = _make_vdb(
            [
                _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
                _record(f"{OTHER_ID}:开心", OTHER_ID, path=str(other_file)),
            ],
            page_sequence=[
                [f"{MEME_ID}:开心"],
                [f"{OTHER_ID}:开心"],
                [f"{MEME_ID}:开心"],
            ],
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "分页未推进" in reason
        assert meme_file.exists()
        assert len(vdb.records) == 2

    @pytest.mark.asyncio
    async def test_retry_completes_pending_file_cleanup(self, tmp_path: Path) -> None:
        """向量记录已删但文件仍在时，重试可补齐文件清理。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert result is not None
        assert result["deleted_records"] == 0
        assert not meme_file.exists()

    @pytest.mark.asyncio
    async def test_retry_without_file_reports_not_found(self, tmp_path: Path) -> None:
        """向量记录与文件都不存在时报告未找到，不误报成功。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        data_dir.mkdir(parents=True, exist_ok=True)
        vdb = _make_vdb([])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "未找到" in reason

    @pytest.mark.asyncio
    async def test_retry_with_wrong_hash_file_rejected(self, tmp_path: Path) -> None:
        """目录下同名文件哈希不符时不删除，报失败。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        data_dir.mkdir(parents=True, exist_ok=True)
        wrong_file = data_dir / f"{MEME_ID}.png"
        wrong_file.write_bytes(b"other-content")
        vdb = _make_vdb([])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "拒绝删除" in reason
        assert wrong_file.exists()


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


class TestUnrelatedRecordsDoNotBlockDelete:
    """无关记录的异常不应阻断删除目标表情包。"""

    @pytest.mark.asyncio
    async def test_unrelated_conflicting_record_does_not_block(self, tmp_path: Path) -> None:
        """与目标无关的身份冲突记录不应阻断删除目标表情包。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)

        records = [
            _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
            # 记录 ID 前缀与元数据不一致，且与目标无关
            _record(f"{OTHER_ID}:开心", "c" * 64, path=str(meme_file)),
        ]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert result is not None
        # 只删除目标记录，无关记录保留
        assert [item["record_id"] for item in vdb.records] == [f"{OTHER_ID}:开心"]
        assert not meme_file.exists()

    @pytest.mark.asyncio
    async def test_unrelated_missing_meme_id_record_does_not_block(
        self, tmp_path: Path
    ) -> None:
        """与目标无关且缺少 meme_id 的记录不应阻断删除目标表情包。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)

        records = [
            _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
            # 记录 ID 前缀指向其它表情包，元数据没有 meme_id
            _record(f"{OTHER_ID}:开心", None, path=str(meme_file)),
        ]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert not meme_file.exists()
        # 无关记录必须幸存，否则过度删除（把无关记录一并删掉）不会被发现
        assert [item["record_id"] for item in vdb.records] == [f"{OTHER_ID}:开心"]

    @pytest.mark.asyncio
    async def test_record_without_any_meme_id_does_not_block(self, tmp_path: Path) -> None:
        """记录 ID 前缀与元数据都缺少 meme_id 的记录不应阻断删除目标。

        这类记录无法指向目标（目标 ID 恒为非空的 64 位十六进制），
        对它失败关闭不会防止漏删目标记录，只会阻断用户的删除请求。
        """
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)

        records = [
            _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
            # 记录 ID 前缀为空（以冒号开头）且元数据没有 meme_id
            _record(":开心", None, path=str(meme_file)),
        ]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert not meme_file.exists()
        assert [item["record_id"] for item in vdb.records] == [":开心"]

    @pytest.mark.asyncio
    async def test_target_record_with_conflicting_identity_still_rejected(
        self, tmp_path: Path
    ) -> None:
        """目标记录自身身份矛盾时仍必须失败关闭。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)

        # 记录 ID 前缀指向目标，元数据却指向别的表情包
        records = [_record(f"{MEME_ID}:开心", OTHER_ID, path=str(meme_file))]
        vdb = _make_vdb(records)

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "身份冲突" in reason
        assert meme_file.exists()


class TestDeleteSuppressesReingest:
    """删除后应抑制后续入库重新收录同一张表情包。"""

    @pytest.mark.asyncio
    async def test_deleted_meme_id_added_to_deleted_hashes(self, tmp_path: Path) -> None:
        """删除成功后 meme_id 进入删除集合，阻止再次入库。"""
        config = EmojiSenderConfig()
        deleted: set[str] = set()
        plugin = SimpleNamespace(
            config=config, _rejected_hashes=set(), _deleted_hashes=deleted
        )
        service = EmojiSenderService(plugin=cast(Any, plugin))

        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert MEME_ID in deleted

    @pytest.mark.asyncio
    async def test_delete_not_written_into_vlm_rejected_set(self, tmp_path: Path) -> None:
        """删除语义不应与 VLM 拒收混在同一个集合里。"""
        config = EmojiSenderConfig()
        rejected: set[str] = set()
        deleted: set[str] = set()
        plugin = SimpleNamespace(
            config=config, _rejected_hashes=rejected, _deleted_hashes=deleted
        )
        service = EmojiSenderService(plugin=cast(Any, plugin))

        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert MEME_ID in deleted
        # VLM 拒收集合只表达「模型认为不值得收藏」，不应记录用户删除
        assert MEME_ID not in rejected

    @pytest.mark.asyncio
    async def test_completion_path_also_suppresses_reingest(self, tmp_path: Path) -> None:
        """补齐文件删除的路径同样要抑制重新入库。"""
        config = EmojiSenderConfig()
        deleted: set[str] = set()
        plugin = SimpleNamespace(
            config=config, _rejected_hashes=set(), _deleted_hashes=deleted
        )
        service = EmojiSenderService(plugin=cast(Any, plugin))

        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        # 向量库中已无该记录，只剩文件
        vdb = _make_vdb([])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        assert reason == "删除成功"
        assert not meme_file.exists()
        assert MEME_ID in deleted

    @pytest.mark.asyncio
    async def test_failed_delete_does_not_suppress_reingest(self, tmp_path: Path) -> None:
        """删除失败时不应抑制重新入库，避免误伤正常表情包。"""
        config = EmojiSenderConfig()
        deleted: set[str] = set()
        plugin = SimpleNamespace(
            config=config, _rejected_hashes=set(), _deleted_hashes=deleted
        )
        service = EmojiSenderService(plugin=cast(Any, plugin))

        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        def boom(path_value: str) -> None:
            raise OSError("注入的文件删除失败")

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
            patch.object(service, "_delete_file", side_effect=boom),
        ):
            ok, _, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert "删除文件失败" in reason
        # 删除的是 MEME_ID 本身，因此断言的必须是同一个 id，
        # 否则断言的候选从未参与删除，恒为真、无法发现过早抑制。
        assert MEME_ID not in deleted


class TestStageAFailureHandling:
    """收集阶段（扫描向量库）的失败形态必须失败关闭。"""

    @pytest.mark.asyncio
    async def test_scan_query_exception_keeps_file(self, tmp_path: Path) -> None:
        """扫描阶段查询抛异常时失败关闭，文件不得被删除。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            fail_get=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "查询向量库失败" in reason
        assert meme_file.exists()
        assert vdb.records != []

    @pytest.mark.asyncio
    async def test_scan_empty_dict_response_keeps_file(self, tmp_path: Path) -> None:
        """扫描阶段返回空字典（真实后端的失败形态）时失败关闭。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            empty_dict_on_get=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "无法确认表情包记录" in reason
        assert meme_file.exists()

    @pytest.mark.asyncio
    async def test_scan_metadata_key_missing_keeps_file(self, tmp_path: Path) -> None:
        """扫描阶段响应缺少 metadatas 键时失败关闭。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb(
            [_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))],
            metadatas_missing_key=True,
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "无法确认表情包元数据" in reason
        assert meme_file.exists()


class TestCollectionScoping:
    """所有向量库操作必须落在同一个集合上。"""

    @pytest.mark.asyncio
    async def test_all_operations_use_same_collection(self, tmp_path: Path) -> None:
        """扫描、删除与复查查询必须使用同一个集合名。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, _, _ = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is True
        calls = [*vdb.get_calls, *vdb.delete_calls]
        assert calls, "应记录到向量库调用"
        expected = service._collection_name()
        # 断言每一次调用都显式带上同一个集合名：不能用
        # `if call.get("collection_name")` 过滤，否则漏掉集合名的调用
        # 会被悄悄跳过，扫描路径指向错集合也测不出来。
        assert all(call["collection_name"] == expected for call in calls), (
            f"所有向量库调用都应使用集合 {expected}，实际: "
            f"{[call['collection_name'] for call in calls]}"
        )


class TestPartialCompletionAssertions:
    """部分完成状态必须同时钉住向量侧与文件侧。"""

    @pytest.mark.asyncio
    async def test_vector_deleted_but_file_kept(self, tmp_path: Path) -> None:
        """向量已删、文件未删时，向量侧与文件侧都要被断言。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        vdb = _make_vdb([_record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file))])

        def boom(path_value: str) -> None:
            raise OSError("注入的文件删除失败")

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
            patch.object(service, "_delete_file", side_effect=boom),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert "已删除向量库记录，但删除文件失败" in reason
        # 向量记录确实已删除，文件确实仍在
        assert vdb.records == []
        assert meme_file.exists()
        # 部分完成也要返回详情，调用方无需解析中文字符串即可区分
        # 「什么都没删」与「删了一半」
        assert result is not None
        assert result["deleted_records"] == 1
        assert result["file_deleted"] is False

    @pytest.mark.asyncio
    async def test_retry_completion_file_delete_failure(self, tmp_path: Path) -> None:
        """补齐路径中文件删除也失败时，必须报告失败而非成功。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)
        # 向量库中已无记录，只剩文件
        vdb = _make_vdb([])

        def boom(path_value: str) -> None:
            raise OSError("注入的文件删除失败")

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
            patch.object(service, "_delete_file", side_effect=boom),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert "表情包文件删除失败" in reason
        assert meme_file.exists()
        # 部分完成同样返回详情
        assert result is not None
        assert result["file_deleted"] is False
        assert result["deleted_records"] == 0


class TestPaginationAdvancement:
    """分页必须真正推进到下一页。"""

    @pytest.mark.asyncio
    async def test_alternating_pages_rejected(self, tmp_path: Path) -> None:
        """交替重复页必须被识别为分页未推进。"""
        service = _make_service()
        data_dir = tmp_path / "memes"
        meme_file = _write_meme(data_dir, MEME_ID)

        page_a = [f"{MEME_ID}:开心"]
        page_b = [f"{OTHER_ID}:开心"]
        vdb = _make_vdb(
            [
                _record(f"{MEME_ID}:开心", MEME_ID, path=str(meme_file)),
                _record(f"{OTHER_ID}:开心", OTHER_ID, path=str(meme_file)),
            ],
            page_sequence=[page_a, page_b, page_a, page_b],
        )

        with (
            patch("plugins.emoji_sender.service.get_vector_db_service", return_value=vdb),
            patch.object(service, "_data_dir", return_value=data_dir),
        ):
            ok, result, reason = await service.delete_by_meme_id(meme_id=MEME_ID)

        assert ok is False
        assert result is None
        assert "分页未推进" in reason
        assert meme_file.exists()
