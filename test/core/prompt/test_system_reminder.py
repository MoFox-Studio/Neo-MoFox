"""System reminder store tests."""

from __future__ import annotations

import pytest

from src.core.prompt.system_reminder import (
    SystemReminderBucket,
    SystemReminderStore,
    get_system_reminder_store,
    reset_system_reminder_store,
)


def test_store_set_requires_bucket() -> None:
    """bucket 为空应抛出 ValueError。"""
    store = SystemReminderStore()
    with pytest.raises(ValueError, match="bucket 不能为空"):
        store.set("", name="n", content="c")


def test_store_set_requires_name_and_content() -> None:
    """name/content 为空应抛出 ValueError。"""
    store = SystemReminderStore()

    with pytest.raises(ValueError, match="name 不能为空"):
        store.set(SystemReminderBucket.ACTOR, name="", content="c")

    with pytest.raises(ValueError, match="content 不能为空"):
        store.set(SystemReminderBucket.ACTOR, name="n", content="")


def test_store_get_empty_bucket_returns_empty_string() -> None:
    """无内容时应返回空字符串。"""
    store = SystemReminderStore()
    assert store.get(SystemReminderBucket.ACTOR) == ""


def test_store_get_all_in_bucket() -> None:
    """不传 names 时应返回 bucket 下所有提醒。"""
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    store.set("actor", name="b", content="B")

    text = store.get("actor")
    assert "[a]\nA" in text
    assert "[b]\nB" in text


def test_store_get_filters_by_names_and_keeps_names_order() -> None:
    """传 names 时仅返回指定 name，且按 names 顺序拼接。"""
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    store.set("actor", name="b", content="B")

    text = store.get("actor", names=["b", "a"])
    assert text == "[b]\nB\n\n[a]\nA"


def test_store_get_rejects_empty_name_in_names() -> None:
    """names 中包含空值应抛出 ValueError。"""
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    with pytest.raises(ValueError, match="names 中包含空 name"):
        store.get("actor", names=[""])


def test_store_delete_removes_item_and_empty_bucket() -> None:
    """delete 应删除指定 reminder，并在 bucket 为空时清理该 bucket。"""
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    store.set("actor", name="b", content="B")

    assert store.delete("actor", "a") is True
    assert store.get("actor") == "[b]\nB"

    assert store.delete("actor", "b") is True
    assert store.get("actor") == ""


def test_store_delete_returns_false_when_missing() -> None:
    """删除不存在的 reminder 时应返回 False。"""
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")

    assert store.delete("actor", "missing") is False
    assert store.get("actor") == "[a]\nA"


def test_global_store_singleton_and_reset() -> None:
    """全局 store 应可 reset（测试用途）。"""
    reset_system_reminder_store()
    s1 = get_system_reminder_store()
    s2 = get_system_reminder_store()
    assert s1 is s2

    s1.set("actor", name="a", content="A")
    assert s1.get("actor")

    reset_system_reminder_store()
    s3 = get_system_reminder_store()
    assert s3 is not s1
    assert s3.get("actor") == ""
