"""数据库引擎管理

职责:
- 创建和管理 SQLAlchemy 异步引擎
- 支持 SQLite 和 PostgreSQL 数据库
- 应用数据库特定的性能优化
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.kernel.logger import get_logger

from .exceptions import DatabaseInitializationError

logger = get_logger("database.engine", display="DB 引擎")

# 全局引擎实例
_engine: AsyncEngine | None = None
_engine_lock: asyncio.Lock | None = None


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """由高层传入的引擎配置。

    注意：kernel/db 不负责读取用户配置，只消费调用方提供的参数。
    """

    url: str
    engine_kwargs: dict
    db_type: str | None = None
    apply_optimizations: bool = True


_engine_config: EngineConfig | None = None


def configure_engine(
    url: str,
    *,
    engine_kwargs: dict | None = None,
    db_type: str | None = None,
    apply_optimizations: bool = True,
) -> None:
    """配置数据库引擎的初始化参数（由高层调用方调用）。

    典型用法：应用启动时根据用户配置/环境变量解析出 URL 与参数，然后调用本函数。

    Args:
        url: SQLAlchemy 异步 URL，例如："sqlite+aiosqlite:///path/to.db"。
        engine_kwargs: 传给 create_async_engine 的 kwargs。
        db_type: 可选。"sqlite" / "postgresql"；为空时将从 url 推断。
        apply_optimizations: 是否在初始化后应用数据库特定优化。

    Raises:
        RuntimeError: 引擎已创建时禁止重新配置（请先 close_engine）。
    """
    global _engine_config

    if _engine is not None:
        raise RuntimeError(
            "数据库引擎已初始化，无法重新配置；请先调用 close_engine() 再 configure_engine()"
        )

    _engine_config = EngineConfig(
        url=url,
        engine_kwargs=engine_kwargs or {},
        db_type=db_type,
        apply_optimizations=apply_optimizations,
    )


async def reset_engine_state() -> None:
    """重置引擎状态（用于测试）。

    - dispose 当前引擎
    - 清理引擎实例与锁
    - 清理已配置的 EngineConfig
    """
    global _engine, _engine_lock, _engine_config

    await close_engine()
    _engine_lock = None
    _engine_config = None


def _infer_db_type_from_url(url: str) -> str | None:
    """从 URL 推断数据库类型
    
    Args:
        url: SQLAlchemy 数据库 URL
        
    Returns:
        str | None: 数据库类型或 None
    """
    scheme = url.split(":", 1)[0]
    backend = scheme.split("+", 1)[0].lower()
    if backend in {"sqlite", "postgresql"}:
        return backend
    return backend or None


def get_configured_db_type() -> str | None:
    """获取已配置的数据库类型
    
    Returns:
        str | None: 数据库类型 (sqlite/postgresql) 或 None
    """
    if _engine_config is None:
        return None
    
    db_type = _engine_config.db_type
    if db_type:
        return db_type
    
    return _infer_db_type_from_url(_engine_config.url)


async def get_engine() -> AsyncEngine:
    """获取全局数据库引擎（单例模式）

    Returns:
        AsyncEngine: SQLAlchemy 异步引擎

    Raises:
        DatabaseInitializationError: 如果引擎初始化失败
    """
    global _engine, _engine_lock

    # 快速路径：引擎已初始化
    if _engine is not None:
        return _engine

    # 延迟创建锁
    if _engine_lock is None:
        _engine_lock = asyncio.Lock()

    async with _engine_lock:
        # 双重检查锁定模式
        if _engine is not None:
            return _engine

        try:
            if _engine_config is None:
                raise DatabaseInitializationError(
                    "数据库引擎尚未配置；请在高层启动流程中先调用 configure_engine(url, ...)"
                )

            db_type = (
                _engine_config.db_type or _infer_db_type_from_url(_engine_config.url)
            )

            logger.info(
                f"正在初始化 {(db_type or 'UNKNOWN').upper()} 数据库引擎..."
            )

            # 创建异步引擎
            _engine = create_async_engine(
                _engine_config.url,
                **(_engine_config.engine_kwargs or {}),
            )

            # 应用数据库特定的优化
            if _engine_config.apply_optimizations:
                if db_type == "sqlite":
                    await _enable_sqlite_optimizations(_engine)
                elif db_type == "postgresql":
                    await _enable_postgresql_optimizations(_engine)

            logger.info(f"{(db_type or 'UNKNOWN').upper()} 数据库引擎初始化成功")
            return _engine

        except DatabaseInitializationError:
            raise
        except Exception as e:
            logger.error(f"数据库引擎初始化失败: {e}")
            raise DatabaseInitializationError(f"引擎初始化失败: {e}") from e


def _build_sqlite_config(
    db_path: str,
    echo: bool = False,
) -> tuple[str, dict]:
    """构建 SQLite 配置

    Args:
        db_path: SQLite 数据库文件路径
        echo: 是否打印 SQL 语句（用于调试）

    Returns:
        (url, engine_kwargs) 元组
    """
    # 确保数据库目录存在
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # SQLAlchemy SQLite URL expects forward slashes even on Windows.
    url = f"sqlite+aiosqlite:///{db_file.absolute().as_posix()}"

    engine_kwargs = {
        "echo": echo,
        "future": True,
        "connect_args": {
            "check_same_thread": False,
            "timeout": 60,
        },
    }

    logger.debug(f"SQLite 配置: {db_file.absolute()}")
    return url, engine_kwargs


def _build_postgresql_config(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    schema: str = "public",
    echo: bool = False,
    pool_size: int = 10,
    connection_timeout: int = 30,
    ssl_mode: str = "prefer",
    ssl_ca: str = "",
    ssl_cert: str = "",
    ssl_key: str = "",
) -> tuple[str, dict]:
    """构建 PostgreSQL 配置

    Args:
        host: 数据库主机
        port: 数据库端口
        user: 数据库用户
        password: 数据库密码
        database: 数据库名称
        schema: 模式名（schema），默认为 public
        echo: 是否打印 SQL 语句（用于调试）
        pool_size: 连接池大小
        connection_timeout: 连接超时时间（秒）
        ssl_mode: SSL 模式 (disable/allow/prefer/require/verify-ca/verify-full)
        ssl_ca: SSL CA 证书路径
        ssl_cert: SSL 客户端证书路径
        ssl_key: SSL 客户端密钥路径

    Returns:
        (url, engine_kwargs) 元组
    """
    # 优化 localhost 到 127.0.0.1 以避免 Windows 下 DNS 解析延迟引发的 TimeoutError
    if host.lower() == "localhost":
        logger.debug("将 localhost 自动重定向到 127.0.0.1 以优化解析速度")
        host = "127.0.0.1"

    encoded_user = quote_plus(user)
    encoded_password = quote_plus(password)

    # 构建带有 SSL 参数的 URL
    url = (
        f"postgresql+asyncpg://{encoded_user}:{encoded_password}"
        f"@{host}:{port}/{database}"
    )

    # 构建基础连接参数 (asyncpg 特定)
    connect_args: dict[str, Any] = {
        "timeout": connection_timeout,  # 建立连接超时
        "command_timeout": max(60, connection_timeout * 2),  # 语句执行超时
    }

    # 根据 ssl_mode 设置 SSL 参数
    if ssl_mode == "disable":
        # 禁用 SSL
        connect_args["ssl"] = False
    elif ssl_mode == "allow":
        # 尝试 SSL，失败则降级到非 SSL
        connect_args["ssl"] = "allow"
    elif ssl_mode == "prefer":
        # 优先 SSL（asyncpg 默认行为）
        # 不设置 ssl 参数，让 asyncpg 自动协商
        pass
    elif ssl_mode == "require":
        # 要求 SSL，但不验证证书
        connect_args["ssl"] = True
    elif ssl_mode == "verify-ca":
        # 验证 CA 证书
        connect_args["ssl"] = True
        if ssl_ca:
            connect_args["sslrootcert"] = ssl_ca
    elif ssl_mode == "verify-full":
        # 完全验证
        connect_args["ssl"] = True
        if ssl_ca:
            connect_args["sslrootcert"] = ssl_ca
        if ssl_cert:
            connect_args["sslcert"] = ssl_cert
        if ssl_key:
            connect_args["sslkey"] = ssl_key
    
    engine_kwargs = {
        "echo": echo,
        "future": True,
        "pool_size": pool_size,
        "max_overflow": pool_size * 2,  # 溢出大小为池大小的2倍
        "pool_timeout": connection_timeout,
        "pool_recycle": 1800,  # 缩短回收时间以避免无效连接 (30分钟)
        "pool_pre_ping": True,  # 连接前 ping 检查
        "connect_args": connect_args,
    }

    logger.debug(
        f"PostgreSQL 配置: {user}@{host}:{port}/{database} "
        f"(schema: {schema}, pool: {pool_size}, ssl: {ssl_mode}, timeout: {connection_timeout})"
    )
    return url, engine_kwargs


async def init_database_from_config(
    database_type: str,
    *,
    # SQLite 配置
    sqlite_path: str = "data/MaiBot.db",
    # PostgreSQL 配置
    postgresql_host: str = "localhost",
    postgresql_port: int = 5432,
    postgresql_database: str = "maibot",
    postgresql_user: str = "postgres",
    postgresql_password: str = "",
    postgresql_schema: str = "public",
    # PostgreSQL SSL 配置
    postgresql_ssl_mode: str = "prefer",
    postgresql_ssl_ca: str = "",
    postgresql_ssl_cert: str = "",
    postgresql_ssl_key: str = "",
    # 连接池配置
    connection_pool_size: int = 10,
    connection_timeout: int = 30,
    # 通用配置
    echo: bool = False,
) -> AsyncEngine:
    """从配置对象初始化数据库引擎

    这是应用层使用的便捷函数，直接从 CoreConfig 的 database 配置节初始化数据库。

    Args:
        database_type: 数据库类型 ("sqlite" 或 "postgresql")
        sqlite_path: SQLite 数据库文件路径
        postgresql_host: PostgreSQL 服务器地址
        postgresql_port: PostgreSQL 服务器端口
        postgresql_database: PostgreSQL 数据库名
        postgresql_user: PostgreSQL 用户名
        postgresql_password: PostgreSQL 密码
        postgresql_schema: PostgreSQL 模式名（schema）
        postgresql_ssl_mode: SSL 模式
        postgresql_ssl_ca: SSL CA 证书路径
        postgresql_ssl_cert: SSL 客户端证书路径
        postgresql_ssl_key: SSL 客户端密钥路径
        connection_pool_size: 连接池大小
        connection_timeout: 连接超时时间（秒）
        echo: 是否打印 SQL 语句（用于调试）

    Returns:
        AsyncEngine: 已初始化的数据库引擎

    Raises:
        ValueError: 如果数据库类型不支持
        RuntimeError: 如果引擎已初始化

    Examples:
        从 CoreConfig 初始化：
        ```python
        from src.core.config import get_core_config

        config = get_core_config()
        db_cfg = config.database

        await init_database_from_config(
            database_type=db_cfg.database_type,
            sqlite_path=db_cfg.sqlite_path,
            postgresql_host=db_cfg.postgresql_host,
            postgresql_port=db_cfg.postgresql_port,
            postgresql_database=db_cfg.postgresql_database,
            postgresql_user=db_cfg.postgresql_user,
            postgresql_password=db_cfg.postgresql_password,
            postgresql_schema=db_cfg.postgresql_schema,
            postgresql_ssl_mode=db_cfg.postgresql_ssl_mode,
            postgresql_ssl_ca=db_cfg.postgresql_ssl_ca,
            postgresql_ssl_cert=db_cfg.postgresql_ssl_cert,
            postgresql_ssl_key=db_cfg.postgresql_ssl_key,
            connection_pool_size=db_cfg.connection_pool_size,
            connection_timeout=db_cfg.connection_timeout,
            echo=db_cfg.echo,
        )
        ```
    """
    if database_type == "sqlite":
        url, engine_kwargs = _build_sqlite_config(
            db_path=sqlite_path,
            echo=echo,
        )
        configure_engine(url, engine_kwargs=engine_kwargs, db_type="sqlite")
    elif database_type == "postgresql":
        url, engine_kwargs = _build_postgresql_config(
            host=postgresql_host,
            port=postgresql_port,
            user=postgresql_user,
            password=postgresql_password,
            database=postgresql_database,
            schema=postgresql_schema,
            echo=echo,
            pool_size=connection_pool_size,
            connection_timeout=connection_timeout,
            ssl_mode=postgresql_ssl_mode,
            ssl_ca=postgresql_ssl_ca,
            ssl_cert=postgresql_ssl_cert,
            ssl_key=postgresql_ssl_key,
        )
        configure_engine(url, engine_kwargs=engine_kwargs, db_type="postgresql")
    else:
        raise ValueError(
            f"不支持的数据库类型: {database_type}. "
            f"仅支持 'sqlite' 或 'postgresql'"
        )

    # 立即初始化引擎（不延迟到第一次使用）
    return await get_engine()


async def close_engine() -> None:
    """关闭数据库引擎

    释放所有连接池资源
    """
    global _engine

    if _engine is not None:
        logger.info("正在关闭数据库引擎...")
        await _engine.dispose()
        _engine = None
        logger.info("数据库引擎已关闭")


async def _enable_sqlite_optimizations(engine: AsyncEngine) -> None:
    """启用 SQLite 性能优化

    优化项:
    - WAL 模式: 提升并发性能
    - NORMAL 同步: 平衡性能与安全
    - 外键约束
    - busy_timeout: 避免锁错误

    Args:
        engine: SQLAlchemy 异步引擎
    """
    try:
        async with engine.begin() as conn:
            # 启用 WAL 模式
            await conn.execute(text("PRAGMA journal_mode = WAL"))
            # 设置适中的同步级别
            await conn.execute(text("PRAGMA synchronous = NORMAL"))
            # 启用外键约束
            await conn.execute(text("PRAGMA foreign_keys = ON"))
            # 设置 busy_timeout 避免锁错误
            await conn.execute(text("PRAGMA busy_timeout = 10000"))
            # 设置缓存大小（10MB）
            await conn.execute(text("PRAGMA cache_size = -10000"))
            # 使用内存进行临时存储
            await conn.execute(text("PRAGMA temp_store = MEMORY"))

    except Exception as e:
        logger.warning(f"SQLite 优化失败: {e}，使用默认配置")


async def _enable_postgresql_optimizations(engine: AsyncEngine) -> None:
    """启用 PostgreSQL 会话级性能优化

    优化项:
    - work_mem: 排序/哈希操作的内存
    - statement_timeout: 语句超时
    - synchronous_commit: 提交同步级别
    - jit: 即时编译
    - idle_in_transaction_session_timeout: 事务空闲超时
    - lock_timeout: 锁等待超时

    Args:
        engine: SQLAlchemy 异步引擎
    """
    try:
        async with engine.begin() as conn:
            # 排序/哈希内存（每次操作）
            await conn.execute(text("SET work_mem = '64MB'"))
            # 语句超时（1分钟）
            await conn.execute(text("SET statement_timeout = '60000'"))
            # 提交同步级别
            await conn.execute(text("SET synchronous_commit = 'local'"))
            # 对短查询禁用 JIT
            await conn.execute(text("SET jit = 'off'"))
            # 事务空闲超时
            await conn.execute(
                text("SET idle_in_transaction_session_timeout = '60000'")
            )
            # 锁超时
            await conn.execute(text("SET lock_timeout = '5000'"))

    except Exception as e:
        logger.warning(f"PostgreSQL 优化失败: {e}，使用默认配置")


async def get_engine_info() -> dict:
    """获取引擎信息（用于监控和调试）

    Returns:
        dict: 引擎信息字典
    """
    try:
        engine = await get_engine()

        info = {
            "name": engine.name,
            "driver": engine.driver,
            "url": str(engine.url).replace(str(engine.url.password or ""), "***"),
            "pool_size": getattr(engine.pool, "size", lambda: None)(),
            "pool_checked_out": getattr(engine.pool, "checked_out", lambda: 0)(),
            "pool_overflow": getattr(engine.pool, "overflow", lambda: 0)(),
        }

        return info

    except Exception as e:
        logger.error(f"获取引擎信息失败: {e}")
        return {}
