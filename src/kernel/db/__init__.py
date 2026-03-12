"""数据库抽象层 (kernel/db)

提供与业务逻辑无关的数据库技术能力：

核心层 (core/)：
- engine: 数据库引擎管理
- session: 会话管理
- exceptions: 异常定义

API 层 (api/)：
- crud: 基础 CRUD 操作
- query: 高级查询构建器
"""

# 核心
from src.kernel.db.core import (
    Base,
    close_engine,
    configure_engine,
    get_db_session,
    get_engine,
    get_engine_info,
    init_database_from_config,
    get_session_factory,
    reset_engine_state,
    reset_session_factory,
)

# API
from src.kernel.db.api import (
    AggregateQuery,
    CRUDBase,
    QueryBuilder,
)

# 异常
from src.kernel.db.core.exceptions import (
    DatabaseConnectionError,
    DatabaseError,
    DatabaseInitializationError,
    DatabaseQueryError,
    DatabaseTransactionError,
)

__all__ = [
    # 核心
    "configure_engine",
    "Base",
    "get_engine",
    "get_session_factory",
    "get_db_session",
    "close_engine",
    "get_engine_info",
    "reset_engine_state",
    "reset_session_factory",
    "init_database_from_config",
    # API
    "CRUDBase",
    "QueryBuilder",
    "AggregateQuery",
    # 异常
    "DatabaseError",
    "DatabaseInitializationError",
    "DatabaseConnectionError",
    "DatabaseQueryError",
    "DatabaseTransactionError",
]
