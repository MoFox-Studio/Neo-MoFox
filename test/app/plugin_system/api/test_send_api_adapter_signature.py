"""send_api 指定 adapter 签名测试。

该模块验证：当传入 adapter_signature 时，send_api 应直接通过该适配器发送消息，
不再按 platform 推断适配器，platform 与 bot_info 均从该适配器实例获取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.app.plugin_system.api import send_api
from src.core.models.message import MessageType


@dataclass
class _Captured:
    """捕获 send_message 调用时的消息与 adapter_signature。"""

    message: object | None = None
    adapter_signature: str | None = None
    called: bool = False


def _build_fakes(captured: _Captured, *, adapter_platform: str = "qq") -> dict[str, Any]:
    """构造一组测试用的 fake 管理器与 sender。

    Args:
        captured: 用于捕获 send_message 调用信息的容器
        adapter_platform: fake 适配器上报的 platform 名称

    Returns:
        包含 _FakeAdapterManager / _FakeMessageSender 工厂函数的字典
    """

    class _FakeAdapter:
        platform = adapter_platform

        async def get_bot_info(self) -> dict[str, str]:
            return {"bot_id": "bot_001", "bot_name": "TestBot"}

    class _FakeAdapterManager:
        def __init__(self) -> None:
            self._adapters: dict[str, _FakeAdapter] = {}

        def register(self, signature: str) -> None:
            self._adapters[signature] = _FakeAdapter()

        def get_adapter(self, signature: str) -> _FakeAdapter | None:
            return self._adapters.get(signature)

        async def get_bot_info_by_platform(
            self, platform: str
        ) -> dict[str, str] | None:
            raise AssertionError(
                "指定 adapter_signature 时不应走 platform 推断分支"
            )

    class _FakeStreamManager:
        async def get_stream_info(
            self, stream_id: str
        ) -> dict[str, object] | None:
            # 指定 adapter 签名时 stream_info 仍会被读取以解析 chat_type 等，
            # 但 platform 字段不应被使用
            return {
                "stream_id": stream_id,
                "chat_type": "group",
                "group_id": "999",
            }

    class _FakeMessageSender:
        async def send_message(
            self,
            message: object,
            adapter_signature: str | None = None,
        ) -> bool:
            captured.called = True
            captured.message = message
            captured.adapter_signature = adapter_signature
            return True

    fake_adapter_manager = _FakeAdapterManager()

    def _fake_get_stream_manager() -> _FakeStreamManager:
        return _FakeStreamManager()

    def _fake_get_adapter_manager() -> _FakeAdapterManager:
        return fake_adapter_manager

    def _fake_get_message_sender() -> _FakeMessageSender:
        return _FakeMessageSender()

    return {
        "adapter_manager": fake_adapter_manager,
        "get_stream_manager": _fake_get_stream_manager,
        "get_adapter_manager": _fake_get_adapter_manager,
        "get_message_sender": _fake_get_message_sender,
    }


def _apply_fakes(monkeypatch: pytest.MonkeyPatch, fakes: dict[str, Any]) -> None:
    """将 fake 工厂挂到对应模块路径上。"""
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        fakes["get_stream_manager"],
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        fakes["get_adapter_manager"],
    )
    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        fakes["get_message_sender"],
    )


@pytest.mark.asyncio
async def test_send_text_with_adapter_signature_uses_adapter_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指定 adapter_signature 时应使用适配器自身的 platform，忽略 platform 参数。"""

    captured = _Captured()
    fakes = _build_fakes(captured, adapter_platform="wx")
    fakes["adapter_manager"].register("my_plugin:adapter:wx_adapter")
    _apply_fakes(monkeypatch, fakes)

    ok = await send_api.send_text(
        "hi",
        stream_id="some_stream",
        platform="should_be_ignored",
        adapter_signature="my_plugin:adapter:wx_adapter",
    )

    assert ok is True
    assert captured.called is True
    assert getattr(captured.message, "platform") == "wx"
    assert captured.adapter_signature == "my_plugin:adapter:wx_adapter"
    assert getattr(captured.message, "sender_id") == "bot_001"
    assert getattr(captured.message, "sender_name") == "TestBot"


@pytest.mark.asyncio
async def test_send_text_with_adapter_signature_not_active_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """指定未启动的 adapter_signature 时应直接失败。"""

    captured = _Captured()
    fakes = _build_fakes(captured)
    _apply_fakes(monkeypatch, fakes)

    ok = await send_api.send_text(
        "hi",
        stream_id="some_stream",
        adapter_signature="my_plugin:adapter:not_started",
    )

    assert ok is False
    assert captured.called is False


