"""运行时单例生命周期测试。"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from plugins.neo_emoji_sender.config import NeoEmojiSenderConfig
from plugins.neo_emoji_sender.manager.runtime import EmojiRuntime


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """每个用例前后都清空单例，避免测试互相污染。"""
    EmojiRuntime._instance = None
    yield
    EmojiRuntime._instance = None


def _make_config(data_dir: Path) -> NeoEmojiSenderConfig:
    """构造指向临时目录的配置。"""
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(data_dir)
    return config


def test_get_raises_before_create() -> None:
    """未创建时 get 应抛错而不是返回 None。"""
    with pytest.raises(RuntimeError, match="运行时未初始化"):
        EmojiRuntime.get()


def test_peek_returns_none_before_create() -> None:
    """未创建时 peek 返回 None，不抛错。"""
    assert EmojiRuntime.peek() is None


def test_create_then_get_returns_same_instance(tmp_path: Path) -> None:
    """create 之后 get 必须拿到同一个对象。"""
    created = EmojiRuntime.create(_make_config(tmp_path))

    assert EmojiRuntime.get() is created
    assert EmojiRuntime.peek() is created


def test_create_uses_given_config(tmp_path: Path) -> None:
    """运行时持有的配置就是传入的那份。"""
    config = _make_config(tmp_path)

    runtime = EmojiRuntime.create(config)

    assert runtime.config is config
    assert runtime.data_dir == tmp_path


def test_create_builds_components(tmp_path: Path) -> None:
    """运行时应装配好仓储、存储、状态与向量索引。"""
    runtime = EmojiRuntime.create(_make_config(tmp_path))

    assert runtime.repo is not None
    assert runtime.store.root == tmp_path
    assert runtime.state.path == tmp_path / "state.json"
    assert runtime.vector.collection == "neo_emoji_sender"


def test_create_replaces_previous_instance(tmp_path: Path) -> None:
    """重复 create 应替换旧实例，模拟插件重载。"""
    first = EmojiRuntime.create(_make_config(tmp_path / "a"))
    second = EmojiRuntime.create(_make_config(tmp_path / "b"))

    assert first is not second
    assert EmojiRuntime.get() is second


async def test_close_unregisters_singleton(tmp_path: Path) -> None:
    """close 之后必须注销单例，否则重载会拿到已关闭数据库的对象。"""
    runtime = EmojiRuntime.create(_make_config(tmp_path))
    await runtime.initialize()

    await runtime.close()

    assert EmojiRuntime.peek() is None
    with pytest.raises(RuntimeError):
        EmojiRuntime.get()


async def test_close_does_not_unregister_newer_instance(tmp_path: Path) -> None:
    """关闭旧实例不能误删新实例。"""
    old = EmojiRuntime.create(_make_config(tmp_path / "old"))
    await old.initialize()
    newer = EmojiRuntime.create(_make_config(tmp_path / "new"))

    await old.close()

    assert EmojiRuntime.peek() is newer


def test_index_lock_is_stable_per_emoji(tmp_path: Path) -> None:
    """同一 emoji 反复取锁应得到同一个对象。"""
    runtime = EmojiRuntime.create(_make_config(tmp_path))

    assert runtime.index_lock("abc") is runtime.index_lock("abc")
    assert runtime.index_lock("abc") is not runtime.index_lock("def")


def test_record_usage_respects_window(tmp_path: Path) -> None:
    """使用历史受 dedup.window 限制。"""
    config = _make_config(tmp_path)
    config.dedup.enabled = True
    config.dedup.window = 2
    runtime = EmojiRuntime.create(config)

    for emoji_id in ("a", "b", "c"):
        runtime.record_usage("stream-1", emoji_id)

    assert runtime.recently_used("stream-1") == {"b", "c"}


def test_record_usage_disabled_keeps_history_empty(tmp_path: Path) -> None:
    """关闭去重时不记录历史。"""
    config = _make_config(tmp_path)
    config.dedup.enabled = False
    runtime = EmojiRuntime.create(config)

    runtime.record_usage("stream-1", "a")

    assert runtime.recently_used("stream-1") == set()


def test_record_usage_ignores_empty_arguments(tmp_path: Path) -> None:
    """空 stream_id 或 emoji_id 直接忽略。"""
    runtime = EmojiRuntime.create(_make_config(tmp_path))

    runtime.record_usage("", "a")
    runtime.record_usage("stream-1", "")

    assert runtime.recently_used("stream-1") == set()


def test_recently_used_unknown_stream_is_empty(tmp_path: Path) -> None:
    """未知聊天流返回空集合。"""
    runtime = EmojiRuntime.create(_make_config(tmp_path))

    assert runtime.recently_used("nobody") == set()
