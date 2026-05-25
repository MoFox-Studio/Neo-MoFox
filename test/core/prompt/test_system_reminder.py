"""System reminder store tests."""

from __future__ import annotations

import pytest

from src.core.prompt.system_reminder import (
    SystemReminderBucket,
    SystemReminderConsumeType,
    SystemReminderInsertType,
    SystemReminderItem,
    SystemReminderStore,
    get_system_reminder_store,
    reset_system_reminder_store,
)


def test_store_set_requires_bucket() -> None:
    store = SystemReminderStore()
    with pytest.raises(ValueError, match="bucket"):
        store.set("", name="n", content="c")


def test_store_set_requires_name_and_content() -> None:
    store = SystemReminderStore()

    with pytest.raises(ValueError, match="name"):
        store.set(SystemReminderBucket.ACTOR, name="", content="c")

    with pytest.raises(ValueError, match="content"):
        store.set(SystemReminderBucket.ACTOR, name="n", content="")


def test_store_get_empty_bucket_returns_empty_string() -> None:
    store = SystemReminderStore()
    assert store.get(SystemReminderBucket.ACTOR) == ""


def test_store_set_uses_fixed_insert_type_by_default() -> None:
    store = SystemReminderStore()

    store.set("actor", name="goal", content="A")

    assert store.get_items("actor") == [
        SystemReminderItem(
            name="goal",
            content="A",
            insert_type=SystemReminderInsertType.FIXED,
            consume_type=SystemReminderConsumeType.FOREVER,
        )
    ]


def test_store_set_accepts_dynamic_insert_type() -> None:
    store = SystemReminderStore()

    store.set("actor", name="goal", content="A", insert_type="dynamic")

    assert store.get_items("actor")[0].insert_type == SystemReminderInsertType.DYNAMIC


def test_store_set_uses_forever_consume_by_default() -> None:
    store = SystemReminderStore()

    store.set("actor", name="goal", content="A")

    assert store.get_items("actor")[0].consume_type == SystemReminderConsumeType.FOREVER


def test_store_set_accepts_once_consume_for_dynamic_reminder() -> None:
    store = SystemReminderStore()

    store.set(
        "actor",
        name="goal",
        content="A",
        insert_type="dynamic",
        consume="once",
    )

    assert store.get_items("actor")[0].consume_type == SystemReminderConsumeType.ONCE


def test_store_set_rejects_invalid_insert_type() -> None:
    store = SystemReminderStore()

    with pytest.raises(ValueError, match="insert_type"):
        store.set("actor", name="goal", content="A", insert_type="tail")


def test_store_set_rejects_invalid_consume_type() -> None:
    store = SystemReminderStore()

    with pytest.raises(ValueError, match="consume"):
        store.set("actor", name="goal", content="A", insert_type="dynamic", consume="later")


def test_store_set_rejects_once_consume_for_fixed_reminder() -> None:
    store = SystemReminderStore()

    with pytest.raises(ValueError, match="consume=once"):
        store.set("actor", name="goal", content="A", consume="once")


def test_store_get_all_in_bucket() -> None:
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    store.set("actor", name="b", content="B")

    text = store.get("actor")
    assert "[a]\nA" in text
    assert "[b]\nB" in text


def test_store_get_filters_by_names_and_keeps_names_order() -> None:
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    store.set("actor", name="b", content="B")

    text = store.get("actor", names=["b", "a"])
    assert text == "[b]\nB\n\n[a]\nA"


def test_store_get_items_filters_by_names_and_keeps_names_order() -> None:
    store = SystemReminderStore()
    store.set("actor", name="a", content="A", insert_type="fixed")
    store.set("actor", name="b", content="B", insert_type="dynamic")

    items = store.get_items("actor", names=["b", "a"])

    assert items == [
        SystemReminderItem("b", "B", SystemReminderInsertType.DYNAMIC),
        SystemReminderItem("a", "A", SystemReminderInsertType.FIXED),
    ]


def test_store_get_rejects_empty_name_in_names() -> None:
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    with pytest.raises(ValueError, match="names"):
        store.get("actor", names=[""])


def test_store_delete_removes_item_and_empty_bucket() -> None:
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")
    store.set("actor", name="b", content="B")

    assert store.delete("actor", "a") is True
    assert store.get("actor") == "[b]\nB"

    assert store.delete("actor", "b") is True
    assert store.get("actor") == ""


def test_store_delete_returns_false_when_missing() -> None:
    store = SystemReminderStore()
    store.set("actor", name="a", content="A")

    assert store.delete("actor", "missing") is False
    assert store.get("actor") == "[a]\nA"


def test_global_store_singleton_and_reset() -> None:
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
