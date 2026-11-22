"""neo_emoji_sender 工具类模块。

包含可复用的工具函数：

- ``vector_response.py``: 向量库 ``get`` 响应的归一化与失败判定
- ``legacy_migration.py``: 旧 emoji_sender 数据迁移（按需直接导入，
  避免与 manager 层形成循环依赖）
"""

from .vector_response import extract_ids

__all__ = ["extract_ids"]
