"""Temporary memo tests for Booku Memory."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from plugins.booku_memory.agent.tools import BookuTemporaryMemoTool
from plugins.booku_memory.config import BookuMemoryConfig
from plugins.booku_memory.plugin import BookuMemoryAgentPlugin
from plugins.booku_memory.service import (
    BookuMemoryMetadataRepository,
    BookuMemoryService,
    sync_booku_memory_actor_reminder,
)
from src.core.prompt import (
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
    reset_system_reminder_store,
)


@dataclass
class _DummyPlugin:
    config: Any = None


class _FakeMemoService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_temporary_memo(
        self,
        *,
        content: str,
        expire_hours: float = 2.0,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {"content": content, "expire_hours": expire_hours, "stream_id": stream_id}
        )
        return {
            "action": "create_temporary_memo",
            "mode": "created",
            "memo_id": "memo-1",
            "expires_at": 123.0,
            "active_memo_count": 1,
            "item": {
                "memo_id": "memo-1",
                "stream_id": stream_id or "",
                "content": content,
                "expires_at": 123.0,
            },
        }


def _fake_activate_stream(streams: dict[str, Any]) -> Any:
    async def _activate_stream(stream_id: str) -> Any:
        return streams.get(stream_id)

    return _activate_stream


@pytest.mark.asyncio
async def test_temporary_memo_tool_uses_default_expire_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_service = _FakeMemoService()
    monkeypatch.setattr("plugins.booku_memory.agent.tools._service", lambda _plugin: fake_service)

    tool = BookuTemporaryMemoTool(plugin=cast(Any, _DummyPlugin()))
    tool._bind_runtime_context(stream_id="stream-123")
    ok, payload = await tool.execute(content="follow up in another group")

    assert ok is True
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert fake_service.calls == [
        {
            "content": "follow up in another group",
            "expire_hours": 2.0,
            "stream_id": "stream-123",
        }
    ]


@pytest.mark.asyncio
async def test_temporary_memo_tool_rejects_empty_content() -> None:
    tool = BookuTemporaryMemoTool(plugin=cast(Any, _DummyPlugin()))
    ok, payload = await tool.execute(content="   ")

    assert ok is True
    assert isinstance(payload, dict)
    assert payload["ok"] is False
    assert "content" in str(payload["error"])


@pytest.mark.asyncio
async def test_create_temporary_memo_refreshes_duplicate_content(tmp_path: Path) -> None:
    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "memory.db")
    service = BookuMemoryService(plugin=cast(Any, _DummyPlugin(config=cfg)))
    reset_system_reminder_store()

    first = await service.create_temporary_memo(
        content="remember cross-group context",
        stream_id="stream-a",
    )
    second = await service.create_temporary_memo(
        content="remember cross-group context",
        expire_hours=4.0,
        stream_id="stream-a",
    )

    assert first["mode"] == "created"
    assert second["mode"] == "refreshed"
    assert first["memo_id"] == second["memo_id"]
    assert second["active_memo_count"] == 1
    assert float(second["expires_at"]) > float(first["expires_at"])


@pytest.mark.asyncio
async def test_create_temporary_memo_keeps_same_content_across_streams_separate(
    tmp_path: Path,
) -> None:
    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "memory_stream_split.db")
    service = BookuMemoryService(plugin=cast(Any, _DummyPlugin(config=cfg)))
    reset_system_reminder_store()

    first = await service.create_temporary_memo(
        content="same content different stream",
        stream_id="stream-a",
    )
    second = await service.create_temporary_memo(
        content="same content different stream",
        stream_id="stream-b",
    )

    assert first["mode"] == "created"
    assert second["mode"] == "created"
    assert first["memo_id"] != second["memo_id"]
    assert second["active_memo_count"] == 2


@pytest.mark.asyncio
async def test_temporary_memo_repository_persists_until_expired(tmp_path: Path) -> None:
    db_path = str(tmp_path / "memo_repo.db")
    repo = BookuMemoryMetadataRepository(db_path)
    await repo.initialize()

    now = time.time()
    memo_a, created_a = await repo.upsert_temporary_memo(
        content="memo A",
        stream_id="stream-a",
        expires_at=now + 7200,
        now=now,
    )
    memo_b, created_b = await repo.upsert_temporary_memo(
        content="memo B",
        stream_id="stream-b",
        expires_at=now + 3600,
        now=now,
    )
    await repo.close()

    reopened = BookuMemoryMetadataRepository(db_path)
    await reopened.initialize()
    active = await reopened.list_active_temporary_memos(now=now + 10)

    assert created_a is True
    assert created_b is True
    assert {memo.memo_id for memo in active} == {memo_a.memo_id, memo_b.memo_id}
    assert {memo.stream_id for memo in active} == {"stream-a", "stream-b"}

    cleaned = await reopened.cleanup_expired_temporary_memos(now=now + 8000)
    assert cleaned == 2
    assert await reopened.list_active_temporary_memos(now=now + 8000) == []
    await reopened.close()


@pytest.mark.asyncio
async def test_sync_booku_memory_actor_reminder_writes_temporary_memo_dynamic_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "memo_reminder.db")
    cfg.plugin.inject_system_prompt = True

    repo = BookuMemoryMetadataRepository(cfg.storage.metadata_db_path)
    await repo.initialize()
    await repo.upsert_temporary_memo(
        content="A 群等 B 群确认后统一回复",
        stream_id="stream-group",
        expires_at=time.time() + 7200,
    )
    await repo.close()

    reset_system_reminder_store()
    monkeypatch.setattr(
        "plugins.booku_memory.service.booku_memory_service.get_stream_manager",
        lambda: SimpleNamespace(
            _streams={},
            activate_stream=_fake_activate_stream(
                {
                    "stream-group": SimpleNamespace(
                        stream_id="stream-group",
                        chat_type="group",
                        stream_name="发布协作群",
                    )
                }
            ),
        ),
    )

    class _Plugin:
        config = cfg

    await sync_booku_memory_actor_reminder(_Plugin())

    items = get_system_reminder_store().get_items("actor", names=["临时备忘录"])
    assert len(items) == 1
    assert items[0].insert_type == SystemReminderInsertType.DYNAMIC
    assert items[0].consume_type == SystemReminderConsumeType.ONCE
    assert "不是长期记忆" in items[0].content
    assert "在聊天流stream-group（群聊，聊天流名发布协作群）中的备忘条目：" in items[0].content
    assert "统一回复" in items[0].content


@pytest.mark.asyncio
async def test_sync_booku_memory_actor_reminder_clears_expired_temporary_memo(
    tmp_path: Path,
) -> None:
    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "memo_expired.db")
    cfg.plugin.inject_system_prompt = True

    repo = BookuMemoryMetadataRepository(cfg.storage.metadata_db_path)
    await repo.initialize()
    await repo.upsert_temporary_memo(
        content="this should expire immediately",
        expires_at=time.time() - 1,
    )
    await repo.close()

    reset_system_reminder_store()

    class _Plugin:
        config = cfg

    await sync_booku_memory_actor_reminder(_Plugin())

    assert get_system_reminder_store().get("actor", names=["临时备忘录"]) == ""


@pytest.mark.asyncio
async def test_booku_memory_plugin_unload_clears_temporary_memo_reminder(tmp_path: Path) -> None:
    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "memo_plugin.db")
    cfg.plugin.inject_system_prompt = True

    repo = BookuMemoryMetadataRepository(cfg.storage.metadata_db_path)
    await repo.initialize()
    await repo.upsert_temporary_memo(
        content="plugin lifecycle memo",
        expires_at=time.time() + 7200,
    )
    await repo.close()

    reset_system_reminder_store()
    plugin = BookuMemoryAgentPlugin(config=cfg)

    await plugin.on_plugin_loaded()
    assert get_system_reminder_store().get("actor", names=["临时备忘录"]) != ""

    await plugin.on_plugin_unloaded()
    assert get_system_reminder_store().get("actor", names=["临时备忘录"]) == ""
