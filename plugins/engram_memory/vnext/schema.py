"""Engram Memory vNext Schema 初始化与版本校验。"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import select

from src.app.plugin_system.api.storage_api import PluginDatabase

from .models import ALL_MODELS, SchemaVersionModel

SCHEMA_KEY = "engram_memory_vnext"
SCHEMA_VERSION = 5


def upgrade_persona_audit(connection: sqlite3.Connection, source_version: int) -> None:
    """在调用者事务内升级已知人物审查结构及版本，不改写历史内容。"""
    if source_version not in {2, 3, 4}:
        raise RuntimeError(f"不支持自动升级的 Engram Schema: {source_version}")
    if source_version in {2, 3}:
        connection.execute(
            "ALTER TABLE engram_vnext_persona_update_log ADD COLUMN generator_version TEXT"
        )
        connection.execute(
            "ALTER TABLE engram_vnext_persona_update_log ADD COLUMN revision_no INTEGER "
            "CHECK (revision_no >= 1)"
        )
        connection.execute(
            "ALTER TABLE engram_vnext_persona_update_log ADD COLUMN impression_text TEXT"
        )
        connection.execute(
            "CREATE UNIQUE INDEX uq_engram_vnext_persona_revision "
            "ON engram_vnext_persona_update_log(person_id, revision_no)"
        )
    connection.execute(
        "ALTER TABLE engram_vnext_persona_update_log ADD COLUMN seen_revision_ids JSON"
    )
    updated = connection.execute(
        "UPDATE engram_vnext_schema_version SET version=?, applied_at=? "
        "WHERE schema_key=? AND version=?",
        (
            SCHEMA_VERSION,
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            SCHEMA_KEY,
            source_version,
        ),
    )
    if updated.rowcount != 1:
        raise RuntimeError("Engram Schema 版本记录不唯一或已变化")


class VNextSchema:
    """管理隔离的 vNext PluginDatabase。"""

    def __init__(self, db_path: str) -> None:
        """创建 Schema 管理器。"""
        self._db_path = Path(db_path)
        self.database = PluginDatabase(db_path, list(ALL_MODELS))

    @property
    def db_path(self) -> Path:
        """返回规范数据库路径，供关联的来源日志维护使用。"""
        return self._db_path

    def _check_existing_version(self) -> int | None:
        """在副本上识别已知结构，拒绝未知版本及缺少版本记录的已有库。"""
        if not self._db_path.is_file():
            return
        with tempfile.TemporaryDirectory(prefix="engram-schema-") as directory:
            probe = Path(directory) / "probe.db"
            shutil.copyfile(self._db_path, probe)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(f"{self._db_path}{suffix}")
                if sidecar.exists():
                    shutil.copyfile(sidecar, Path(f"{probe}{suffix}"))
            with closing(sqlite3.connect(probe)) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='engram_vnext_schema_version'"
                ).fetchone()
                if exists is None:
                    if (
                        connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
                        ).fetchone()
                        is not None
                    ):
                        raise RuntimeError(
                            "已有数据库缺少 Engram Schema 版本，拒绝隐式适配"
                        )
                    return
                row = connection.execute(
                    "SELECT version FROM engram_vnext_schema_version WHERE schema_key=?",
                    (SCHEMA_KEY,),
                ).fetchone()
                if row is None or row[0] not in {1, 2, 3, 4, SCHEMA_VERSION}:
                    raise RuntimeError(
                        "不支持的 Engram Schema 版本，拒绝自动修改未知结构"
                    )
                return int(row[0])

    def _migrate_existing(self, source_version: int) -> None:
        """备份一致快照后原位升级，DDL 和版本号同事务提交或回滚。"""
        with closing(sqlite3.connect(self._db_path, timeout=10)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "PRAGMA foreign_keys=OFF"
                if source_version == 1
                else "PRAGMA foreign_keys=ON"
            )
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT version FROM engram_vnext_schema_version WHERE schema_key=?",
                    (SCHEMA_KEY,),
                ).fetchone()
                if row == (SCHEMA_VERSION,):
                    return
                if row != (source_version,):
                    raise RuntimeError("Engram 数据库版本在迁移前已变化")
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("Engram 数据库完整性检查失败，拒绝自动迁移")
                backup_dir = self._db_path.parent / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / (
                    f"{self._db_path.stem}.schema-v{source_version}.{uuid4().hex}.db"
                )
                uri = f"file:{quote(self._db_path.absolute().as_posix(), safe='/:')}?mode=ro"
                try:
                    with closing(sqlite3.connect(uri, uri=True)) as source:
                        with closing(sqlite3.connect(backup_path)) as backup:
                            backup.execute("PRAGMA synchronous=FULL")
                            source.backup(backup)
                            if backup.execute("PRAGMA integrity_check").fetchone() != (
                                "ok",
                            ):
                                raise RuntimeError("Engram 自动迁移备份完整性检查失败")
                except BaseException:
                    backup_path.unlink(missing_ok=True)
                    raise
                if source_version == 1:
                    self._upgrade_legacy(connection, backup_path)
                else:
                    upgrade_persona_audit(connection, source_version)
                if (
                    connection.execute("PRAGMA foreign_key_check").fetchone()
                    is not None
                ):
                    raise RuntimeError("Engram 自动迁移后外键检查失败")
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("Engram 自动迁移后完整性检查失败")

    def _upgrade_legacy(
        self, connection: sqlite3.Connection, backup_path: Path
    ) -> None:
        """按共享规则转换旧快照，在调用者事务内重建插件表并保留退役归档。"""
        from .schema_migration import _quote_identifier, migrate_snapshot

        with tempfile.TemporaryDirectory(
            prefix="engram-upgrade-", dir=backup_path.parent
        ) as directory:
            staged_path = Path(directory) / "migrated.db"
            migrate_snapshot(backup_path, staged_path)
            for suffix in (".v1-retired.jsonl", ".migration.json"):
                shutil.copyfile(
                    staged_path.with_suffix(suffix), backup_path.with_suffix(suffix)
                )
            with closing(sqlite3.connect(staged_path)) as staged:
                tables = staged.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                old_tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                for (table,) in old_tables:
                    if table.startswith("engram_vnext_"):
                        connection.execute(f"DROP TABLE {_quote_identifier(table)}")
                for table, definition in tables:
                    connection.execute(definition)
                for table, definition in tables:
                    quoted = _quote_identifier(table)
                    columns = staged.execute(f"PRAGMA table_info({quoted})").fetchall()
                    placeholders = ",".join("?" for column in columns)
                    connection.executemany(
                        f"INSERT INTO {quoted} VALUES ({placeholders})",
                        staged.execute(f"SELECT * FROM {quoted}"),
                    )
                for (definition,) in staged.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
                ):
                    connection.execute(definition)

    async def initialize(self) -> None:
        """自动备份升级已知旧结构，再打开运行时数据库并校验版本。"""
        source_version = self._check_existing_version()
        if source_version is not None and source_version < SCHEMA_VERSION:
            self._migrate_existing(source_version)
        await self.database.initialize()
        async with self.database.session() as session:
            result = await session.execute(
                select(SchemaVersionModel).where(
                    SchemaVersionModel.schema_key == SCHEMA_KEY
                )
            )
            current = result.scalar_one_or_none()
            if current is None:
                session.add(
                    SchemaVersionModel(
                        schema_key=SCHEMA_KEY,
                        version=SCHEMA_VERSION,
                        applied_at=datetime.now(UTC),
                    )
                )
            elif current.version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"不支持的 Engram vNext Schema 版本: {current.version}，"
                    f"当前代码要求 {SCHEMA_VERSION}"
                )

    async def close(self) -> None:
        """关闭数据库连接。"""
        await self.database.close()
