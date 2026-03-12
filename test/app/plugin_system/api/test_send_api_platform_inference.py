"""send_api 平台推断逻辑测试。

该模块验证：当 stream_id 已哈希化时，send_api 不应再通过字符串解析推断平台，
而应从 StreamManager 的 stream_info 中读取 platform。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.app.plugin_system.api import send_api


@dataclass
class _Captured:
    message: object | None = None
    called: bool = False


@pytest.mark.asyncio
async def test_send_text_infers_platform_from_stream_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未传入 platform 时应从 stream_info 读取，而不是解析 stream_id。"""

    class _FakeStreamManager:
        async def get_stream_info(self, stream_id: str) -> dict[str, object] | None:
            assert stream_id == "hashed_stream_id"
            return {
                "stream_id": stream_id,
                "platform": "wx",
                "chat_type": "group",
                "group_id": "123",
            }

    class _FakeAdapterManager:
        async def get_bot_info_by_platform(self, platform: str) -> dict[str, str] | None:
            assert platform == "wx"
            return {"bot_id": "b1", "bot_nickname": "Bot"}

    captured = _Captured()

    class _FakeMessageSender:
        async def send_message(self, message: object) -> bool:
            captured.called = True
            captured.message = message
            return True

    def _fake_get_stream_manager() -> _FakeStreamManager:
        return _FakeStreamManager()

    def _fake_get_adapter_manager() -> _FakeAdapterManager:
        return _FakeAdapterManager()

    def _fake_get_message_sender() -> _FakeMessageSender:
        return _FakeMessageSender()

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        _fake_get_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        _fake_get_adapter_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        _fake_get_message_sender,
    )

    ok = await send_api.send_text("hi", stream_id="hashed_stream_id")

    assert ok is True
    assert captured.called is True
    assert getattr(captured.message, "platform") == "wx"


@pytest.mark.asyncio
async def test_send_text_returns_false_when_platform_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无法解析 platform 时应直接失败，避免错误默认平台。"""

    class _FakeStreamManager:
        async def get_stream_info(self, stream_id: str) -> dict[str, object] | None:
            return None

    class _FakeAdapterManager:
        async def get_bot_info_by_platform(self, platform: str) -> dict[str, str] | None:
            raise AssertionError("不应在 platform 未解析时查询 bot_info")

    class _FakeMessageSender:
        async def send_message(self, message: object) -> bool:
            raise AssertionError("不应在 platform 未解析时发送消息")

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

    ok = await send_api.send_text("hi", stream_id="hashed_stream_id")

    assert ok is False


@pytest.mark.asyncio
async def test_broadcast_text_resolves_platform_per_stream_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """broadcast_text 在未传 platform 时应逐个从 stream_info 读取 platform。"""

    class _FakeStreamManager:
        async def get_stream_info(self, stream_id: str) -> dict[str, object] | None:
            if stream_id == "s1":
                return {"platform": "qq"}
            if stream_id == "s2":
                return None
            raise AssertionError(f"unexpected stream_id: {stream_id}")

    async def _fake_send_batch_parallel(messages: list[object]) -> list[bool]:
        # 只会为能解析平台的 stream_id 构建消息
        assert len(messages) == 1
        assert getattr(messages[0], "stream_id") == "s1"
        assert getattr(messages[0], "platform") == "qq"
        return [True]

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: _FakeStreamManager(),
    )
    monkeypatch.setattr(send_api, "send_batch_parallel", _fake_send_batch_parallel)

    results = await send_api.broadcast_text("notice", stream_ids=["s1", "s2"])

    assert results == {"s1": True, "s2": False}
