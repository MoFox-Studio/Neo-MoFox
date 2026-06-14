"""Tests for session usage_total persistence.

Verifies that usage_total is correctly serialized/deserialized
in SessionData and that old session files without usage_total
are handled gracefully.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from plugins.coding_agent.session_store import SessionData, SessionStore


# ── SessionData usage_total field ─────────────────────────────────────


class TestSessionDataUsageTotal:
    """Verify usage_total field behavior on SessionData."""

    def test_default_usage_total_is_empty_dict(self) -> None:
        data = SessionData(
            session_id="test-1",
            working_directory="/tmp",
        )
        assert data.usage_total == {}

    def test_usage_total_preserved(self) -> None:
        usage = {
            "claude-sonnet-4-5": {
                "prompt_tokens": 12345,
                "completion_tokens": 6789,
                "cache_hit_tokens": 1000,
                "cache_write_tokens": 50,
                "cost": 0.1234,
                "request_count": 12,
            }
        }
        data = SessionData(
            session_id="test-2",
            working_directory="/tmp",
            usage_total=usage,
        )
        assert data.usage_total == usage
        assert data.usage_total["claude-sonnet-4-5"]["prompt_tokens"] == 12345


# ── SessionStore persistence ──────────────────────────────────────────


class TestSessionStoreUsagePersistence:
    """Verify usage_total survives save/load cycle."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> SessionStore:
        return SessionStore(str(tmp_path))

    @pytest.mark.asyncio
    async def test_save_and_load_with_usage(self, store: SessionStore) -> None:
        usage = {
            "gpt-4o": {
                "prompt_tokens": 5000,
                "completion_tokens": 2000,
                "cache_hit_tokens": 500,
                "cache_write_tokens": 0,
                "reasoning_tokens": 300,
                "cost": 0.05,
                "request_count": 8,
            }
        }
        data = SessionData(
            session_id="sess-1",
            working_directory="/tmp/project",
            title="Test Session",
            created_at=1000.0,
            phase="ready",
            usage_total=usage,
        )
        await store.save("sess-1", data)

        loaded = await store.load("sess-1")
        assert loaded is not None
        assert loaded.usage_total == usage
        assert loaded.usage_total["gpt-4o"]["prompt_tokens"] == 5000
        assert loaded.usage_total["gpt-4o"]["reasoning_tokens"] == 300
        assert loaded.usage_total["gpt-4o"]["cost"] == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_load_old_session_without_usage(self, store: SessionStore) -> None:
        """Old session files without usage_total should default to empty dict."""
        # Manually create a session file without usage_total
        sessions_dir = Path(store._sessions_dir)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        old_data = {
            "session_id": "old-sess",
            "working_directory": "/tmp",
            "title": "Old Session",
            "created_at": 900.0,
            "last_active_at": 950.0,
            "message_count": 5,
            "phase": "ready",
            "project_context": None,
            "payloads": [],
            "linked_directories": [],
            # Note: no usage_total field
        }
        session_file = sessions_dir / "old-sess.json"
        session_file.write_text(json.dumps(old_data, ensure_ascii=False), encoding="utf-8")

        loaded = await store.load("old-sess")
        assert loaded is not None
        assert loaded.usage_total == {}

    @pytest.mark.asyncio
    async def test_save_and_load_empty_usage(self, store: SessionStore) -> None:
        data = SessionData(
            session_id="sess-empty",
            working_directory="/tmp",
            usage_total={},
        )
        await store.save("sess-empty", data)

        loaded = await store.load("sess-empty")
        assert loaded is not None
        assert loaded.usage_total == {}

    @pytest.mark.asyncio
    async def test_multiple_models_in_usage(self, store: SessionStore) -> None:
        usage = {
            "gpt-4o": {
                "prompt_tokens": 5000,
                "completion_tokens": 2000,
                "cost": 0.05,
                "request_count": 8,
            },
            "claude-sonnet-4-5": {
                "prompt_tokens": 12345,
                "completion_tokens": 6789,
                "cost": 0.1234,
                "request_count": 12,
            },
        }
        data = SessionData(
            session_id="sess-multi",
            working_directory="/tmp",
            usage_total=usage,
        )
        await store.save("sess-multi", data)

        loaded = await store.load("sess-multi")
        assert loaded is not None
        assert len(loaded.usage_total) == 2
        assert loaded.usage_total["gpt-4o"]["prompt_tokens"] == 5000
        assert loaded.usage_total["claude-sonnet-4-5"]["request_count"] == 12