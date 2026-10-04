"""Booku 到 Engram 根目录入口、只读预览和取消确认的隔离测试。"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from plugins.engram_memory.scripts import migrate_booku
from scripts import migrate_booku_to_engram


def test_migration_entry_preserves_arguments_and_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根目录入口转交原始参数并保留迁移器的失败退出码。"""
    arguments = ["migrate_booku_to_engram.py", "--preview", "--batch-size", "16"]
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(sys, "path", list(sys.path))

    def run_module(module_name: str, *, run_name: str) -> dict[str, Any]:
        """记录入口转发并模拟迁移器的非零退出。"""
        assert module_name == "plugins.engram_memory.scripts.migrate_booku"
        assert run_name == "__main__"
        assert sys.argv == arguments
        assert sys.path[0] == str(
            Path(migrate_booku_to_engram.__file__).resolve().parent.parent
        )
        raise SystemExit(7)

    monkeypatch.setattr(migrate_booku_to_engram.runpy, "run_module", run_module)
    with pytest.raises(SystemExit) as error:
        migrate_booku_to_engram.main()
    assert error.value.code == 7


@pytest.fixture
def booku_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """创建虚构记忆和知识文档，禁止测试调用真实向量模型。"""
    monkeypatch.setattr(migrate_booku, "ROOT", tmp_path)
    source = tmp_path / "data/booku_memory/metadata.db"
    source.parent.mkdir(parents=True)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute(
            "CREATE TABLE booku_memory_records ("
            "memory_id TEXT PRIMARY KEY, bucket TEXT NOT NULL, "
            "title TEXT NOT NULL, content TEXT NOT NULL, "
            "memory_type TEXT, person_id TEXT, is_deleted INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO booku_memory_records VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("example-memory", "memory", "Plan", "Example plan", "event", None, 0),
                ("example-document", "knowledge", "Guide", "Example text", "knowledge", None, 0),
            ],
        )
        connection.commit()

    def reject_model_call(*args: Any, **kwargs: Any) -> None:
        """预览和取消操作不得请求真实模型。"""
        raise AssertionError("Unexpected embedding request")

    monkeypatch.setattr(
        migrate_booku.llm_api, "create_embedding_request", reject_model_call
    )
    return source


@pytest.mark.asyncio
async def test_preview_preserves_source_and_creates_no_output(
    booku_source: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """预览区分记忆与知识文档，不改来源也不生成迁移目录。"""
    monkeypatch.setattr(sys, "argv", ["migrate_booku_to_engram.py", "--preview"])
    before = migrate_booku.source_fingerprints(booku_source)
    assert await migrate_booku.main() == 0
    output = capsys.readouterr().out
    assert "待迁移记忆：1 条" in output
    assert "跳过知识文档：1 条" in output
    assert migrate_booku.source_fingerprints(booku_source) == before
    assert not (migrate_booku.ROOT / "data/engram_memory/booku-import").exists()


@pytest.mark.asyncio
async def test_cancel_preserves_source_and_creates_no_output(
    booku_source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未输入迁移确认文字时，来源及当前运行配置保持不变。"""
    monkeypatch.setattr(sys, "argv", ["migrate_booku_to_engram.py"])
    monkeypatch.setattr("builtins.input", lambda prompt: "cancel")
    before = migrate_booku.source_fingerprints(booku_source)
    assert await migrate_booku.main() == 0
    assert migrate_booku.source_fingerprints(booku_source) == before
    assert not (migrate_booku.ROOT / "data/engram_memory/booku-import").exists()
    assert not (migrate_booku.ROOT / "config").exists()