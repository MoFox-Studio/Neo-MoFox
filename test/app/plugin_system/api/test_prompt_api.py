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


# ── SystemReminderBucket 枚举值兼容性测试 ───────────────────────


# ── 流隔离 reminder API 测试 ──────────────────────────────────────


def test_stream_bucket_requires_non_empty_stream_id() -> None:
    """_stream_bucket 对空 stream_id 抛 ValueError。"""
    with pytest.raises(ValueError, match="stream_id"):
        prompt_api._stream_bucket("", "actor")


def test_stream_bucket_constructs_key() -> None:
    """_stream_bucket 构造 stream:{stream_id}:{bucket} key。"""
    assert prompt_api._stream_bucket("s1", "actor") == "stream:s1:actor"
    assert prompt_api._stream_bucket("s1", "sub_agent") == "stream:s1:sub_agent"


def test_add_stream_reminder_requires_non_empty_stream_id() -> None:
    """add_stream_reminder 对空 stream_id 抛 ValueError。"""
    with pytest.raises(ValueError, match="stream_id"):
        prompt_api.add_stream_reminder("", "actor", "n", "c")


def test_add_stream_reminder_requires_name() -> None:
    """add_stream_reminder 对空 name 抛 ValueError。"""
    with pytest.raises(ValueError, match="name"):
        prompt_api.add_stream_reminder("s1", "actor", "", "c")


def test_add_stream_reminder_requires_content() -> None:
    """add_stream_reminder 对空 content 抛 ValueError。"""
    with pytest.raises(ValueError, match="content"):
        prompt_api.add_stream_reminder("s1", "actor", "n", "")


def test_add_stream_reminder_writes_to_stream_private_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_stream_reminder 写入 stream:{stream_id}:{bucket} 的流私有 bucket。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def set(
            self,
            bucket: str,
            name: str,
            content: str,
            insert_type: object,
            consume: object,
        ) -> None:
            captured["bucket"] = bucket
            captured["name"] = name
            captured["content"] = content
            captured["insert_type"] = insert_type
            captured["consume"] = consume

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.add_stream_reminder("s1", "actor", "n", "c")
    assert captured == {
        "bucket": "stream:s1:actor",
        "name": "n",
        "content": "c",
        "insert_type": SystemReminderInsertType.FIXED,
        "consume": SystemReminderConsumeType.FOREVER,
    }


def test_add_stream_reminder_passes_custom_insert_and_consume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """add_stream_reminder 转发自定义 insert_type/consume 到 store。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def set(
            self,
            bucket: str,
            name: str,
            content: str,
            insert_type: object,
            consume: object,
        ) -> None:
            captured["insert_type"] = insert_type
            captured["consume"] = consume

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.add_stream_reminder(
        "s1", "actor", "n", "c", insert_type="dynamic", consume="once"
    )
    assert captured == {"insert_type": "dynamic", "consume": "once"}


def test_get_stream_reminder_delegates_to_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_stream_reminder 从 stream:{stream_id}:{bucket} 读取内容。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def get(self, bucket: str, names: list[str] | None = None) -> str:
            captured["bucket"] = bucket
            captured["names"] = names
            return "ok"

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    assert prompt_api.get_stream_reminder("s1", "actor", names=["a"]) == "ok"
    assert captured == {"bucket": "stream:s1:actor", "names": ["a"]}


def test_delete_stream_reminder_delegates_to_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_stream_reminder 从 stream:{stream_id}:{bucket} 删除单条。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def delete(self, bucket: str, name: str) -> bool:
            captured["bucket"] = bucket
            captured["name"] = name
            return True

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    assert prompt_api.delete_stream_reminder("s1", "actor", "n") is True
    assert captured == {"bucket": "stream:s1:actor", "name": "n"}


def test_clear_stream_reminders_requires_non_empty_stream_id() -> None:
    """clear_stream_reminders 对空 stream_id 抛 ValueError。"""
    with pytest.raises(ValueError, match="stream_id"):
        prompt_api.clear_stream_reminders("")


def test_clear_stream_reminders_calls_clear_by_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear_stream_reminders 调用 store.clear_by_prefix(stream:{stream_id}:)。"""
    captured: dict[str, object] = {}

    class _FakeStore:
        def clear_by_prefix(self, prefix: str) -> None:
            captured["prefix"] = prefix

    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: _FakeStore())

    prompt_api.clear_stream_reminders("s1")
    assert captured == {"prefix": "stream:s1:"}


# ── 流隔离 reminder 集成测试（对真实 store 读写） ──────────────


def test_stream_reminder_isolation_from_global() -> None:
    """流私有 reminder 与全局 bucket 互相隔离。"""
    from src.core.prompt import reset_system_reminder_store

    reset_system_reminder_store()

    # 全局写入
    prompt_api.add_system_reminder("actor", "global_item", "GLOBAL")
    # 流私有写入
    prompt_api.add_stream_reminder("s1", "actor", "stream_item", "STREAM1")
    prompt_api.add_stream_reminder("s2", "actor", "stream_item", "STREAM2")

    # 全局读取：看不到流私有
    assert prompt_api.get_system_reminder("actor") == "[global_item]\nGLOBAL"
    # 流私有读取：看不到全局，也不跨流
    assert prompt_api.get_stream_reminder("s1", "actor") == "[stream_item]\nSTREAM1"
    assert prompt_api.get_stream_reminder("s2", "actor") == "[stream_item]\nSTREAM2"


def test_clear_stream_reminders_only_clears_target_stream() -> None:
    """clear_stream_reminders 只清除目标流，不影响其他流和全局。"""
    from src.core.prompt import reset_system_reminder_store

    reset_system_reminder_store()

    prompt_api.add_system_reminder("actor", "global_item", "GLOBAL")
    prompt_api.add_stream_reminder("s1", "actor", "stream_item", "STREAM1")
    prompt_api.add_stream_reminder("s2", "actor", "stream_item", "STREAM2")

    prompt_api.clear_stream_reminders("s1")

    # s1 被清空
    assert prompt_api.get_stream_reminder("s1", "actor") == ""
    # s2 不受影响
    assert prompt_api.get_stream_reminder("s2", "actor") == "[stream_item]\nSTREAM2"
    # 全局不受影响
    assert prompt_api.get_system_reminder("actor") == "[global_item]\nGLOBAL"


def test_delete_stream_reminder_only_deletes_target_name() -> None:
    """delete_stream_reminder 只删除目标 name，不影响同名全局。"""
    from src.core.prompt import reset_system_reminder_store

    reset_system_reminder_store()

    prompt_api.add_system_reminder("actor", "shared", "GLOBAL_VALUE")
    prompt_api.add_stream_reminder("s1", "actor", "shared", "STREAM_VALUE")

    assert (
        prompt_api.delete_stream_reminder("s1", "actor", "shared") is True
    )
    assert prompt_api.get_stream_reminder("s1", "actor") == ""
    # 全局同名 item 不受影响
    assert prompt_api.get_system_reminder("actor") == "[shared]\nGLOBAL_VALUE"
    # 再次删除返回 False
    assert prompt_api.delete_stream_reminder("s1", "actor", "shared") is False
