"""表情包文件存储。

所有路径均为相对数据根目录的 POSIX 路径，避免绝对路径写入数据库或向量库。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

ALLOWED_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

_MIME_BY_SUFFIX: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def guess_mime(suffix: str) -> str:
    """根据文件后缀猜测 MIME 类型。

    Args:
        suffix: 文件后缀（可带或不带前导点）。

    Returns:
        对应的 MIME 字符串；不认识的后缀按 PNG 处理。
    """
    return _MIME_BY_SUFFIX.get(suffix.lower(), "image/png")


def normalize_suffix(suffix: str) -> str:
    """规范化并校验文件后缀。

    Args:
        suffix: 原始后缀（可带或不带前导点）。

    Returns:
        带前导点的合法后缀；不在白名单内时回退为 ``.png``。
    """
    value = suffix.lower()
    if not value.startswith("."):
        value = "." + value
    return value if value in ALLOWED_SUFFIXES else ".png"


def sha256_bytes(data: bytes) -> str:
    """计算字节内容的 sha256 十六进制摘要。

    摘要同时用作表情包 ID 与去重依据。

    Args:
        data: 原始字节。

    Returns:
        64 位小写十六进制字符串。
    """
    return hashlib.sha256(data).hexdigest()


class EmojiStore:
    """负责 ``memes/`` 下的文件读写与原子落盘。"""

    def __init__(self, data_dir: str | Path) -> None:
        """初始化存储。

        Args:
            data_dir: 插件数据根目录。
        """
        self.root = Path(data_dir)
        self.memes_dir = self.root / "memes"
        self.tmp_dir = self.root / "tmp"

    async def initialize(self) -> None:
        """创建存储所需目录。"""
        await asyncio.to_thread(self.memes_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.tmp_dir.mkdir, parents=True, exist_ok=True)

    def rel_path_for(self, emoji_id: str, suffix: str) -> str:
        """按 ID 与后缀构造相对存储路径。

        Args:
            emoji_id: 表情包 ID（内容哈希）。
            suffix: 文件后缀。

        Returns:
            相对数据根目录的 POSIX 路径。
        """
        return f"memes/{emoji_id}{normalize_suffix(suffix)}"

    def resolve(self, rel_path: str) -> Path:
        """把相对路径解析为绝对路径。

        拒绝越界路径与符号链接，防止通过构造路径读写数据目录之外的文件。

        Args:
            rel_path: 相对数据根目录的 POSIX 路径。

        Returns:
            解析后的绝对路径。

        Raises:
            ValueError: 路径越界或指向符号链接时抛出。
        """
        base = self.root.resolve()
        raw = self.root / rel_path
        # 符号链接必须在 resolve() 之前判断：resolve 会展开到真实路径，
        # 展开后的路径不再是链接，检查会永远失效
        if raw.is_symlink():
            raise ValueError(f"拒绝符号链接: {rel_path}")
        target = raw.resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"路径越界: {rel_path}")
        return target

    async def save(self, data: bytes, suffix: str) -> tuple[str, str]:
        """保存文件并返回其标识。

        以内容哈希作为文件名，因此同内容重复保存会直接复用已有文件。

        Args:
            data: 文件字节内容。
            suffix: 文件后缀。

        Returns:
            (内容哈希, 相对路径)。
        """
        emoji_id = sha256_bytes(data)
        rel_path = self.rel_path_for(emoji_id, suffix)
        target = self.resolve(rel_path)
        if target.exists():
            return emoji_id, rel_path

        tmp_path = self.tmp_dir / f"{uuid.uuid4().hex}.part"
        await asyncio.to_thread(self._write_atomic, tmp_path, target, data)
        return emoji_id, rel_path

    @staticmethod
    def _write_atomic(tmp_path: Path, target: Path, data: bytes) -> None:
        """先写临时文件再原子替换，避免对账读到写了一半的文件。

        Args:
            tmp_path: 临时文件路径（位于 ``tmp/`` 下，不在扫描范围内）。
            target: 最终目标路径。
            data: 文件字节内容。
        """
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)

    async def read(self, rel_path: str) -> bytes:
        """读取文件内容。

        Args:
            rel_path: 相对路径。

        Returns:
            文件字节。

        Raises:
            FileNotFoundError: 文件不存在时抛出。
        """
        target = self.resolve(rel_path)
        return await asyncio.to_thread(target.read_bytes)

    async def delete(self, rel_path: str) -> bool:
        """删除文件。

        Args:
            rel_path: 相对路径。

        Returns:
            始终返回 True；文件本就不存在也视为成功。
        """
        target = self.resolve(rel_path)
        return await asyncio.to_thread(self._unlink, target)

    @staticmethod
    def _unlink(path: Path) -> bool:
        """在子线程中删除文件。

        Args:
            path: 绝对路径。

        Returns:
            始终返回 True。
        """
        path.unlink(missing_ok=True)
        return True

    async def exists(self, rel_path: str) -> bool:
        """判断文件是否存在。

        Args:
            rel_path: 相对路径。

        Returns:
            是常规文件时返回 True。
        """
        target = self.resolve(rel_path)
        return await asyncio.to_thread(target.is_file)

    async def list_rel_paths(self) -> set[str]:
        """列出 ``memes/`` 下所有受支持格式的相对路径。

        Returns:
            相对路径集合。
        """
        return await asyncio.to_thread(self._list_rel_paths)

    def _list_rel_paths(self) -> set[str]:
        """在子线程中扫描目录。

        Returns:
            相对路径集合；目录不存在时为空集合。
        """
        if not self.memes_dir.exists():
            return set()
        return {
            f"memes/{path.name}"
            for path in self.memes_dir.iterdir()
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
        }

    async def find_by_hash(self, emoji_id: str) -> list[str]:
        """按内容哈希查找所有可能后缀的相对路径。

        用于删除时 DB 记录已不存在、但仍需清理残留文件的情形。

        Args:
            emoji_id: 表情包 ID（内容哈希）。

        Returns:
            匹配到的相对路径列表。
        """
        return await asyncio.to_thread(self._find_by_hash, emoji_id)

    def _find_by_hash(self, emoji_id: str) -> list[str]:
        """在子线程中按文件名前缀匹配。

        Args:
            emoji_id: 表情包 ID（内容哈希）。

        Returns:
            匹配到的相对路径列表。
        """
        if not self.memes_dir.exists():
            return []
        return [
            f"memes/{path.name}"
            for path in self.memes_dir.iterdir()
            if path.is_file()
            and path.name.startswith(emoji_id)
            and path.suffix.lower() in ALLOWED_SUFFIXES
        ]

    async def count(self) -> int:
        """统计表情包文件数量。

        Returns:
            文件数量。
        """
        return len(await self.list_rel_paths())

    async def mtime(self, rel_path: str) -> float:
        """获取文件最后修改时间。

        Args:
            rel_path: 相对路径。

        Returns:
            Unix 时间戳（秒）。

        Raises:
            FileNotFoundError: 文件不存在时抛出。
        """
        target = self.resolve(rel_path)
        return await asyncio.to_thread(lambda: target.stat().st_mtime)
