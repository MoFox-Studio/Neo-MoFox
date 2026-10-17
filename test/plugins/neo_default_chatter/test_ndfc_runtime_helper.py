"""``_runtime_helper`` 共享 ``NeoChatter`` 缓存的单元测试。

覆盖：

- ``get_runtime`` 按 ``stream_id`` 缓存：首次访问构造，二次访问复用。
- 不同 ``stream_id`` 拿到不同实例。
- ``drop_runtime`` 清理指定 ``stream_id`` 的缓存，下次访问重新构造。
- ``drop_runtime`` 对不存在的 ``stream_id`` 安全 no-op。
"""

from __future__ import annotations

from typing import Generator
from unittest.mock import MagicMock

import pytest

from plugins.neo_default_chatter.components.event_handlers.defaults import (
    _runtime_helper,
)


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> Generator[None, None, None]:
    """每个测试前后清空 ``_RUNTIME_CACHE``，避免跨测试污染。"""
    _runtime_helper._RUNTIME_CACHE.clear()
    yield
    _runtime_helper._RUNTIME_CACHE.clear()


def _patch_neo_chatter(monkeypatch: pytest.MonkeyPatch) -> list:
    """把 ``NeoChatter`` 类替换为返回唯一 ``MagicMock`` 的工厂。

    返回 ``constructed`` 列表，按构造顺序记录每个实例，便于断言构造次数。
    """
    constructed: list = []

    class _FakeNeoChatter:
        def __init__(self, stream_id: str, plugin: object) -> None:
            self.stream_id = stream_id
            self.plugin = plugin
            constructed.append(self)

    # ``get_runtime`` 内部用 ``from ...chatter import NeoChatter`` 延迟导入，
    # 因此 patch ``components.chatter.NeoChatter`` 即可。
    monkeypatch.setattr(
        "plugins.neo_default_chatter.components.chatter.NeoChatter",
        _FakeNeoChatter,
    )
    return constructed


# ---------------------------------------------------------------------------
# get_runtime
# ---------------------------------------------------------------------------


def test_get_runtime_constructs_on_first_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次访问应构造一个新的 ``NeoChatter`` 实例。"""
    constructed = _patch_neo_chatter(monkeypatch)
    plugin = MagicMock()

    runtime = _runtime_helper.get_runtime("s1", plugin)

    assert len(constructed) == 1
    assert runtime is constructed[0]
    assert runtime.stream_id == "s1"
    assert runtime.plugin is plugin


def test_get_runtime_caches_by_stream_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 ``stream_id`` 二次访问应复用缓存，不重复构造。"""
    constructed = _patch_neo_chatter(monkeypatch)
    plugin = MagicMock()

    first = _runtime_helper.get_runtime("s1", plugin)
    second = _runtime_helper.get_runtime("s1", plugin)

    assert first is second
    assert len(constructed) == 1


def test_get_runtime_separates_different_stream_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同 ``stream_id`` 应拿到不同实例。"""
    constructed = _patch_neo_chatter(monkeypatch)
    plugin = MagicMock()

    a = _runtime_helper.get_runtime("s_a", plugin)
    b = _runtime_helper.get_runtime("s_b", plugin)

    assert a is not b
    assert a.stream_id == "s_a"
    assert b.stream_id == "s_b"
    assert len(constructed) == 2


def test_get_runtime_ignores_plugin_change_for_cached_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已缓存的 ``stream_id`` 再次访问时，传入不同 plugin 仍返回首次缓存的实例。

    这是「按 ``stream_id`` 缓存」的预期行为——同一 stream 应只构造一次 ``NeoChatter``。
    """
    constructed = _patch_neo_chatter(monkeypatch)
    plugin_a = MagicMock(name="plugin_a")
    plugin_b = MagicMock(name="plugin_b")

    first = _runtime_helper.get_runtime("s1", plugin_a)
    second = _runtime_helper.get_runtime("s1", plugin_b)

    assert first is second
    assert first.plugin is plugin_a  # 首次的 plugin
    assert len(constructed) == 1


# ---------------------------------------------------------------------------
# drop_runtime
# ---------------------------------------------------------------------------


def test_drop_runtime_clears_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``drop_runtime`` 清理指定 ``stream_id`` 的缓存，下次访问重新构造。"""
    constructed = _patch_neo_chatter(monkeypatch)
    plugin = MagicMock()

    first = _runtime_helper.get_runtime("s1", plugin)
    assert "s1" in _runtime_helper._RUNTIME_CACHE

    _runtime_helper.drop_runtime("s1")
    assert "s1" not in _runtime_helper._RUNTIME_CACHE

    second = _runtime_helper.get_runtime("s1", plugin)
    assert first is not second
    assert len(constructed) == 2


def test_drop_runtime_no_op_on_missing_stream_id() -> None:
    """``drop_runtime`` 对未缓存的 ``stream_id`` 应安全 no-op，不抛错。"""
    # 不应抛 KeyError
    _runtime_helper.drop_runtime("never_cached_stream_id")
    assert "never_cached_stream_id" not in _runtime_helper._RUNTIME_CACHE


def test_drop_runtime_does_not_affect_other_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``drop_runtime`` 只清理目标 ``stream_id``，不影响其他已缓存的实例。"""
    _patch_neo_chatter(monkeypatch)
    plugin = MagicMock()

    _runtime_helper.get_runtime("s_a", plugin)
    b = _runtime_helper.get_runtime("s_b", plugin)

    _runtime_helper.drop_runtime("s_a")

    assert "s_a" not in _runtime_helper._RUNTIME_CACHE
    assert "s_b" in _runtime_helper._RUNTIME_CACHE
    # b 仍可继续访问，复用缓存
    assert _runtime_helper.get_runtime("s_b", plugin) is b


# ---------------------------------------------------------------------------
# 并发场景（语义验证，不真跑 asyncio）
# ---------------------------------------------------------------------------


def test_runtime_cache_is_module_level_singleton() -> None:
    """``_RUNTIME_CACHE`` 应是模块级 dict，所有 import 都共享同一份。"""
    from plugins.neo_default_chatter.components.event_handlers.defaults import (
        _runtime_helper as another_ref,
    )

    assert _runtime_helper._RUNTIME_CACHE is another_ref._RUNTIME_CACHE
