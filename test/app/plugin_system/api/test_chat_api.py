"""chat_api 模块测试。"""

from __future__ import annotations

from typing import cast

import pytest

from src.app.plugin_system.api import chat_api
from src.app.plugin_system.types import ChatType
from src.core.components.base.chatter import BaseChatter


def test_get_all_chatters_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_all_chatters 应委托给 ChatterManager。"""

    class _FakeManager:
        def get_all_chatters(self) -> dict[str, type[object]]:
            return {"demo:chatter:demo": object}

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_all_chatters()

    assert "demo:chatter:demo" in result


def test_get_chatters_for_plugin_requires_name() -> None:
    """plugin_name 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="plugin_name 不能为空"):
        chat_api.get_chatters_for_plugin("")


def test_get_chatters_for_plugin_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_chatters_for_plugin 应委托给 ChatterManager。"""

    class _FakeManager:
        def get_chatters_for_plugin(self, plugin_name: str) -> dict[str, type[object]]:
            return {f"{plugin_name}:chatter:demo": object}

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_chatters_for_plugin("demo_plugin")

    assert "demo_plugin:chatter:demo" in result


def test_get_chatter_class_requires_signature() -> None:
    """signature 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="signature 不能为空"):
        chat_api.get_chatter_class("")


def test_get_chatter_class_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_chatter_class 应委托给 ChatterManager。"""

    class _FakeManager:
        def get_chatter_class(self, signature: str) -> type[object] | None:
            if signature == "demo:chatter:demo":
                return object
            return None

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_chatter_class("demo:chatter:demo")

    assert result is object


def test_register_active_chatter_requires_stream_id() -> None:
    """stream_id 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="stream_id 不能为空"):
        chat_api.register_active_chatter("", cast(BaseChatter, object()))


def test_register_active_chatter_requires_chatter() -> None:
    """chatter 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="chatter 不能为空"):
        chat_api.register_active_chatter("stream_1", cast(BaseChatter, None))


def test_register_active_chatter_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """register_active_chatter 应委托给 ChatterManager。"""
    captured: dict[str, object] = {}
    chatter = cast(BaseChatter, object())

    class _FakeManager:
        def register_active_chatter(self, stream_id: str, chatter_obj: object) -> None:
            captured["stream_id"] = stream_id
            captured["chatter"] = chatter_obj

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    chat_api.register_active_chatter("stream_1", chatter)

    assert captured["stream_id"] == "stream_1"
    assert captured["chatter"] is chatter


def test_get_active_chatters_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_active_chatters 应委托给 ChatterManager。"""

    class _FakeManager:
        def get_active_chatters(self) -> dict[str, object]:
            return {"stream_1": object()}

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_active_chatters()

    assert "stream_1" in result


def test_unregister_active_chatter_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """unregister_active_chatter 应委托给 ChatterManager。"""

    class _FakeManager:
        def unregister_active_chatter(self, stream_id: str) -> bool:
            return stream_id == "stream_1"

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.unregister_active_chatter("stream_1")

    assert result is True


def test_unregister_active_chatter_requires_stream_id() -> None:
    """stream_id 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="stream_id 不能为空"):
        chat_api.unregister_active_chatter("")


def test_get_or_create_chatter_for_stream_normalizes_chat_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_type 为 ChatType 时应传递其 value。"""
    captured: dict[str, str] = {}

    class _FakeManager:
        def get_or_create_chatter_for_stream(
            self,
            stream_id: str,
            chat_type: str,
            platform: str,
        ) -> object | None:
            captured["stream_id"] = stream_id
            captured["chat_type"] = chat_type
            captured["platform"] = platform
            return object()

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_or_create_chatter_for_stream(
        "stream_1",
        ChatType.PRIVATE,
        "qq",
    )

    assert result is not None
    assert captured["chat_type"] == "private"
    assert captured["stream_id"] == "stream_1"
    assert captured["platform"] == "qq"


def test_get_or_create_chatter_for_stream_accepts_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_type 为 str 时应直接传递。"""
    captured: dict[str, str] = {}

    class _FakeManager:
        def get_or_create_chatter_for_stream(
            self,
            stream_id: str,
            chat_type: str,
            platform: str,
        ) -> object | None:
            captured["chat_type"] = chat_type
            return object()

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_or_create_chatter_for_stream(
        "stream_1",
        "group",
        "qq",
    )

    assert result is not None
    assert captured["chat_type"] == "group"


def test_get_or_create_chatter_for_stream_rejects_invalid_chat_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_type 非法时应抛出 TypeError。"""

    class _FakeManager:
        def get_or_create_chatter_for_stream(
            self,
            stream_id: str,
            chat_type: str,
            platform: str,
        ) -> object | None:
            return object()

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    with pytest.raises(TypeError, match="chat_type 必须是 ChatType 或 str"):
        chat_api.get_or_create_chatter_for_stream(
            "stream_1",
            cast(ChatType | str, 1),
            "qq",
        )


def test_get_chatter_by_stream_requires_stream_id() -> None:
    """stream_id 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="stream_id 不能为空"):
        chat_api.get_chatter_by_stream("")


def test_get_chatter_by_stream_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_chatter_by_stream 应委托给 ChatterManager。"""

    class _FakeManager:
        def get_chatter_by_stream(self, stream_id: str) -> object | None:
            if stream_id == "stream_1":
                return object()
            return None

    monkeypatch.setattr(chat_api, "_get_chatter_manager", lambda: _FakeManager())

    result = chat_api.get_chatter_by_stream("stream_1")

    assert result is not None
