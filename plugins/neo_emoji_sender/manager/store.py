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
    """根据后缀猜测 MIME。"""
    return _MIME_BY_SUFFIX.get(suffix.lower(), "image/png")


def normalize_suffix(suffix: str) -> str:
    """规范化并校验后缀。"""
    value = suffix.lower()
    if not value.startswith("."):
        value = "." + value
    return value if value in ALLOWED_SUFFIXES else ".png"


def sha256_bytes(data: bytes) -> str:
    """计算 bytes 的 sha256 十六进制值。"""
    return hashlib.sha256(data).hexdigest()


class EmojiStore:
    """负责 memes/ 下的文件读写与原子落盘。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir)
        self.memes_dir = self.root / "memes"
        self.tmp_dir = self.root / "tmp"

    async def initialize(self) -> None:
        """创建目录。"""
        await asyncio.to_thread(self.memes_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.tmp_dir.mkdir, parents=True, exist_ok=True)

    def rel_path_for(self, emoji_id: str, suffix: str) -> str:
        """构造相对存储路径。"""
        return f"memes/{emoji_id}{normalize_suffix(suffix)}"

    def resolve(self, rel_path: str) -> Path:
        """把相对路径解析为绝对路径，并拒绝越界与符号链接。"""
        base = self.root.resolve()
        target = (self.root / rel_path).resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"路径越界: {rel_path}")
        if target.is_symlink():
            raise ValueError(f"拒绝符号链接: {rel_path}")
        return target

    async def save(self, data: bytes, suffix: str) -> tuple[str, str]:
        """保存文件，返回 (sha256, rel_path)。同内容重复保存直接复用。"""
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
        """线程内写临时文件并原子替换。"""
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)

    async def read(self, rel_path: str) -> bytes:
        """读取文件内容。"""
        target = self.resolve(rel_path)
        return await asyncio.to_thread(target.read_bytes)

    async def delete(self, rel_path: str) -> bool:
        """删除文件；不存在视为成功。"""
        target = self.resolve(rel_path)
        return await asyncio.to_thread(self._unlink, target)

    @staticmethod
    def _unlink(path: Path) -> bool:
        """线程内删除文件。"""
        path.unlink(missing_ok=True)
        return True

    async def exists(self, rel_path: str) -> bool:
        """判断文件是否存在。"""
        target = self.resolve(rel_path)
        return await asyncio.to_thread(target.is_file)

    async def list_rel_paths(self) -> set[str]:
        """列出 memes/ 下所有允许后缀的文件。"""
        return await asyncio.to_thread(self._list_rel_paths)

    def _list_rel_paths(self) -> set[str]:
        """线程内扫描目录。"""
        if not self.memes_dir.exists():
            return set()
        return {
            f"memes/{path.name}"
            for path in self.memes_dir.iterdir()
            if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
        }

    async def find_by_hash(self, emoji_id: str) -> list[str]:
        """按内容哈希查找所有可能后缀的相对路径。"""
        return await asyncio.to_thread(self._find_by_hash, emoji_id)

    def _find_by_hash(self, emoji_id: str) -> list[str]:
        """线程内按前缀匹配文件。"""
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
        """统计表情包文件数量。"""
        return len(await self.list_rel_paths())

    async def mtime(self, rel_path: str) -> float:
        """获取文件修改时间。"""
        target = self.resolve(rel_path)
        return await asyncio.to_thread(lambda: target.stat().st_mtime)
