"""文件存储测试（``EmojiStore``）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.neo_emoji_sender.manager.store import (
    EmojiStore,
    guess_mime,
    normalize_suffix,
    sha256_bytes,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
OTHER_BYTES = b"\x89PNG\r\n\x1a\n" + b"another-body"


async def test_initialize_creates_directories(tmp_path: Path) -> None:
    """初始化应建好 memes 与 tmp 目录。"""
    store = EmojiStore(tmp_path)

    await store.initialize()

    assert (tmp_path / "memes").is_dir()
    assert (tmp_path / "tmp").is_dir()


async def test_save_and_read_roundtrip(tmp_path: Path) -> None:
    """写入后应能原样读回，文件名使用内容哈希。"""
    store = EmojiStore(tmp_path)
    await store.initialize()

    emoji_id, rel_path = await store.save(PNG_BYTES, ".png")

    assert emoji_id == sha256_bytes(PNG_BYTES)
    assert rel_path == f"memes/{emoji_id}.png"
    assert await store.read(rel_path) == PNG_BYTES


async def test_save_same_content_reuses_file(tmp_path: Path) -> None:
    """同内容重复保存应复用已有文件，不产生第二份。"""
    store = EmojiStore(tmp_path)
    await store.initialize()

    first_id, first_path = await store.save(PNG_BYTES, ".png")
    second_id, second_path = await store.save(PNG_BYTES, ".png")

    assert first_id == second_id
    assert first_path == second_path
    assert await store.count() == 1


async def test_save_different_content_produces_distinct_files(tmp_path: Path) -> None:
    """不同内容应落到不同文件。"""
    store = EmojiStore(tmp_path)
    await store.initialize()

    id_a, _ = await store.save(PNG_BYTES, ".png")
    id_b, _ = await store.save(OTHER_BYTES, ".png")

    assert id_a != id_b
    assert await store.count() == 2


async def test_save_does_not_leave_temp_files(tmp_path: Path) -> None:
    """原子写入完成后不应残留临时文件。"""
    store = EmojiStore(tmp_path)
    await store.initialize()

    await store.save(PNG_BYTES, ".png")

    assert list((tmp_path / "tmp").iterdir()) == []


async def test_resolve_rejects_path_escape(tmp_path: Path) -> None:
    """越界路径必须被拒绝，防止读写数据目录之外的文件。"""
    store = EmojiStore(tmp_path)

    with pytest.raises(ValueError, match="路径越界"):
        store.resolve("../outside.png")


async def test_resolve_rejects_symlink(tmp_path: Path) -> None:
    """符号链接一律拒绝。"""
    store = EmojiStore(tmp_path)
    (tmp_path / "memes").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "real.png"
    target.write_bytes(PNG_BYTES)
    link = tmp_path / "memes" / "link.png"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="符号链接"):
        store.resolve("memes/link.png")


async def test_delete_is_idempotent(tmp_path: Path) -> None:
    """删除不存在的文件也返回 True。"""
    store = EmojiStore(tmp_path)
    await store.initialize()

    assert await store.delete("memes/missing.png") is True


async def test_delete_removes_file(tmp_path: Path) -> None:
    """删除后 exists 应为 False。"""
    store = EmojiStore(tmp_path)
    await store.initialize()
    _, rel_path = await store.save(PNG_BYTES, ".png")

    assert await store.delete(rel_path) is True
    assert await store.exists(rel_path) is False


async def test_list_rel_paths_only_returns_supported_suffixes(tmp_path: Path) -> None:
    """只列出受支持格式的文件。"""
    store = EmojiStore(tmp_path)
    await store.initialize()
    await store.save(PNG_BYTES, ".png")
    (tmp_path / "memes" / "notes.txt").write_text("x", encoding="utf-8")

    paths = await store.list_rel_paths()

    assert len(paths) == 1
    assert all(path.endswith(".png") for path in paths)


async def test_find_by_hash_matches_prefix(tmp_path: Path) -> None:
    """按内容哈希查找应忽略后缀差异。"""
    store = EmojiStore(tmp_path)
    await store.initialize()
    emoji_id, _ = await store.save(PNG_BYTES, ".png")

    found = await store.find_by_hash(emoji_id)

    assert found == [f"memes/{emoji_id}.png"]


async def test_find_by_hash_returns_empty_when_absent(tmp_path: Path) -> None:
    """未知哈希返回空列表。"""
    store = EmojiStore(tmp_path)
    await store.initialize()

    assert await store.find_by_hash("deadbeef") == []


async def test_mtime_reports_file_timestamp(tmp_path: Path) -> None:
    """mtime 应返回可为对账使用的浮点时间戳。"""
    store = EmojiStore(tmp_path)
    await store.initialize()
    _, rel_path = await store.save(PNG_BYTES, ".png")

    assert await store.mtime(rel_path) > 0


async def test_rel_path_for_normalizes_suffix(tmp_path: Path) -> None:
    """rel_path_for 应规范化后缀。"""
    store = EmojiStore(tmp_path)

    assert store.rel_path_for("abc", "PNG") == "memes/abc.png"
    assert store.rel_path_for("abc", ".JPG") == "memes/abc.jpg"


def test_guess_mime_known_and_unknown() -> None:
    """已知后缀映射到正确 MIME，未知后缀回退 PNG。"""
    assert guess_mime(".png") == "image/png"
    assert guess_mime(".JPG") == "image/jpeg"
    assert guess_mime(".webp") == "image/webp"
    assert guess_mime(".unknown") == "image/png"


def test_normalize_suffix_rejects_unsupported() -> None:
    """不在白名单内的后缀回退为 .png。"""
    assert normalize_suffix("gif") == ".gif"
    assert normalize_suffix(".bmp") == ".png"


def test_sha256_bytes_is_stable() -> None:
    """同一内容哈希稳定且长度正确。"""
    assert sha256_bytes(PNG_BYTES) == sha256_bytes(PNG_BYTES)
    assert len(sha256_bytes(PNG_BYTES)) == 64
