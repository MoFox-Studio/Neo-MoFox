"""数据库结构强一致同步。

在应用启动阶段执行：
- 删除代码模型中未定义的列
- 添加数据库中缺失的列
- 校验并修正列类型与可空性

目标是让数据库结构与 `src.core.models.sql_alchemy` 中的 ORM 定义保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect, text
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.schema import MetaData, Table

from src.kernel.db.core.engine import get_configured_db_type, get_engine
from src.kernel.db.core.exceptions import DatabaseInitializationError
from src.kernel.logger import get_logger

logger = get_logger("schema_sync", display="Schema 同步")


@dataclass(slots=True)
class SchemaSyncStats:
    """数据库结构同步统计信息。"""

    tables_checked: int = 0
    columns_added: int = 0
    columns_removed: int = 0
    columns_type_altered: int = 0
    columns_nullability_altered: int = 0


async def enforce_database_schema_consistency(
    metadata: MetaData | None = None,
) -> SchemaSyncStats:
    """强制数据库结构与 ORM 定义保持一致。

    Args:
        metadata: 要对齐的模型元数据；为空时使用 core 默认模型元数据。

    Returns:
        SchemaSyncStats: 同步结果统计。

    Raises:
        DatabaseInitializationError: 结构不一致且无法自动修复时抛出。
    """
    if metadata is None:
        from src.core.models.sql_alchemy import Base

        metadata = Base.metadata

    active_metadata = metadata
    assert active_metadata is not None

    engine = await get_engine()
    db_type = (get_configured_db_type() or "").lower()
    stats = SchemaSyncStats()

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: active_metadata.create_all(sync_conn))

        for table in active_metadata.sorted_tables:
            await _sync_table(conn, table, db_type, stats)

    logger.info(
        "Schema 对齐完成: "
        f"tables={stats.tables_checked}, "
        f"add={stats.columns_added}, "
        f"drop={stats.columns_removed}, "
        f"type={stats.columns_type_altered}, "
        f"nullable={stats.columns_nullability_altered}"
    )
    return stats


async def _sync_table(
    conn: AsyncConnection,
    model_table: Table,
    db_type: str,
    stats: SchemaSyncStats,
) -> None:
    """同步单个表结构。"""
    db_columns = await _get_db_columns(conn, model_table.name, model_table.schema)
    if not db_columns:
        # create_all 已处理缺失表；这里只处理已存在表的列对齐
        return

    stats.tables_checked += 1

    db_column_map = {col["name"]: col for col in db_columns}
    model_column_map = {col.name: col for col in model_table.columns}
    dialect = conn.dialect

    table_ref = _qualified_table_name(model_table.name, model_table.schema, dialect)

    # 1. 删除多余列（数据库有，模型无）
    for col_name in sorted(set(db_column_map) - set(model_column_map)):
        quoted_col = _quote_identifier(dialect, col_name)
        if "postgresql" in db_type:
            await conn.execute(
                text(f"ALTER TABLE {table_ref} DROP COLUMN {quoted_col} CASCADE")
            )
        else:
            # SQLite: 先删除引用该列的索引，避免 DROP COLUMN 时报错
            try:
                indexes = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_indexes(model_table.name)
                )
                for idx in indexes:
                    if col_name in (idx.get("column_names") or []):
                        idx_name = idx["name"]
                        await conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
                        logger.info(
                            f"已删除引用列 {col_name} 的索引: {idx_name}"
                        )
            except Exception as idx_err:
                logger.warning(
                    f"查询/删除索引时出错({model_table.name}.{col_name}): {idx_err}"
                )

            try:
                await conn.execute(
                    text(f"ALTER TABLE {table_ref} DROP COLUMN {quoted_col}")
                )
            except Exception as drop_err:
                # SQLite 旧版本可能完全不支持 DROP COLUMN，记录警告后跳过
                logger.warning(
                    f"SQLite 删除列 {model_table.name}.{col_name} 失败，跳过: {drop_err}"
                )
                continue
        stats.columns_removed += 1
        logger.warning(f"已移除未定义列: {model_table.name}.{col_name}")

    # 2. 添加缺失列（模型有，数据库无）
    for col_name in sorted(set(model_column_map) - set(db_column_map)):
        model_col = model_column_map[col_name]

        if model_col.primary_key:
            raise DatabaseInitializationError(
                f"表 {model_table.name} 缺失主键列 {col_name}，无法自动修复"
            )

        if not model_col.nullable and model_col.server_default is None:
            row_count = await _get_table_row_count(conn, model_table.name, model_table.schema)
            if row_count > 0:
                raise DatabaseInitializationError(
                    f"表 {model_table.name} 缺失非空列 {col_name} 且无默认值，"
                    "存在历史数据，无法安全自动修复"
                )

        col_def = _build_column_definition(model_col, dialect)
        await conn.execute(text(f"ALTER TABLE {table_ref} ADD COLUMN {col_def}"))
        stats.columns_added += 1
        logger.warning(f"已补齐缺失列: {model_table.name}.{col_name}")

    # 3. 校验类型和可空性（对齐后的最新结构）
    refreshed_columns = await _get_db_columns(conn, model_table.name, model_table.schema)
    refreshed_map = {col["name"]: col for col in refreshed_columns}

    for col_name, model_col in model_column_map.items():
        db_col = refreshed_map.get(col_name)
        if db_col is None:
            continue

        if model_col.primary_key:
            continue

        model_type = _normalize_type(str(model_col.type.compile(dialect=dialect)))
        db_col_type = _normalize_type(str(db_col["type"]))

        if model_type != db_col_type:
            await _alter_column_type(conn, model_table, col_name, model_col, db_type)
            stats.columns_type_altered += 1
            logger.warning(
                f"已修正列类型: {model_table.name}.{col_name} "
                f"({db_col_type} -> {model_type})"
            )

        db_nullable = bool(db_col.get("nullable", True))
        model_nullable = bool(model_col.nullable)
        if db_nullable != model_nullable:
            await _alter_column_nullability(conn, model_table, col_name, model_nullable, db_type)
            stats.columns_nullability_altered += 1
            logger.warning(
                f"已修正可空性: {model_table.name}.{col_name} "
                f"({db_nullable} -> {model_nullable})"
            )


async def _get_db_columns(
    conn: AsyncConnection,
    table_name: str,
    schema: str | None,
) -> list[dict]:
    """读取数据库中的列信息。"""

    def _fetch(sync_conn):
        from sqlalchemy import inspect

        inspector = inspect(sync_conn)
        return inspector.get_columns(table_name, schema=schema)

    return await conn.run_sync(_fetch)


def _build_column_definition(model_col, dialect: Dialect) -> str:
    """构造 `ALTER TABLE ADD COLUMN` 用列定义。"""
    col_name = _quote_identifier(dialect, model_col.name)
    type_sql = str(model_col.type.compile(dialect=dialect))

    parts = [col_name, type_sql]

    if model_col.server_default is not None:
        default_sql = _compile_server_default_sql(model_col, dialect)
        if default_sql:
            parts.append(f"DEFAULT {default_sql}")

    if not model_col.nullable:
        parts.append("NOT NULL")

    return " ".join(parts)


def _compile_server_default_sql(model_col, dialect: Dialect) -> str:
    """编译服务器默认值 SQL。"""
    default = model_col.server_default
    if default is None:
        return ""

    try:
        return str(
            default.arg.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
        )
    except Exception:
        return str(default.arg)


async def _alter_column_type(
    conn: AsyncConnection,
    table: Table,
    col_name: str,
    model_col,
    db_type: str,
) -> None:
    """修正列类型。"""
    table_ref = _qualified_table_name(table.name, table.schema, conn.dialect)
    quoted_col = _quote_identifier(conn.dialect, col_name)
    target_type_sql = str(model_col.type.compile(dialect=conn.dialect))

    if "postgresql" in db_type:
        await conn.execute(
            text(
                f"ALTER TABLE {table_ref} ALTER COLUMN {quoted_col} "
                f"TYPE {target_type_sql} USING {quoted_col}::{target_type_sql}"
            )
        )
        return

    if "sqlite" in db_type:
        # SQLite 的类型亲和性意味着大多数类型差异在运行时不会造成问题，
        # 不应因此阻止应用启动
        logger.warning(
            f"SQLite 不支持 ALTER TYPE，跳过类型修正: {table.name}.{col_name}"
        )
        return

    raise DatabaseInitializationError(
        f"暂不支持的数据库类型: {db_type}，无法修正列类型 {table.name}.{col_name}"
    )


async def _alter_column_nullability(
    conn: AsyncConnection,
    table: Table,
    col_name: str,
    target_nullable: bool,
    db_type: str,
) -> None:
    """修正列可空性。"""
    table_ref = _qualified_table_name(table.name, table.schema, conn.dialect)
    quoted_col = _quote_identifier(conn.dialect, col_name)

    if "postgresql" in db_type:
        sql = (
            f"ALTER TABLE {table_ref} ALTER COLUMN {quoted_col} DROP NOT NULL"
            if target_nullable
            else f"ALTER TABLE {table_ref} ALTER COLUMN {quoted_col} SET NOT NULL"
        )
        await conn.execute(text(sql))
        return

    if "sqlite" in db_type:
        # SQLite 不支持修改可空性，但这通常不影响运行时行为，跳过即可
        logger.warning(
            f"SQLite 不支持 ALTER NULLABILITY，跳过可空性修正: {table.name}.{col_name}"
        )
        return

    raise DatabaseInitializationError(
        f"暂不支持的数据库类型: {db_type}，无法修正可空性 {table.name}.{col_name}"
    )


async def _get_table_row_count(
    conn: AsyncConnection,
    table_name: str,
    schema: str | None,
) -> int:
    """获取表记录数。"""
    table_ref = _qualified_table_name(table_name, schema, conn.dialect)
    result = await conn.execute(text(f"SELECT COUNT(1) FROM {table_ref}"))
    value = result.scalar_one()
    return int(value)


def _qualified_table_name(
    table_name: str,
    schema: str | None,
    dialect: Dialect,
) -> str:
    """构造带 schema 的表引用。"""
    quoted_table = _quote_identifier(dialect, table_name)
    if schema:
        quoted_schema = _quote_identifier(dialect, schema)
        return f"{quoted_schema}.{quoted_table}"
    return quoted_table


def _quote_identifier(dialect: Dialect, name: str) -> str:
    """引用标识符，避免关键字/特殊字符问题。"""
    return dialect.identifier_preparer.quote(name)


def _normalize_type(raw: str) -> str:
    """归一化类型字符串用于比较。"""
    value = " ".join(raw.lower().replace('"', "").split())

    aliases = {
        "int": "integer",
        "int4": "integer",
        "double precision": "float",
        "real": "float",
        "float8": "float",
        "bool": "boolean",
        # 时间类型统一归一化为 "datetime"（SQLite 中 TIMESTAMP/DATE/DATETIME 都是 NUMERIC 亲和性，等价）
        "timestamp without time zone": "datetime",
        "timestamp with time zone": "datetime",
        "timestamp": "datetime",
        "date": "datetime",
        # SQLite 中 TEXT/VARCHAR/CLOB 完全等价（TEXT affinity）；
        # ORM 模型统一使用 Text()，所以将 VARCHAR 系列也归一化为 "text"
        "character varying": "text",
        "varchar": "text",
        "string": "text",
    }

    if value.startswith("character varying"):
        value = "text"
    elif value.startswith("varchar"):
        value = "text"

    return aliases.get(value, value)
