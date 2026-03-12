"""数据库异常定义

提供统一的数据库异常系统。
"""


class DatabaseError(Exception):
    """数据库基础异常"""

    pass


class DatabaseInitializationError(DatabaseError):
    """数据库初始化异常"""

    pass


class DatabaseConnectionError(DatabaseError):
    """数据库连接异常"""

    pass


class DatabaseQueryError(DatabaseError):
    """数据库查询异常"""

    pass


class DatabaseTransactionError(DatabaseError):
    """数据库事务异常"""

    pass
