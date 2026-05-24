"""Tests for prompt_api."""

from __future__ import annotations

import pytest

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.types import PromptTemplate
from src.core.prompt import SystemReminderConsumeType, SystemReminderInsertType


def test_get_template_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        prompt_api.get_template("")


def test_get_or_create_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        prompt_api.get_or_create("", "Hello {name}")


def test_get_or_create_requires_template() -> None:
    with pytest.raises(ValueError, match="template"):
        prompt_api.get_or_create("greet", "")


def test_register_template_requires_template() -> None:
    with pytest.raises(ValueError, match="template"):
        prompt_api.register_template(None)  # type: ignore[arg-type]


def test_register_template_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeManager:
        def register_template(self, template: PromptTemplate) -> None:
            captured["template"] = template

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    template = PromptTemplate(name="demo", template="Hello {name}")
    prompt_api.register_template(template)

    assert captured["template"] is template


def test_unregister_template_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeManager:
        def unregister_template(self, name: str) -> bool:
            return name == "demo"

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    assert prompt_api.unregister_template("demo") is True


def test_list_templates_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeManager:
        def list_templates(self) -> list[str]:
            return ["a", "b"]

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    assert prompt_api.list_templates() == ["a", "b"]


def test_count_templates_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeManager:
        def count(self) -> int:
            return 2

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    assert prompt_api.count_templates() == 2


def test_add_system_reminder_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        prompt_api.add_system_reminder("actor", name="", content="c")


def test_add_system_reminder_requires_content() -> None:
    with pytest.raises(ValueError, match="content"):
        prompt_api.add_system_reminder("actor", name="n", content="")


def test_add_system_reminder_bucket_validation_delegates_to_store() -> None:
    with pytest.raises(ValueError, match="bucket"):
        prompt_api.add_system_reminder("", name="n", content="c")


def test_add_system_reminder_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeStore:
        def set(
            self,
            bucket: str,
            name: str,
            content: str,
            insert_type: str | SystemReminderInsertType,
            consume: str | SystemReminderConsumeType,
        ) -> None:
            captured["bucket"] = bucket
            captured["name"] = name
            captured["content"] = content
            captured["insert_type"] = insert_type
            captured["consume"] = consume

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.add_system_reminder("actor", name="n", content="c")
    assert captured == {
        "bucket": "actor",
        "name": "n",
        "content": "c",
        "insert_type": SystemReminderInsertType.FIXED,
        "consume": SystemReminderConsumeType.FOREVER,
    }


def test_add_system_reminder_delegates_custom_insert_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeStore:
        def set(
            self,
            bucket: str,
            name: str,
            content: str,
            insert_type: str | SystemReminderInsertType,
            consume: str | SystemReminderConsumeType,
        ) -> None:
            captured["bucket"] = bucket
            captured["name"] = name
            captured["content"] = content
            captured["insert_type"] = insert_type
            captured["consume"] = consume

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.add_system_reminder("actor", name="n", content="c", insert_type="dynamic")
    assert captured == {
        "bucket": "actor",
        "name": "n",
        "content": "c",
        "insert_type": "dynamic",
        "consume": SystemReminderConsumeType.FOREVER,
    }


def test_add_system_reminder_delegates_custom_consume(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeStore:
        def set(
            self,
            bucket: str,
            name: str,
            content: str,
            insert_type: str | SystemReminderInsertType,
            consume: str | SystemReminderConsumeType,
        ) -> None:
            captured["bucket"] = bucket
            captured["name"] = name
            captured["content"] = content
            captured["insert_type"] = insert_type
            captured["consume"] = consume

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.add_system_reminder(
        "actor",
        name="n",
        content="c",
        insert_type="dynamic",
        consume="once",
    )
    assert captured == {
        "bucket": "actor",
        "name": "n",
        "content": "c",
        "insert_type": "dynamic",
        "consume": "once",
    }


def test_get_system_reminder_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeStore:
        def get(self, bucket: str, names: list[str] | None = None) -> str:
            captured["bucket"] = bucket
            captured["names"] = names
            return "ok"

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    assert prompt_api.get_system_reminder("actor", names=["a"]) == "ok"
    assert captured == {"bucket": "actor", "names": ["a"]}
