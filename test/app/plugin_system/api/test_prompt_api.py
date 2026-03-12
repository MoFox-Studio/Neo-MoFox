"""prompt_api 模块测试。"""

from __future__ import annotations

import pytest

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.types import PromptTemplate


def test_get_template_requires_name() -> None:
    """name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="name 不能为空"):
        prompt_api.get_template("")


def test_get_or_create_requires_name() -> None:
    """name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="name 不能为空"):
        prompt_api.get_or_create("", "Hello {name}")


def test_get_or_create_requires_template() -> None:
    """template 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="template 不能为空"):
        prompt_api.get_or_create("greet", "")


def test_register_template_requires_template() -> None:
    """template 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="template 不能为空"):
        prompt_api.register_template(None)  # type: ignore[arg-type]


def test_register_template_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """register_template 应委托给 PromptManager。"""
    captured: dict[str, object] = {}

    class _FakeManager:
        def register_template(self, template: PromptTemplate) -> None:
            captured["template"] = template

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    tmpl = PromptTemplate(name="demo", template="Hello {name}")
    prompt_api.register_template(tmpl)

    assert captured["template"] is tmpl


def test_unregister_template_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """unregister_template 应委托给 PromptManager。"""

    class _FakeManager:
        def unregister_template(self, name: str) -> bool:
            return name == "demo"

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    assert prompt_api.unregister_template("demo") is True


def test_list_templates_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_templates 应委托给 PromptManager。"""

    class _FakeManager:
        def list_templates(self) -> list[str]:
            return ["a", "b"]

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    assert prompt_api.list_templates() == ["a", "b"]


def test_count_templates_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """count_templates 应委托给 PromptManager。"""

    class _FakeManager:
        def count(self) -> int:
            return 2

    monkeypatch.setattr(prompt_api, "_get_prompt_manager", lambda: _FakeManager())

    assert prompt_api.count_templates() == 2


def test_add_system_reminder_requires_name() -> None:
    """name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="name 不能为空"):
        prompt_api.add_system_reminder("actor", name="", content="c")


def test_add_system_reminder_requires_content() -> None:
    """content 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="content 不能为空"):
        prompt_api.add_system_reminder("actor", name="n", content="")


def test_add_system_reminder_bucket_validation_delegates_to_store() -> None:
    """bucket 为空时应由 store 抛出 ValueError。"""
    with pytest.raises(ValueError, match="bucket 不能为空"):
        prompt_api.add_system_reminder("", name="n", content="c")


def test_add_system_reminder_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """add_system_reminder 应委托给 SystemReminderStore.set。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def set(self, bucket: str, name: str, content: str) -> None:
            captured["bucket"] = bucket
            captured["name"] = name
            captured["content"] = content

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.add_system_reminder("actor", name="n", content="c")
    assert captured == {"bucket": "actor", "name": "n", "content": "c"}


def test_get_system_reminder_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_system_reminder 应委托给 SystemReminderStore.get。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def get(self, bucket: str, names: list[str] | None = None) -> str:
            captured["bucket"] = bucket
            captured["names"] = names
            return "ok"

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    assert prompt_api.get_system_reminder("actor", names=["a"]) == "ok"
    assert captured == {"bucket": "actor", "names": ["a"]}
