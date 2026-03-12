"""数据库核心层 (core)

包含引擎、会话与异常定义。
"""

from src.kernel.db.core.engine import (
    close_engine,
    configure_engine,
    get_engine,
    get_engine_info,
    init_database_from_config,
    reset_engine_state,
)
from src.kernel.db.core.exceptions import (
    DatabaseConnectionError,
    DatabaseError,
    DatabaseInitializationError,
    DatabaseQueryError,
    DatabaseTransactionError,
)
from src.kernel.db.core.session import (
    get_db_session,
    get_session_factory,
    reset_session_factory,
)

# Base 并非 kernel/db 的硬性能力边界；此处仅作为占位导出，避免破坏现有对外 API。
from typing import Any

Base = Any

__all__ = [
    # 引擎
    "configure_engine",
    "init_database_from_config",
    "get_engine",
    "close_engine",
    "get_engine_info",
    "reset_engine_state",
    # 会话
    "get_session_factory",
    "get_db_session",
    "reset_session_factory",
    # 异常
    "DatabaseError",
    "DatabaseInitializationError",
    "DatabaseConnectionError",
    "DatabaseQueryError",
    "DatabaseTransactionError",
    # 模型基类
    "Base",
]
