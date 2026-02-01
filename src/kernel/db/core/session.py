"""数据库会话管理

职责:
- 提供数据库会话工厂
- 提供数据库会话的上下文管理器
- 应用数据库特定的会话设置
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.kernel.logger import get_logger

from .engine import get_engine

logger = get_logger("database.session", display="DB 会话")

# 全局会话工厂
_session_factory: async_sessionmaker | None = None
_factory_lock: asyncio.Lock | None = None


async def get_session_factory() -> async_sessionmaker:
    """获取会话工厂（单例模式）

    Returns:
        async_sessionmaker: SQLAlchemy 异步会话工厂
    """
    global _session_factory, _factory_lock

    # 快速路径
    if _session_factory is not None:
        return _session_factory

    # 延迟创建锁
    if _factory_lock is None:
        _factory_lock = asyncio.Lock()

    async with _factory_lock:
        # 双重检查
        if _session_factory is not None:
            return _session_factory

        engine = await get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,  # 避免提交后访问属性时重新查询
        )

        logger.debug("会话工厂已创建")
        return _session_factory


async def _apply_session_settings(session: AsyncSession, db_type: str) -> None:
    """应用数据库特定的会话设置

    Args:
        session: 数据库会话
        db_type: 数据库类型（sqlite 或 postgresql）
    """
    try:
        if db_type == "sqlite":
            # SQLite 特定的 PRAGMA 设置
            await session.execute(text("PRAGMA busy_timeout = 60000"))
            await session.execute(text("PRAGMA foreign_keys = ON"))
        elif db_type == "postgresql":
            # PostgreSQL 特定设置（如果需要）
            # 可以设置 schema 搜索路径等
            pass
    except Exception:
        # 连接复用时设置可能已存在，忽略错误
        pass


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话上下文管理器

    这是数据库操作的主要入口点。

    事务管理:
    - 正常退出时自动提交事务
    - 发生异常时自动回滚事务
    - 安全地手动调用 commit/rollback

    使用示例:
        async with get_db_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

    Yields:
        AsyncSession: SQLAlchemy 异步会话对象
    """
    session_factory = await get_session_factory()

    async with session_factory() as session:
        try:
            # 从 engine 配置获取 db_type（避免运行时反射）
            from .engine import _engine_config
            
            db_type = None
            if _engine_config:
                db_type = _engine_config.db_type
                if not db_type:
                    # 从 URL 推断
                    url = _engine_config.url.lower()
                    if 'sqlite' in url:
                        db_type = 'sqlite'
                    elif 'postgresql' in url:
                        db_type = 'postgresql'

            if db_type:
                await _apply_session_settings(session, db_type)

            yield session

            # 正常退出时提交事务
            if session.is_active:
                await session.commit()
        except Exception:
            # 发生异常时回滚
            if session.is_active:
                await session.rollback()
            raise
        finally:
            await session.close()


async def reset_session_factory() -> None:
    """重置会话工厂（用于测试）"""
    global _session_factory
    _session_factory = None