@pytest.mark.asyncio
async def test_send_image_with_adapter_signature_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_image 应将 adapter_signature 透传到底层 sender。"""

    captured = _Captured()
    fakes = _build_fakes(captured, adapter_platform="qq")
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    ok = await send_api.send_image(
        "base64_data",
        stream_id="some_stream",
        adapter_signature="onebot:adapter:napcat",
    )

    assert ok is True
    assert captured.adapter_signature == "onebot:adapter:napcat"
    assert getattr(captured.message, "message_type") == MessageType.IMAGE


@pytest.mark.asyncio
async def test_send_custom_with_adapter_signature_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_custom 在未知消息类型分支也应透传 adapter_signature。"""

    captured = _Captured()
    fakes = _build_fakes(captured, adapter_platform="qq")
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    ok = await send_api.send_custom(
        {"song": "abc"},
        message_type="music",
        stream_id="some_stream",
        adapter_signature="onebot:adapter:napcat",
    )

    assert ok is True
    assert captured.adapter_signature == "onebot:adapter:napcat"
    assert getattr(captured.message, "message_type") == MessageType.UNKNOWN


@pytest.mark.asyncio
async def test_send_message_passes_adapter_signature_to_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_message 应将 adapter_signature 透传给 sender.send_message。"""

    captured = _Captured()

    class _FakeMessageSender:
        async def send_message(
            self,
            message: object,
            adapter_signature: str | None = None,
        ) -> bool:
            captured.called = True
            captured.message = message
            captured.adapter_signature = adapter_signature
            return True

    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: _FakeMessageSender(),
    )

    from src.core.models.message import Message

    msg = Message(content="hi", platform="qq", stream_id="s1")
    ok = await send_api.send_message(
        msg, adapter_signature="onebot:adapter:napcat"
    )

    assert ok is True
    assert captured.adapter_signature == "onebot:adapter:napcat"


@pytest.mark.asyncio
async def test_send_batch_passes_adapter_signature_to_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_batch 应对每条消息透传 adapter_signature。"""

    signatures: list[str | None] = []

    class _FakeMessageSender:
        async def send_message(
            self,
            message: object,
            adapter_signature: str | None = None,
        ) -> bool:
            signatures.append(adapter_signature)
            return True

    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: _FakeMessageSender(),
    )

    from src.core.models.message import Message

    messages = [
        Message(content="a", platform="qq", stream_id="s1"),
        Message(content="b", platform="qq", stream_id="s2"),
    ]
    results = await send_api.send_batch(
        messages, adapter_signature="onebot:adapter:napcat"
    )

    assert results == [True, True]
    assert signatures == ["onebot:adapter:napcat", "onebot:adapter:napcat"]


@pytest.mark.asyncio
async def test_send_batch_parallel_passes_adapter_signature_to_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_batch_parallel 应对每条消息透传 adapter_signature。"""

    signatures: list[str | None] = []

    class _FakeMessageSender:
        async def send_message(
            self,
            message: object,
            adapter_signature: str | None = None,
        ) -> bool:
            signatures.append(adapter_signature)
            return True

    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: _FakeMessageSender(),
    )

    from src.core.models.message import Message

    messages = [
        Message(content="a", platform="qq", stream_id="s1"),
        Message(content="b", platform="qq", stream_id="s2"),
    ]
    results = await send_api.send_batch_parallel(
        messages, adapter_signature="onebot:adapter:napcat"
    )

    assert results == [True, True]
    assert len(signatures) == 2
    assert all(s == "onebot:adapter:napcat" for s in signatures)


@pytest.mark.asyncio
async def test_send_text_with_image_passes_adapter_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_text_with_image 应对文本与图片均透传 adapter_signature。"""

    captured_signatures: list[str | None] = []

    class _FakeAdapterManager:
        def get_adapter(self, signature: str) -> object | None:
            class _FakeAdapter:
                platform = "qq"

                async def get_bot_info(self) -> dict[str, str]:
                    return {"bot_id": "b1", "bot_name": "Bot"}

            return _FakeAdapter()

        async def get_bot_info_by_platform(
            self, platform: str
        ) -> dict[str, str] | None:
            raise AssertionError("不应走 platform 推断分支")

    class _FakeStreamManager:
        async def get_stream_info(
            self, stream_id: str
        ) -> dict[str, object] | None:
            return {
                "stream_id": stream_id,
                "chat_type": "group",
                "group_id": "1",
            }

    class _FakeMessageSender:
        async def send_message(
            self,
            message: object,
            adapter_signature: str | None = None,
        ) -> bool:
            captured_signatures.append(adapter_signature)
            return True

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: _FakeStreamManager(),
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: _FakeAdapterManager(),
    )
    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: _FakeMessageSender(),
    )

    ok = await send_api.send_text_with_image(
        "text",
        "image_data",
        stream_id="s1",
        adapter_signature="onebot:adapter:napcat",
    )

    assert ok is True
    assert captured_signatures == [
        "onebot:adapter:napcat",
        "onebot:adapter:napcat",
    ]
