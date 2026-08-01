"""接口层：提供 search/read/create/update/delete API 供外部插件调用。

本层将插件以数据库（知识库）的形式暴露，允许外部插件定义
何时存入数据、何时检索数据、何时删除更新数据。本插件仅提供
高级检索和高级存储服务，而不是一个完整的记忆系统。
"""

from .memory_store import BookuMemoryStoreService

__all__ = ["BookuMemoryStoreService"]
