"""Booku Memory Store 插件包。

此包实现一个纯记忆数据库子插件，提供：
- 数据库层：记忆数据结构和 SQLite CRUD
- 算法层：RAG 检索算法（EPA 向量重塑、SVD 子空间、结果去重）
- 接口层：对外暴露 search/read/create/update/delete 五个 API

不包含 tool、agent、memory flashback、system reminder 等高级机制，
由外部插件自行决定何时存储、检索、更新、删除数据。
"""
