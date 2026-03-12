"""storage 模块核心实现

提供简单的 JSON 本地持久化存储服务。
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiofiles import open as aio_open
from aiofiles.os import makedirs


class JSONStore:
    """JSON 存储服务

    提供异步的 JSON 文件读写功能，用于简单的本地数据持久化。

    示例:
        >>> await json_store.save("my_data", {"key": "value"})
        >>> data = await json_store.load("my_data")
        >>> print(data)  # {"key": "value"}
    """

    def __init__(self, storage_dir: str | Path = "data/json_storage") -> None:
        """初始化 JSON 存储服务

        Args:
            storage_dir: 存储目录路径，默认为 data/json_storage
        """
        self._storage_dir = Path(storage_dir)
        self._lock = asyncio.Lock()

    async def _ensure_dir(self) -> None:
        """确保存储目录存在"""
        if not self._storage_dir.exists():
            await makedirs(self._storage_dir, exist_ok=True)

    def _get_file_path(self, name: str) -> Path:
        """获取存储文件路径

        Args:
            name: 数据名称

        Returns:
            Path: 文件路径
        """
        # 安全检查：防止路径遍历攻击
        if "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"Invalid storage name: {name}")

        return self._storage_dir / f"{name}.json"

    async def save(self, name: str, data: dict[str, Any]) -> None:
        """保存数据到 JSON 文件

        Args:
            name: 数据名称（将作为文件名，不含 .json 后缀）
            data: 要保存的数据字典

        Raises:
            ValueError: 如果名称包含非法字符
            IOError: 如果文件写入失败
        """
        async with self._lock:
            await self._ensure_dir()

            file_path = self._get_file_path(name)

            # 使用 aiofiles 异步写入
            async with aio_open(file_path, mode="w", encoding="utf-8") as f:
                import json

                await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    async def load(self, name: str) -> dict[str, Any] | None:
        """从 JSON 文件加载数据

        Args:
            name: 数据名称（不含 .json 后缀）

        Returns:
            dict[str, Any] | None: 加载的数据字典，如果文件不存在则返回 None

        Raises:
            ValueError: 如果名称包含非法字符
            json.JSONDecodeError: 如果 JSON 格式错误
        """
        async with self._lock:
            file_path = self._get_file_path(name)

            # 文件不存在时返回 None
            if not file_path.exists():
                return None

            # 使用 aiofiles 异步读取
            async with aio_open(file_path, mode="r", encoding="utf-8") as f:
                content = await f.read()
                import json

                return json.loads(content)

    async def delete(self, name: str) -> bool:
        """删除指定名称的数据文件

        Args:
            name: 数据名称（不含 .json 后缀）

        Returns:
            bool: 是否成功删除（文件不存在时返回 False）

        Raises:
            ValueError: 如果名称包含非法字符
        """
        async with self._lock:
            file_path = self._get_file_path(name)

            if not file_path.exists():
                return False

            file_path.unlink()
            return True

    async def exists(self, name: str) -> bool:
        """检查指定名称的数据是否存在

        Args:
            name: 数据名称（不含 .json 后缀）

        Returns:
            bool: 数据是否存在

        Raises:
            ValueError: 如果名称包含非法字符
        """
        file_path = self._get_file_path(name)
        return file_path.exists()

    async def list_all(self) -> list[str]:
        """列出所有已存储的数据名称

        Returns:
            list[str]: 数据名称列表（不含 .json 后缀）
        """
        await self._ensure_dir()

        # 同步操作（文件列表查询不需要异步）
        json_files = list(self._storage_dir.glob("*.json"))
        return [f.stem for f in json_files]

    def get_storage_dir(self) -> Path:
        """获取存储目录路径

        Returns:
            Path: 存储目录的绝对路径
        """
        return self._storage_dir.resolve()


@lru_cache
def _get_json_store() -> JSONStore:
    """获取全局 JSONStore 单例

    使用 lru_cache 实现单例模式，确保整个应用共享同一个实例。

    Returns:
        JSONStore: 全局 JSONStore 实例
    """
    return JSONStore()


# 导出全局单例
json_store = _get_json_store()
"""全局 JSONStore 实例

使用此实例进行数据持久化操作。

示例:
    >>> from src.kernel.storage import json_store
    >>> await json_store.save("my_data", {"key": "value"})
    >>> data = await json_store.load("my_data")
"""

__all__ = [
    "JSONStore",
    "json_store",
]
