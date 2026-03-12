#!/usr/bin/env python3
"""数据库引擎迁移脚本

支持在不同数据库之间迁移数据：
- SQLite <-> PostgreSQL

使用方法:
    python scripts/migrate_database.py --help
    python scripts/migrate_database.py --source sqlite --target postgresql
    python scripts/migrate_database.py --source postgresql --target sqlite --batch-size 5000

    # 交互式向导模式（推荐）
    python scripts/migrate_database.py

注意事项:
1. 迁移前请备份源数据库
2. 目标数据库应该是空的或不存在的（脚本会自动创建表）
3. 迁移过程可能需要较长时间，请耐心等待
4. 迁移到 PostgreSQL 时，脚本会自动：
   - 修复布尔列类型（SQLite INTEGER -> PostgreSQL BOOLEAN）
   - 重置序列值（避免主键冲突）

实现细节:
- 使用同步 SQLAlchemy 进行数据库连接和元数据管理（不依赖框架的异步引擎）
- 配置从 config/core.toml 的 [database] 节读取（与框架共享同一份配置）
- 采用流式迁移，避免一次性加载过多数据
- 支持 SQLite、PostgreSQL 之间的互相迁移
- 批量插入失败时自动降级为逐行插入，最大程度保留数据
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from getpass import getpass
from pathlib import Path

# 添加项目根目录到路径，使脚本能独立运行
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))

# =============================================================================
# 设置日志
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)

# =============================================================================
# 导入第三方库（延迟导入以便友好报错）
# =============================================================================

try:
    import tomllib
except ImportError:
    tomllib = None  # type: ignore[assignment]

from collections.abc import Iterable
from datetime import datetime as dt
from typing import Any

from sqlalchemy import (
    MetaData,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy import (
    types as sqltypes,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

# Windows 终端 UTF-8 兼容
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
    except Exception:
        pass


# =============================================================================
# 配置相关工具
# =============================================================================

PROJECT_ROOT = str(_project_root)


def load_core_config() -> dict:
    """加载 config/core.toml 配置文件。

    Returns:
        配置字典，若文件不存在或解析失败则返回空字典
    """
    config_path = os.path.join(PROJECT_ROOT, "config", "core.toml")
    if not os.path.exists(config_path):
        logger.warning("配置文件不存在: %s", config_path)
        return {}

    if tomllib is None:
        logger.warning("当前 Python 版本不支持 tomllib，请使用 Python 3.11+")
        return {}

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        return config
    except Exception as e:
        logger.error("解析配置文件失败: %s", e)
        return {}


def get_database_config_from_toml(db_type: str) -> dict | None:
    """从 config/core.toml 的 [database] 节读取数据库配置。

    Args:
        db_type: 数据库类型，支持 "sqlite" 或 "postgresql"

    Returns:
        数据库配置字典，若对应配置不存在则返回 None
    """
    config_data = load_core_config()
    if not config_data:
        return None

    # 新框架配置统一在 [database] 节下
    db_config = config_data.get("database", {})

    if db_type == "sqlite":
        sqlite_path = db_config.get("sqlite_path") or "data/MoFox.db"
        if not os.path.isabs(sqlite_path):
            sqlite_path = os.path.join(PROJECT_ROOT, sqlite_path)
        return {"path": sqlite_path}

    elif db_type == "postgresql":
        return {
            "host": db_config.get("postgresql_host") or "localhost",
            "port": db_config.get("postgresql_port") or 5432,
            "database": db_config.get("postgresql_database") or "mofox",
            "user": db_config.get("postgresql_user") or "postgres",
            "password": db_config.get("postgresql_password") or "",
            # core.toml 暂无 schema 配置项，默认 public
            "schema": db_config.get("postgresql_schema") or "public",
        }

    return None


# =============================================================================
# 数据库连接相关
# =============================================================================


def create_sqlite_engine(sqlite_path: str) -> Engine:
    """创建同步 SQLite 引擎。

    Args:
        sqlite_path: SQLite 数据库文件路径

    Returns:
        SQLAlchemy Engine 实例
    """
    if not os.path.isabs(sqlite_path):
        sqlite_path = os.path.join(PROJECT_ROOT, sqlite_path)

    # 确保目录存在
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)

    url = f"sqlite:///{sqlite_path}"
    logger.info("使用 SQLite 数据库: %s", sqlite_path)
    engine = create_engine(
        url,
        future=True,
        connect_args={
            "timeout": 30,
            "check_same_thread": False,
        },
    )
    # 减少"database is locked"错误
    with engine.connect() as conn:
        conn.execute(text("PRAGMA busy_timeout=30000"))
    return engine


def create_postgresql_engine(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    schema: str = "public",
) -> Engine:
    """创建同步 PostgreSQL 引擎。

    Args:
        host: 服务器地址
        port: 服务器端口
        database: 数据库名
        user: 用户名
        password: 密码
        schema: Schema 名称

    Returns:
        SQLAlchemy Engine 实例
    """
    # 解决 Windows 上 psycopg2 编码问题
    os.environ.setdefault("PGCLIENTENCODING", "utf-8")

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        logger.error("需要安装 psycopg2-binary 才能连接 PostgreSQL: uv add psycopg2-binary")
        raise

    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
    logger.info("使用 PostgreSQL 数据库: %s@%s:%s/%s (schema=%s)", user, host, port, database, schema)
    engine = create_engine(url, future=True)
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path TO {schema}"))
    return engine


def create_engine_by_type(db_type: str, config: dict) -> Engine:
    """根据数据库类型创建对应的 SQLAlchemy Engine。

    Args:
        db_type: 数据库类型，支持 sqlite/postgresql
        config: 配置字典

    Returns:
        SQLAlchemy Engine 实例
    """
    db_type = db_type.lower()
    if db_type == "sqlite":
        return create_sqlite_engine(config["path"])
    elif db_type == "postgresql":
        return create_postgresql_engine(
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            schema=config.get("schema", "public"),
        )
    else:
        raise ValueError(f"不支持的数据库类型: {db_type}")


# =============================================================================
# 工具函数
# =============================================================================


def chunked_iterable(iterable: Iterable, size: int) -> Iterable[list]:
    """将可迭代对象按指定大小分块。

    Args:
        iterable: 可迭代对象
        size: 每块大小

    Yields:
        分块列表
    """
    chunk: list[Any] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def get_table_row_count(conn: Connection, table: Table) -> int:
    """获取表的行数。

    Args:
        conn: 数据库连接
        table: 表对象

    Returns:
        行数，获取失败时返回 0
    """
    try:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table.name}"))
        return int(result.scalar() or 0)
    except SQLAlchemyError as e:
        logger.warning("获取表行数失败 %s: %s", table.name, e)
        return 0


def convert_value_for_target(
    val: Any,
    col_name: str,
    source_col_type: Any,
    target_col_type: Any,
    target_dialect: str,
    target_col_nullable: bool = True,
) -> Any:
    """转换值以适配目标数据库类型。

    处理以下情况:
    1. 空字符串日期时间 -> None
    2. SQLite INTEGER (0/1) -> PostgreSQL BOOLEAN
    3. 字符串日期时间 -> datetime 对象
    4. 对于 NOT NULL 列，提供合适的默认值

    Args:
        val: 原始值
        col_name: 列名
        source_col_type: 源列类型
        target_col_type: 目标列类型
        target_dialect: 目标数据库方言名称
        target_col_nullable: 目标列是否允许 NULL

    Returns:
        转换后的值
    """
    target_type_name = target_col_type.__class__.__name__.upper()
    source_col_type.__class__.__name__.upper()

    # 处理 None 值
    if val is None:
        if not target_col_nullable:
            # 按类型提供默认值
            if target_type_name == "BOOLEAN" or isinstance(target_col_type, sqltypes.Boolean):
                return False
            if target_type_name in ("INTEGER", "BIGINT", "SMALLINT") or isinstance(target_col_type, sqltypes.Integer):
                return 0
            if target_type_name in ("FLOAT", "DOUBLE", "REAL", "NUMERIC", "DECIMAL", "DOUBLE_PRECISION") or isinstance(target_col_type, sqltypes.Float):
                return 0.0
            if target_type_name in ("DATETIME", "TIMESTAMP") or isinstance(target_col_type, sqltypes.DateTime):
                return dt.now()
            if target_type_name in ("VARCHAR", "STRING", "TEXT") or isinstance(target_col_type, (sqltypes.String, sqltypes.Text)):
                return ""
            return ""
        return None

    # Boolean 转换：SQLite 存储为 INTEGER (0/1)
    if target_type_name == "BOOLEAN" or isinstance(target_col_type, sqltypes.Boolean):
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            val_lower = val.lower().strip()
            if val_lower in ("true", "1", "yes"):
                return True
            elif val_lower in ("false", "0", "no", ""):
                return False
        return bool(val) if val else False

    # DateTime 转换
    if target_type_name in ("DATETIME", "TIMESTAMP") or isinstance(target_col_type, sqltypes.DateTime):
        if isinstance(val, dt):
            return val
        if isinstance(val, str):
            val = val.strip()
            if val == "":
                return None
            for fmt in [
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            ]:
                try:
                    return dt.strptime(val, fmt)
                except ValueError:
                    continue
            try:
                return dt.fromisoformat(val)
            except ValueError:
                logger.warning("无法解析日期时间字符串 '%s' (列: %s)，设为 None", val, col_name)
                return None
        if isinstance(val, (int, float)) and val > 0:
            try:
                return dt.fromtimestamp(val)
            except (OSError, ValueError, OverflowError):
                return None
        return None

    # Float 转换
    if target_type_name == "FLOAT" or isinstance(target_col_type, sqltypes.Float):
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            val = val.strip()
            if val == "":
                return None
            try:
                return float(val)
            except ValueError:
                return None
        return val

    # Integer 转换
    if target_type_name == "INTEGER" or isinstance(target_col_type, sqltypes.Integer):
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            val = val.strip()
            if val == "":
                return None
            try:
                return int(float(val))
            except ValueError:
                return None
        return val

    return val


def copy_table_structure(source_table: Table, target_metadata: MetaData, target_engine: Engine) -> Table:
    """复制表结构到目标数据库。

    Args:
        source_table: 源表对象
        target_metadata: 目标元数据对象
        target_engine: 目标数据库引擎

    Returns:
        目标表对象
    """
    target_is_sqlite = target_engine.dialect.name == "sqlite"
    target_is_pg = target_engine.dialect.name == "postgresql"

    columns = []
    for c in source_table.columns:
        new_col = c.copy()

        # SQLite 不支持 nextval 等 server_default
        if target_is_sqlite:
            new_col.server_default = None

        # PostgreSQL 特有类型修正
        if target_is_pg:
            col_type = new_col.type
            if isinstance(col_type, sqltypes.DateTime) or col_type.__class__.__name__ in {"DATETIME", "DateTime"}:
                new_col.type = sqltypes.DateTime()
            elif isinstance(col_type, sqltypes.Text) and getattr(col_type, "length", None):
                new_col.type = sqltypes.String(length=col_type.length)

        columns.append(new_col)

    # 不复制表级约束，避免"Set changed size during iteration"
    target_table = Table(
        source_table.name,
        target_metadata,
        *columns,
    )
    target_metadata.create_all(target_engine, tables=[target_table])
    return target_table


def migrate_table_data(
    source_conn: Connection,
    target_engine: Engine,
    source_table: Table,
    target_table: Table,
    batch_size: int = 1000,
    target_dialect: str = "postgresql",
    row_limit: int | None = None,
) -> tuple[int, int]:
    """迁移单个表的数据。

    Args:
        source_conn: 源数据库连接
        target_engine: 目标数据库引擎
        source_table: 源表对象
        target_table: 目标表对象
        batch_size: 每批次处理大小
        target_dialect: 目标数据库方言 (sqlite/postgresql)
        row_limit: 最大迁移行数，None 表示不限制

    Returns:
        (迁移行数, 错误数量)
    """
    total_rows = get_table_row_count(source_conn, source_table)
    logger.info(
        "开始迁移表: %s (共 %s 行)",
        source_table.name,
        total_rows if total_rows else "未知",
    )

    migrated_rows = 0
    error_count = 0
    conversion_warnings = 0

    target_cols_by_name = {c.key: c for c in target_table.columns}

    # 使用原始 SQL 查询避免 SQLAlchemy 自动类型转换
    try:
        col_names = [c.key for c in source_table.columns]
        if row_limit:
            raw_sql = text(
                f"SELECT {', '.join(col_names)} FROM {source_table.name} ORDER BY id DESC LIMIT {row_limit}"
            )
            logger.info("  限制迁移最新 %d 行", row_limit)
        else:
            raw_sql = text(f"SELECT {', '.join(col_names)} FROM {source_table.name}")
        result = source_conn.execute(raw_sql)
    except SQLAlchemyError as e:
        logger.error("查询表 %s 失败: %s", source_table.name, e)
        return 0, 1

    def insert_batch(rows: list[dict]) -> None:
        """每批次使用独立事务，失败时降级为逐行插入。"""
        nonlocal migrated_rows, error_count
        if not rows:
            return
        try:
            with target_engine.begin() as target_conn:
                target_conn.execute(target_table.insert(), rows)
            migrated_rows += len(rows)
            logger.info("  已迁移 %d/%s 行", migrated_rows, total_rows or "?")
        except SQLAlchemyError as e:
            logger.warning("批量插入失败，降级为逐行插入 (共 %d 行): %s", len(rows), str(e)[:200])
            for row in rows:
                try:
                    with target_engine.begin() as target_conn:
                        target_conn.execute(target_table.insert(), [row])
                    migrated_rows += 1
                except SQLAlchemyError as row_e:
                    row_id = row.get("id", "unknown")
                    logger.error("插入行失败 (id=%s): %s", row_id, str(row_e)[:200])
                    error_count += 1
            logger.info("  逐行插入完成，已迁移 %d/%s 行", migrated_rows, total_rows or "?")

    batch: list[dict] = []
    null_char_replacements = 0
    col_list = list(source_table.columns)
    col_name_to_idx = {c.key: idx for idx, c in enumerate(col_list)}

    for row in result:
        row_dict = {}
        for col in col_list:
            col_key = col.key
            col_idx = col_name_to_idx[col_key]
            val = row[col_idx]

            # 移除 NUL 字符（PostgreSQL TEXT 列不允许包含 \x00）
            if isinstance(val, str) and "\x00" in val:
                val = val.replace("\x00", "")
                null_char_replacements += 1

            target_col = target_cols_by_name.get(col_key)
            if target_col is not None:
                try:
                    val = convert_value_for_target(
                        val=val,
                        col_name=col_key,
                        source_col_type=col.type,
                        target_col_type=target_col.type,
                        target_dialect=target_dialect,
                        target_col_nullable=target_col.nullable if target_col.nullable is not None else True,
                    )
                except Exception as e:
                    conversion_warnings += 1
                    if conversion_warnings <= 5:
                        logger.warning(
                            "值转换异常 (表=%s, 列=%s, 值=%r): %s",
                            source_table.name, col_key, val, e
                        )

            row_dict[col_key] = val

        batch.append(row_dict)
        if len(batch) >= batch_size:
            insert_batch(batch)
            batch = []

    if batch:
        insert_batch(batch)

    logger.info(
        "完成迁移表: %s (成功: %d 行, 失败: %d 行)",
        source_table.name,
        migrated_rows,
        error_count,
    )
    if null_char_replacements:
        logger.warning(
            "表 %s 中 %d 个字符串值包含 NUL 已被移除后写入目标库",
            source_table.name,
            null_char_replacements,
        )
    if conversion_warnings:
        logger.warning(
            "表 %s 中 %d 个值发生类型转换警告",
            source_table.name,
            conversion_warnings,
        )

    return migrated_rows, error_count


def confirm_action(prompt: str, default: bool = False) -> bool:
    """在终端中询问用户是否确认。

    Args:
        prompt: 提示信息
        default: 默认值

    Returns:
        用户是否确认
    """
    while True:
        if default:
            choice = input(f"{prompt} [Y/n]: ").strip().lower()
            if choice == "":
                return True
        else:
            choice = input(f"{prompt} [y/N]: ").strip().lower()
            if choice == "":
                return False

        if choice in ("y", "yes"):
            return True
        elif choice in ("n", "no"):
            return False
        else:
            print("请输入 y 或 n")


# =============================================================================
# 迁移器实现
# =============================================================================


class DatabaseMigrator:
    """通用数据库迁移器，支持 SQLite ↔ PostgreSQL 双向迁移。"""

    def __init__(
        self,
        source_type: str,
        target_type: str,
        batch_size: int = 1000,
        source_config: dict | None = None,
        target_config: dict | None = None,
        skip_tables: set | None = None,
        only_tables: set | None = None,
        no_create_tables: bool = False,
    ) -> None:
        """初始化迁移器。

        Args:
            source_type: 源数据库类型 (sqlite/postgresql)
            target_type: 目标数据库类型 (sqlite/postgresql)
            batch_size: 批量处理大小
            source_config: 源数据库配置，None 时从 config/core.toml 读取
            target_config: 目标数据库配置，必须显式传入
            skip_tables: 要跳过的表名集合
            only_tables: 只迁移的表名集合（设置后忽略 skip_tables）
            no_create_tables: 跳过创建表结构（假设目标表已存在）
        """
        self.source_type = source_type.lower()
        self.target_type = target_type.lower()
        self.batch_size = batch_size
        self.source_config = source_config
        self.target_config = target_config
        self.skip_tables = skip_tables or set()
        self.only_tables = only_tables or set()
        self.no_create_tables = no_create_tables

        self._validate_database_types()

        self.source_engine: Any = None
        self.target_engine: Any = None
        self.metadata = MetaData()

        self.stats: dict[str, Any] = {
            "tables_migrated": 0,
            "rows_migrated": 0,
            "errors": [],
            "start_time": None,
            "end_time": None,
        }

    def _validate_database_types(self) -> None:
        """验证数据库类型是否受支持。"""
        supported_types = {"sqlite", "postgresql"}
        if self.source_type not in supported_types:
            raise ValueError(f"不支持的源数据库类型: {self.source_type}")
        if self.target_type not in supported_types:
            raise ValueError(f"不支持的目标数据库类型: {self.target_type}")

    def _load_source_config(self) -> dict:
        """加载源数据库配置，优先使用传入的配置，否则从 core.toml 读取。"""
        if self.source_config:
            logger.info("使用传入的源数据库配置")
            return self.source_config

        logger.info("未提供源数据库配置，尝试从 config/core.toml 读取")
        config = get_database_config_from_toml(self.source_type)
        if not config:
            raise ValueError("无法从 config/core.toml 中读取源数据库配置，请检查 [database] 节")

        logger.info("成功从配置文件读取源数据库配置")
        return config

    def _load_target_config(self) -> dict:
        """加载目标数据库配置（必须显式传入）。"""
        if not self.target_config:
            raise ValueError("未提供目标数据库配置，请通过命令行参数指定或在交互模式中输入")
        logger.info("使用传入的目标数据库配置")
        return self.target_config

    def _connect_databases(self) -> None:
        """连接源数据库和目标数据库，并反射源库元数据。"""
        source_config = self._load_source_config()
        target_config = self._load_target_config()

        # 防止 SQLite 源与目标指向同一文件
        if (
            self.source_type == "sqlite"
            and self.target_type == "sqlite"
            and os.path.abspath(source_config.get("path", "")) == os.path.abspath(target_config.get("path", ""))
        ):
            raise ValueError("源数据库与目标数据库不能是同一个 SQLite 文件，请为目标指定不同的路径")

        self.source_engine = create_engine_by_type(self.source_type, source_config)
        self.target_engine = create_engine_by_type(self.target_type, target_config)

        logger.info("正在反射源数据库元数据...")
        self.metadata.reflect(bind=self.source_engine)
        logger.info("发现 %d 张表: %s", len(self.metadata.tables), ", ".join(self.metadata.tables.keys()))

    def _get_tables_in_dependency_order(self) -> list[Table]:
        """获取按外键依赖顺序排列的表列表（拓扑排序）。"""
        inspector = inspect(self.source_engine)

        dependencies: dict[str, set[str]] = {name: set() for name in self.metadata.tables}

        for table_name in self.metadata.tables.keys():
            for fk in inspector.get_foreign_keys(table_name):
                referred = fk["referred_table"]
                if referred in dependencies:
                    dependencies[table_name].add(referred)

        sorted_tables: list[Table] = []
        visited: set[str] = set()
        temp_mark: set[str] = set()

        def visit(table_name: str) -> None:
            if table_name in visited:
                return
            if table_name in temp_mark:
                logger.warning("检测到循环依赖，表: %s", table_name)
                return
            temp_mark.add(table_name)
            for dep in dependencies[table_name]:
                visit(dep)
            temp_mark.remove(table_name)
            visited.add(table_name)
            sorted_tables.append(self.metadata.tables[table_name])

        for table_name in dependencies:
            if table_name not in visited:
                visit(table_name)

        return sorted_tables

    def _drop_target_tables(self) -> None:
        """提示用户是否删除目标数据库中已有的表。"""
        if self.target_engine is None:
            logger.warning("目标数据库引擎尚未初始化，无法删除表")
            return

        with self.target_engine.begin() as conn:
            inspector = inspect(conn)
            existing_tables = inspector.get_table_names()

            if not existing_tables:
                logger.info("目标数据库中没有已存在的表，无需删除")
                return

            logger.info("目标数据库中的当前表: %s", ", ".join(existing_tables))
            if confirm_action("是否删除目标数据库中现有的表？此操作不可撤销", default=False):
                for table_name in existing_tables:
                    try:
                        logger.info("删除目标数据库表: %s", table_name)
                        conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
                    except SQLAlchemyError as e:
                        logger.error("删除 %s 失败: %s", table_name, e)
                        self.stats["errors"].append(f"删除 {table_name} 失败: {e}")
            else:
                logger.info("跳过删除目标数据库中的表，继续迁移过程")

    def migrate(self) -> None:
        """执行完整的迁移操作。"""
        self.stats["start_time"] = time.time()

        self._connect_databases()

        tables = self._get_tables_in_dependency_order()
        logger.info("按依赖顺序迁移表: %s", ", ".join(t.name for t in tables))

        # 过滤 only_tables
        if self.only_tables:
            tables = [t for t in tables if t.name in self.only_tables]
            logger.info("只迁移指定的表: %s", ", ".join(t.name for t in tables))
            if not tables:
                logger.warning("没有找到任何匹配 --only-tables 的表")
                return

        if not self.no_create_tables:
            self._drop_target_tables()

        target_dialect = self.target_engine.dialect.name

        with self.source_engine.connect() as source_conn:
            for source_table in tables:
                # only_tables 未设置时才检查 skip_tables
                if not self.only_tables and source_table.name in self.skip_tables:
                    logger.info("跳过表: %s", source_table.name)
                    continue

                try:
                    if self.no_create_tables:
                        target_metadata = MetaData()
                        target_metadata.reflect(bind=self.target_engine, only=[source_table.name])
                        target_table = target_metadata.tables.get(source_table.name)
                        if target_table is None:
                            logger.error(
                                "目标数据库中不存在表: %s，请先创建或移除 --no-create-tables",
                                source_table.name,
                            )
                            self.stats["errors"].append(f"目标数据库中不存在表: {source_table.name}")
                            continue
                        logger.info("使用目标数据库中已存在的表结构: %s", source_table.name)
                    else:
                        target_table = copy_table_structure(source_table, MetaData(), self.target_engine)

                    migrated_rows, error_count = migrate_table_data(
                        source_conn,
                        self.target_engine,
                        source_table,
                        target_table,
                        batch_size=self.batch_size,
                        target_dialect=target_dialect,
                        row_limit=None,
                    )

                    self.stats["tables_migrated"] += 1
                    self.stats["rows_migrated"] += migrated_rows
                    if error_count > 0:
                        self.stats["errors"].append(f"表 {source_table.name} 迁移失败 {error_count} 行")

                except Exception as e:
                    logger.error("迁移表 %s 时发生错误: %s", source_table.name, e)
                    self.stats["errors"].append(f"表 {source_table.name} 迁移失败: {e}")

        self.stats["end_time"] = time.time()

        # 迁移完成后修复 PostgreSQL 特有问题
        if self.target_type == "postgresql" and self.target_engine:
            fix_postgresql_boolean_columns(self.target_engine)
            fix_postgresql_sequences(self.target_engine)

    def print_summary(self) -> None:
        """打印迁移统计摘要。"""
        duration = None
        if self.stats["start_time"] is not None and self.stats["end_time"] is not None:
            duration = self.stats["end_time"] - self.stats["start_time"]

        print("\n" + "=" * 60)
        print("迁移完成！")
        print(f"  迁移表数量: {self.stats['tables_migrated']}")
        print(f"  迁移行数量: {self.stats['rows_migrated']}")
        if duration is not None:
            print(f"  总耗时: {duration:.2f} 秒")
        if self.stats["errors"]:
            print("  ⚠️ 发生错误:")
            for err in self.stats["errors"]:
                print(f"    - {err}")
        else:
            print("  没有发生错误 🎉")
        print("=" * 60 + "\n")

    def run(self) -> dict:
        """运行迁移并打印总结。

        Returns:
            迁移统计字典
        """
        self.migrate()
        self.print_summary()
        return self.stats


# =============================================================================
# PostgreSQL 迁移后修复
# =============================================================================


def fix_postgresql_sequences(engine: Engine) -> None:
    """修复 PostgreSQL 序列值，避免迁移后插入新记录时出现主键冲突。

    Args:
        engine: PostgreSQL 数据库引擎
    """
    if engine.dialect.name != "postgresql":
        return

    logger.info("正在修复 PostgreSQL 序列...")

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                t.table_name,
                c.column_name,
                pg_get_serial_sequence(t.table_name, c.column_name) as sequence_name
            FROM information_schema.tables t
            JOIN information_schema.columns c
                ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE t.table_schema = 'public'
            AND t.table_type = 'BASE TABLE'
            AND c.column_default LIKE 'nextval%'
            ORDER BY t.table_name
        """))

        sequences = result.fetchall()
        logger.info("发现 %d 个带序列的表", len(sequences))

        fixed_count = 0
        for table_name, column_name, seq_name in sequences:
            if seq_name:
                try:
                    max_result = conn.execute(
                        text(f"SELECT COALESCE(MAX({column_name}), 0) FROM {table_name}")
                    )
                    max_val = max_result.scalar() or 0
                    next_val = int(max_val) + 1
                    conn.execute(text(f"SELECT setval('{seq_name}', {next_val}, false)"))
                    conn.commit()
                    logger.info(
                        "  ✅ %s.%s: 最大值=%d, 序列设为=%d",
                        table_name, column_name, max_val, next_val,
                    )
                    fixed_count += 1
                except Exception as e:
                    logger.warning("  ❌ %s.%s: 修复失败 - %s", table_name, column_name, e)

        logger.info("序列修复完成！共修复 %d 个序列", fixed_count)


