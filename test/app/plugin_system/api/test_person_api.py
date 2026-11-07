"""person_api 的单元测试。

测试覆盖：
- generate_person_id / generate_raw_person_id
- get_or_create_person / get_person / get_person_by_id / update_person_info
- update_user_impression / update_user_attitude
- get_nickname_history
- get_user_streams / get_user_recent_messages / resolve_user_id
- enrich_message_with_person_info
- 参数校验
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import person_api


def _make_helper_mock() -> MagicMock:
    """构造一个所有方法都是 AsyncMock 的 helper mock。"""
    helper = MagicMock()
    helper.generate_raw_person_id = MagicMock(return_value="qq:123456")
    helper.generate_person_id = MagicMock(return_value="hashed_id")
    helper.get_or_create_person = AsyncMock(return_value=(MagicMock(id=1), False))
    helper.get_person = AsyncMock(return_value=MagicMock(id=1))
    helper.get_person_by_id = AsyncMock(return_value=MagicMock(id=1))
    helper.update_person_info = AsyncMock(return_value=True)
    helper.update_user_impression = AsyncMock(return_value=True)
    helper.update_user_attitude = AsyncMock(return_value=60)
    helper.get_nickname_history = AsyncMock(return_value=[{"name": "Old", "retired_at": 1.0}])
    helper.get_user_streams = AsyncMock(return_value=[MagicMock()])
    helper.get_user_recent_messages = AsyncMock(return_value=[MagicMock()])
    helper.resolve_user_id = AsyncMock(return_value="123456")
    helper.enrich_message_with_person_info = AsyncMock(return_value={"message_id": "m1"})
    return helper


class TestPersonAPI:
    """测试 Person API。"""

    def test_api_version(self) -> None:
        """API_VERSION 应存在且非空。"""
        assert person_api.API_VERSION == "1.1.0"

    # ── 身份标识生成（同步） ──

    def test_generate_raw_person_id(self) -> None:
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=_make_helper_mock(),
        ):
            result = person_api.generate_raw_person_id("qq", "123456")
            assert result == "qq:123456"

    def test_generate_person_id(self) -> None:
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=_make_helper_mock(),
        ):
            result = person_api.generate_person_id("qq", "123456")
            assert result == "hashed_id"

    @pytest.mark.parametrize("platform,user_id", [("", "1"), ("qq", ""), (" ", "1")])
    def test_generate_person_id_validates_empty(self, platform: str, user_id: str) -> None:
        with pytest.raises(ValueError):
            person_api.generate_person_id(platform, user_id)

    # ── 用户记录管理（异步） ──

    @pytest.mark.asyncio
    async def test_get_or_create_person(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            person, is_new = await person_api.get_or_create_person(
                "qq", "123", nickname="N", cardname="C"
            )
            assert is_new is False
            helper.get_or_create_person.assert_awaited_once()
            kwargs = helper.get_or_create_person.call_args.kwargs
            assert kwargs["platform"] == "qq"
            assert kwargs["nickname"] == "N"
            assert kwargs["cardname"] == "C"

    @pytest.mark.asyncio
    async def test_get_person(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.get_person("qq", "123")
            assert result is not None
            helper.get_person.assert_awaited_once()
            assert helper.get_person.call_args.kwargs == {
                "platform": "qq",
                "user_id": "123",
            }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exists", [True, False])
    async def test_get_person_by_id(self, exists: bool) -> None:
        """按内部 ID 委托查询，人物不存在时原样返回 None。"""
        helper = _make_helper_mock()
        expected = MagicMock(user_id="example-user") if exists else None
        helper.get_person_by_id.return_value = expected
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            assert await person_api.get_person_by_id("example-person") is expected
        helper.get_person_by_id.assert_awaited_once_with("example-person")
        helper.get_or_create_person.assert_not_awaited()
        helper.update_person_info.assert_not_awaited()
        assert "get_person_by_id" in person_api.__all__

    @pytest.mark.asyncio
    @pytest.mark.parametrize("person_id", ["", " ", None, 123])
    async def test_get_person_by_id_rejects_invalid_id(self, person_id: Any) -> None:
        """无效 ID 在调用人物查询之前报错。"""
        with patch("src.app.plugin_system.api.person_api._get_user_query_helper") as lookup:
            with pytest.raises(ValueError, match="person_id"):
                await person_api.get_person_by_id(person_id)
        lookup.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exists", [True, False])
    async def test_get_person_by_id_sqlite_is_read_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exists: bool,
    ) -> None:
        """真实 SQLite 查询保留人物所有字段，缺失人物也不会被创建。"""
        from src.app.plugin_system.api.storage_api import PluginDatabase
        from src.core.models.sql_alchemy import PersonInfo
        from src.core.utils.user_query_helper import UserQueryHelper

        path = tmp_path / "people.sqlite3"
        database = PluginDatabase(str(path), [PersonInfo])
        await database.initialize()
        try:
            crud = database.crud(PersonInfo)
            await crud.create({
                "person_id": "example-person", "platform": "qq", "user_id": "example-user",
                "nickname": "Example", "interaction_count": 7,
                "last_interaction": 50.0, "updated_at": 40.0, "created_at": 30.0,
            })
            with patch("src.core.utils.user_query_helper.CRUDBase"):
                helper = UserQueryHelper()
            helper.person_crud = crud
            monkeypatch.setattr(person_api, "_get_user_query_helper", lambda: helper)
            before = await crud.get_by(person_id="example-person")
            assert before is not None
            snapshot = {column.name: getattr(before, column.name) for column in PersonInfo.__table__.columns}
            result = await person_api.get_person_by_id("example-person" if exists else "missing-person")
            if exists:
                assert result is not None and result.user_id == "example-user"
            else:
                assert result is None
            after = await crud.get_by(person_id="example-person")
            assert after is not None
            assert {column.name: getattr(after, column.name) for column in PersonInfo.__table__.columns} == snapshot
            assert await crud.count() == 1
        finally:
            await database.close()
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_update_person_info(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.update_person_info(
                "qq", "123", nickname="N", cardname="C"
            )
            assert result is True
            helper.update_person_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_user_impression(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.update_user_impression(
                "qq", "123", "friendly", short_impression="short"
            )
            assert result is True
            kwargs = helper.update_user_impression.call_args.kwargs
            assert kwargs["impression"] == "friendly"
            assert kwargs["short_impression"] == "short"

    @pytest.mark.asyncio
    async def test_update_user_impression_validates_empty(self) -> None:
        with pytest.raises(ValueError):
            await person_api.update_user_impression("qq", "1", "")

    @pytest.mark.asyncio
    async def test_update_user_attitude(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.update_user_attitude("qq", "123", 10)
            assert result == 60
            helper.update_user_attitude.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_user_attitude_rejects_non_int(self) -> None:
        with pytest.raises(ValueError):
            await person_api.update_user_attitude("qq", "123", "10")  # type: ignore[arg-type]

    # ── 名称变更历史（异步） ──

    @pytest.mark.asyncio
    async def test_get_nickname_history(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.get_nickname_history("qq", "123")
            assert result == [{"name": "Old", "retired_at": 1.0}]
            helper.get_nickname_history.assert_awaited_once()

    # ── 用户关联查询（异步） ──

    @pytest.mark.asyncio
    async def test_get_user_streams(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.get_user_streams("qq", "123")
            assert len(result) == 1
            helper.get_user_streams.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_user_recent_messages(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.get_user_recent_messages("qq", "123", limit=10)
            assert len(result) == 1
            kwargs = helper.get_user_recent_messages.call_args.kwargs
            assert kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_get_user_recent_messages_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            await person_api.get_user_recent_messages("qq", "1", limit=-5)

    @pytest.mark.asyncio
    async def test_resolve_user_id(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.resolve_user_id("qq", "Alice")
            assert result == "123456"
            helper.resolve_user_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enrich_message_with_person_info(self) -> None:
        helper = _make_helper_mock()
        with patch(
            "src.app.plugin_system.api.person_api._get_user_query_helper",
            return_value=helper,
        ):
            result = await person_api.enrich_message_with_person_info(MagicMock())
            assert result == {"message_id": "m1"}
            helper.enrich_message_with_person_info.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enrich_message_rejects_none(self) -> None:
        with pytest.raises(ValueError):
            await person_api.enrich_message_with_person_info(None)  # type: ignore[arg-type]

    # ── 集成：名称历史解析（来自 UserQueryHelper 的端到端校验） ──

    @pytest.mark.asyncio
    async def test_name_history_round_trip_through_helper(self) -> None:
        """端到端：让 person_api 调用真实 UserQueryHelper 的 get_nickname_history。"""
        from src.core.utils.user_query_helper import UserQueryHelper

        mock_person = MagicMock()
        mock_person.person_id = "hashed"
        mock_person.nickname_history = json.dumps(
            [
                {"name": "Alice", "retired_at": 100.0},
                {"name": "Alyssa", "retired_at": 200.0},
            ]
        )

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            with patch(
                "src.app.plugin_system.api.person_api._get_user_query_helper",
                return_value=helper,
            ):
                history = await person_api.get_nickname_history("qq", "123")
                assert len(history) == 2
                assert history[0]["name"] == "Alice"
                assert history[1]["name"] == "Alyssa"
                assert history[0]["retired_at"] == 100.0