def fix_postgresql_boolean_columns(engine: Engine) -> None:
    """修复从 SQLite 迁移后 PostgreSQL 中错误的布尔列类型（INTEGER -> BOOLEAN）。

    Args:
        engine: PostgreSQL 数据库引擎
    """
    if engine.dialect.name != "postgresql":
        return

    # 新框架中已知需要转换为 BOOLEAN 的列
    BOOLEAN_COLUMNS: dict[str, list[str]] = {
        "messages": ["is_mentioned", "is_emoji", "is_picid", "is_command",
                     "is_notify", "is_public_notice", "should_reply", "should_act"],
        "action_records": ["action_done", "action_build_into_prompt"],
    }

    logger.info("正在检查并修复 PostgreSQL 布尔列...")

    with engine.connect() as conn:
        fixed_count = 0
        for table_name, columns in BOOLEAN_COLUMNS.items():
            for col_name in columns:
                try:
                    result = conn.execute(text(f"""
                        SELECT data_type FROM information_schema.columns
                        WHERE table_name = '{table_name}' AND column_name = '{col_name}'
                    """))
                    row = result.fetchone()
                    if row and row[0] != "boolean":
                        conn.execute(text(f"""
                            ALTER TABLE {table_name}
                            ALTER COLUMN {col_name} TYPE BOOLEAN
                            USING CASE WHEN {col_name} = 0 THEN FALSE ELSE TRUE END
                        """))
                        conn.commit()
                        logger.info("  ✅ %s.%s: %s -> BOOLEAN", table_name, col_name, row[0])
                        fixed_count += 1
                except Exception as e:
                    logger.warning("  ⚠️ %s.%s: 检查/修复失败 - %s", table_name, col_name, e)

        if fixed_count > 0:
            logger.info("布尔列修复完成！共修复 %d 列", fixed_count)
        else:
            logger.info("所有布尔列类型正确，无需修复")


# =============================================================================
# 命令行参数解析
# =============================================================================


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Neo-MoFox 数据库迁移工具 - 在 SQLite、PostgreSQL 之间迁移数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  # 从 SQLite 迁移到 PostgreSQL
  python scripts/migrate_database.py \\
    --source sqlite \\
    --target postgresql \\
    --target-host localhost \\
    --target-port 5432 \\
    --target-database mofox \\
    --target-user postgres \\
    --target-password your_password

  # 从 PostgreSQL 迁移到 SQLite
  python scripts/migrate_database.py \\
    --source postgresql \\
    --source-host localhost \\
    --source-port 5432 \\
    --source-database mofox \\
    --source-user postgres \\
    --source-password your_password \\
    --target sqlite \\
    --target-path data/MoFox_backup.db

  # 使用交互式向导模式（推荐）
  python scripts/migrate_database.py
  python scripts/migrate_database.py --interactive
        """,
    )

    parser.add_argument("--source", type=str, choices=["sqlite", "postgresql"],
                        help="源数据库类型（不指定时进入交互模式）")
    parser.add_argument("--target", type=str, choices=["sqlite", "postgresql"],
                        help="目标数据库类型（不指定时进入交互模式）")
    parser.add_argument("--batch-size", type=int, default=1000, help="批量处理大小（默认: 1000）")
    parser.add_argument("--interactive", action="store_true", help="启用交互式向导模式")

    # 源数据库参数（可选，默认从 config/core.toml 读取）
    src = parser.add_argument_group("源数据库配置（可选，默认从 config/core.toml 读取）")
    src.add_argument("--source-path", type=str, help="SQLite 数据库路径")
    src.add_argument("--source-host", type=str, help="PostgreSQL 主机")
    src.add_argument("--source-port", type=int, help="PostgreSQL 端口")
    src.add_argument("--source-database", type=str, help="数据库名")
    src.add_argument("--source-user", type=str, help="用户名")
    src.add_argument("--source-password", type=str, help="密码")

    # 目标数据库参数
    tgt = parser.add_argument_group("目标数据库配置")
    tgt.add_argument("--target-path", type=str, help="SQLite 数据库路径")
    tgt.add_argument("--target-host", type=str, help="PostgreSQL 主机")
    tgt.add_argument("--target-port", type=int, help="PostgreSQL 端口")
    tgt.add_argument("--target-database", type=str, help="数据库名")
    tgt.add_argument("--target-user", type=str, help="用户名")
    tgt.add_argument("--target-password", type=str, help="密码")
    tgt.add_argument("--target-schema", type=str, default="public", help="PostgreSQL schema")

    parser.add_argument("--skip-tables", type=str, default="",
                        help="跳过迁移的表名，多个用逗号分隔（如: messages,logs）")
    parser.add_argument("--only-tables", type=str, default="",
                        help="只迁移指定表名，多个用逗号分隔。设置后忽略 --skip-tables")
    parser.add_argument("--no-create-tables", action="store_true",
                        help="不创建表结构，假设目标数据库中的表已存在（增量迁移时使用）")

    return parser.parse_args()


def build_config_from_args(args: argparse.Namespace, prefix: str, db_type: str) -> dict | None:
    """从命令行参数构建数据库配置字典。

    Args:
        args: 命令行参数
        prefix: 参数前缀 ("source" 或 "target")
        db_type: 数据库类型

    Returns:
        配置字典或 None
    """
    if db_type == "sqlite":
        path = getattr(args, f"{prefix}_path", None)
        if path:
            return {"path": path}
        return None

    elif db_type == "postgresql":
        host = getattr(args, f"{prefix}_host", None)
        if not host:
            return None
        return {
            "host": host,
            "port": getattr(args, f"{prefix}_port") or 5432,
            "database": getattr(args, f"{prefix}_database") or "mofox",
            "user": getattr(args, f"{prefix}_user") or "postgres",
            "password": getattr(args, f"{prefix}_password") or "",
            "schema": getattr(args, f"{prefix}_schema", "public"),
        }

    return None


# =============================================================================
# 交互式向导
# =============================================================================


def _ask_choice(prompt: str, options: list[str], default_index: int | None = None) -> str:
    """让用户从列表中选择一个选项。"""
    while True:
        print()
        print(prompt)
        for i, opt in enumerate(options, start=1):
            default_mark = "  (默认)" if default_index is not None and i - 1 == default_index else ""
            print(f"  {i}) {opt}{default_mark}")
        ans = input("请输入选项编号: ").strip()
        if not ans and default_index is not None:
            return options[default_index]
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        print("❌ 无效的选择，请重新输入。")


def _ask_int(prompt: str, default: int | None = None) -> int:
    """让用户输入一个正整数。"""
    while True:
        suffix = f" (默认 {default})" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError()
            return value
        except ValueError:
            print("❌ 请输入一个大于 0 的整数。")


def _ask_str(
    prompt: str,
    default: str | None = None,
    allow_empty: bool = False,
    is_password: bool = False,
) -> str:
    """让用户输入一个字符串，支持默认值和密码输入。"""
    while True:
        suffix = f" (默认: {default})" if default is not None else ""
        full_prompt = f"{prompt}{suffix}: "
        raw = getpass(full_prompt) if is_password else input(full_prompt)
        raw = raw.strip()
        if not raw:
            if default is not None:
                return default
            if allow_empty:
                return ""
            print("❌ 输入不能为空，请重新输入。")
            continue
        return raw


def interactive_setup() -> dict:
    """交互式向导，引导用户配置迁移参数。

    Returns:
        用于初始化 DatabaseMigrator 的参数字典
    """
    print("=" * 60)
    print("Neo-MoFox 数据库迁移向导")
    print("只需回答几个问题，我会帮你构造迁移配置。")
    print("=" * 60)

    db_types = ["sqlite", "postgresql"]
    source_type = _ask_choice("请选择【源数据库类型】:", db_types, default_index=0)

    # 目标数据库不能与源相同
    while True:
        default_idx = 1 if len(db_types) >= 2 else 0
        target_type = _ask_choice("请选择【目标数据库类型】:", db_types, default_index=default_idx)
        if target_type != source_type:
            break
        print("❌ 目标数据库不能和源数据库相同，请重新选择。")

    batch_size = _ask_int("请输入批量大小 batch-size", default=1000)

    # 源数据库配置
    print()
    print("源数据库配置：")
    print("  默认会从 config/core.toml 中读取对应配置。")
    use_default_source = input("是否使用配置文件中的【源数据库】配置? [Y/n]: ").strip().lower()
    if use_default_source in ("", "y", "yes"):
        source_config = None  # 由 DatabaseMigrator 自动从 core.toml 读取
    else:
        print("请手动输入源数据库连接信息：")
        if source_type == "sqlite":
            source_path = _ask_str("源 SQLite 文件路径", default="data/MoFox.db")
            source_config: dict | None = {"path": source_path}
        else:
            host = _ask_str("源数据库 host", default="localhost")
            port = _ask_int("源数据库 port", default=5432)
            database = _ask_str("源数据库名", default="mofox")
            user = _ask_str("源数据库用户名", default="postgres")
            password = _ask_str("源数据库密码（输入时不回显）", default="", is_password=True)
            source_config = {
                "host": host, "port": port, "database": database,
                "user": user, "password": password,
                "schema": _ask_str("源数据库 schema", default="public"),
            }

    # 目标数据库配置（必须显式输入）
    print()
    print("目标数据库配置：")
    if target_type == "sqlite":
        target_path = _ask_str(
            "目标 SQLite 文件路径（若不存在会自动创建）",
            default="data/MoFox.db",
        )
        target_config: dict = {"path": target_path}
    else:
        host = _ask_str("目标数据库 host", default="localhost")
        port = _ask_int("目标数据库 port", default=5432)
        database = _ask_str("目标数据库名", default="mofox")
        user = _ask_str("目标数据库用户名", default="postgres")
        password = _ask_str("目标数据库密码（输入时不回显）", default="", is_password=True)
        target_config = {
            "host": host, "port": port, "database": database,
            "user": user, "password": password,
            "schema": _ask_str("目标数据库 schema", default="public"),
        }

    print()
    print("=" * 60)
    print("迁移配置确认：")
    print(f"  源数据库类型: {source_type}")
    print(f"  目标数据库类型: {target_type}")
    print(f"  批量大小: {batch_size}")
    print("⚠️ 请确认目标数据库为空或可以被覆盖，并且已备份源数据库。")
    confirm = input("是否开始迁移？[Y/n]: ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("已取消迁移。")
        sys.exit(0)

    return {
        "source_type": source_type,
        "target_type": target_type,
        "batch_size": batch_size,
        "source_config": source_config,
        "target_config": target_config,
    }


# =============================================================================
# 入口
# =============================================================================


def main() -> None:
    """主函数。"""
    args = parse_args()

    # 无参数或显式 --interactive 时进入交互模式
    if args.interactive or len(sys.argv) == 1:
        params = interactive_setup()
        try:
            migrator = DatabaseMigrator(**params)
            stats = migrator.run()
            if stats["errors"]:
                sys.exit(1)
            return
        except KeyboardInterrupt:
            print("\n迁移被用户中断")
            sys.exit(130)
        except Exception as e:
            print(f"迁移失败: {e}")
            sys.exit(1)

    # 非交互模式
    if not args.source or not args.target:
        print("错误: 非交互模式下必须指定 --source 和 --target。")
        print("你也可以直接运行脚本或添加 --interactive 使用交互式向导。")
        sys.exit(2)

    source_config = build_config_from_args(args, "source", args.source)
    target_config = build_config_from_args(args, "target", args.target)

    if target_config is None:
        if args.target == "sqlite":
            if not args.target_path:
                print("错误: 目标数据库为 SQLite 时，必须指定 --target-path")
                sys.exit(1)
            target_config = {"path": args.target_path}
        else:
            if not args.target_host:
                print(f"错误: 目标数据库为 {args.target} 时，必须指定 --target-host")
                sys.exit(1)

    try:
        skip_tables: set[str] = set()
        if args.skip_tables:
            skip_tables = {t.strip() for t in args.skip_tables.split(",") if t.strip()}
            logger.info("将跳过以下表: %s", ", ".join(skip_tables))

        only_tables: set[str] = set()
        if args.only_tables:
            only_tables = {t.strip() for t in args.only_tables.split(",") if t.strip()}
            logger.info("将只迁移以下表: %s", ", ".join(only_tables))

        migrator = DatabaseMigrator(
            source_type=args.source,
            target_type=args.target,
            batch_size=args.batch_size,
            source_config=source_config,
            target_config=target_config,
            skip_tables=skip_tables,
            only_tables=only_tables,
            no_create_tables=args.no_create_tables,
        )

        stats = migrator.run()
        if stats["errors"]:
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n迁移被用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"迁移失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
