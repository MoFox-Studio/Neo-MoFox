"""send_api 指定 adapter 签名测试。

该模块验证：当传入 adapter_signature 时，send_api 应直接通过该适配器发送消息，
不再按 platform 推断适配器，platform 与 bot_info 均从该适配器实例获取。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.plugin_system.api import send_api
from src.core.models.message import Message, MessageType


@dataclass
class _Captured:
    """捕获 send_message 调用时的消息与 adapter_signature。"""

    message: Message | None = None
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
            message: Message,
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
async def test_send_image_marks_only_opted_in_binary_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图片内联标记仅在显式选择时随媒体元信息传递。"""
    captured = _Captured()
    fakes = _build_fakes(captured, adapter_platform="qq")
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_image(
        "aGVsbG8=", stream_id="some_stream",
        adapter_signature="onebot:adapter:napcat", include_in_context=True,
    )
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    assert captured.message.content["media"][0]["include_in_context"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("media_url", ["https://example.com/image.png", "file:///media/image.png"])
async def test_send_image_url_with_legacy_context_flag_uses_placeholder(
    monkeypatch: pytest.MonkeyPatch, media_url: str,
) -> None:
    """旧图片入口发送 URL 时忽略仅适用于 base64 的内联选项。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_image(
        media_url, "some_stream",
        adapter_signature="onebot:adapter:napcat", include_in_context=True,
    )
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    item = captured.message.content["media"][0]
    assert item["context_mode"] == "placeholder"
    assert "include_in_context" not in item
    assert item["data"] == media_url

    assert await send_api.send_image(
        "aGVsbG8=", stream_id="some_stream",
        adapter_signature="onebot:adapter:napcat",
    )
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    assert "include_in_context" not in captured.message.content["media"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_type,message_type",
    [
        ("image", MessageType.IMAGE),
        ("emoji", MessageType.EMOJI),
        ("voice", MessageType.VOICE),
        ("video", MessageType.VIDEO),
    ],
)
async def test_send_media_defaults_to_placeholder(
    monkeypatch: pytest.MonkeyPatch, media_type: send_api.MediaType, message_type: MessageType,
) -> None:
    """四类 Bot 媒体共享占位符默认策略，不隐式识别。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_media(
        media_type, "aGVsbG8=", "some_stream",
        adapter_signature="onebot:adapter:napcat",
    )
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    assert captured.message.message_type == message_type
    assert captured.message.content["media"][0]["context_mode"] == "placeholder"


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["image", "emoji", "voice", "video"])
async def test_send_media_provided_uses_caller_text_without_recognition(
    monkeypatch: pytest.MonkeyPatch, media_type: send_api.MediaType,
) -> None:
    """四类媒体均可使用调用者文案，不触发框架媒体识别。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)
    recognize = AsyncMock()
    monkeypatch.setattr("src.app.plugin_system.api.media_api.recognize_media", recognize)

    text = "  自定义上下文 [语音] 原文  "
    assert await send_api.send_media(
        media_type, "aGVsbG8=", "some_stream", context_mode="provided",
        processed_plain_text=text, adapter_signature="onebot:adapter:napcat",
    )
    recognize.assert_not_awaited()
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    assert captured.message.processed_plain_text == text
    assert captured.message.content["text"] == text
    assert captured.message.content["media"][0]["context_mode"] == "provided"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "label"),
    [("image", "图片"), ("emoji", "表情包"), ("voice", "语音"), ("video", "视频")],
)
async def test_send_media_caption_wraps_caller_text_without_recognition(
    monkeypatch: pytest.MonkeyPatch, media_type: send_api.MediaType, label: str,
) -> None:
    """四类媒体的调用者描述由框架包装，无需模型识别。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)
    recognize = AsyncMock()
    monkeypatch.setattr("src.app.plugin_system.api.media_api.recognize_media", recognize)

    assert await send_api.send_media(
        media_type, "aGVsbG8=", "some_stream", processed_plain_text="晚安",
        context_mode="caption", adapter_signature="onebot:adapter:napcat",
    )
    recognize.assert_not_awaited()
    message = captured.message
    assert isinstance(message, Message)
    assert isinstance(message.content, dict)
    assert message.processed_plain_text == f"[{label}:晚安]"
    assert message.content["text"] == message.processed_plain_text
    assert message.content["media"][0]["context_mode"] == "caption"


@pytest.mark.asyncio
async def test_send_voice_explicit_text_uses_caption_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """语音调用者提供的原文应进入语音描述占位符，不触发识别。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)
    recognize = AsyncMock()
    monkeypatch.setattr("src.app.plugin_system.api.media_api.recognize_media", recognize)

    text = "明天十点开会"
    assert await send_api.send_voice(
        "aGVsbG8=", "some_stream", processed_plain_text=text,
        adapter_signature="onebot:adapter:napcat",
    )
    recognize.assert_not_awaited()
    message = captured.message
    assert isinstance(message, Message)
    assert isinstance(message.content, dict)
    assert message.processed_plain_text == f"[语音:{text}]"
    assert message.content["text"] == f"[语音:{text}]"
    assert message.content["media"][0]["context_mode"] == "caption"


@pytest.mark.asyncio
async def test_send_voice_url_caption_has_no_media_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未缓存的 URL 语音只有原文描述，不生成不可回查的媒体 ID。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_voice(
        "https://example.org/voice.wav", "some_stream", processed_plain_text="晚安",
        adapter_signature="onebot:adapter:napcat",
    )
    message = captured.message
    assert isinstance(message, Message)
    assert message.processed_plain_text == "[语音:晚安]"
    assert isinstance(message.content, dict)
    assert message.content["media"] == [{
        "type": "voice", "data": "https://example.org/voice.wav", "context_mode": "caption",
    }]


@pytest.mark.asyncio
async def test_send_voice_without_text_keeps_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未指定文字时语音仍使用现有的占位符历史。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_voice(
        "aGVsbG8=", "some_stream", adapter_signature="onebot:adapter:napcat",
    )
    message = captured.message
    assert isinstance(message, Message)
    assert isinstance(message.content, dict)
    assert message.processed_plain_text == "[语音]"
    assert "context_mode" not in message.content["media"][0]


@pytest.mark.asyncio
async def test_send_voice_rejects_blank_explicit_text() -> None:
    """显式传入空白语音文字不能作为有效上下文。"""
    with pytest.raises(ValueError, match="processed_plain_text"):
        await send_api.send_voice("aGVsbG8=", "some_stream", processed_plain_text="  ")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "", "  \t  "])
@pytest.mark.parametrize("context_mode", ["provided", "caption"])
async def test_send_media_caller_text_requires_nonempty_text(
    text: str | None, context_mode: send_api.MediaContextMode,
) -> None:
    """调用者模式不能用空白内容伪装为有效上下文。"""
    with pytest.raises(ValueError, match="processed_plain_text"):
        await send_api.send_media(
            "voice", "aGVsbG8=", "some_stream",
            context_mode=context_mode, processed_plain_text=text,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("media_url", ["https://example.org/video.mp4", "file:///media/video.mp4"])
async def test_send_media_provided_supports_url(
    monkeypatch: pytest.MonkeyPatch, media_url: str,
) -> None:
    """媒体资源 URI 可自带文本，但不承诺资源已被本地缓存。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_media(
        "video", media_url, "some_stream",
        processed_plain_text="视频字幕", context_mode="provided",
        adapter_signature="onebot:adapter:napcat",
    )
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    item = captured.message.content["media"][0]
    assert item == {"type": "video", "data": media_url, "context_mode": "provided"}
    assert captured.message.processed_plain_text == "视频字幕"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "label"),
    [("image", "图片"), ("emoji", "表情包"), ("voice", "语音"), ("video", "视频")],
)
async def test_send_media_caption_url_does_not_claim_cached_id(
    monkeypatch: pytest.MonkeyPatch, media_type: send_api.MediaType, label: str,
) -> None:
    """URL 媒体可附调用者描述，但不能生成不可回查的 ID。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)

    assert await send_api.send_media(
        media_type, "https://example.org/media", "some_stream",
        processed_plain_text="原文", context_mode="caption",
        adapter_signature="onebot:adapter:napcat",
    )
    message = captured.message
    assert isinstance(message, Message)
    assert message.processed_plain_text == f"[{label}:原文]"
    assert isinstance(message.content, dict)
    assert message.content["media"] == [{
        "type": media_type, "data": "https://example.org/media", "context_mode": "caption",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("context_mode", ["native", "description"])
async def test_send_media_file_url_rejects_uncached_modes(context_mode: send_api.MediaContextMode) -> None:
    """文件资源尚未缓存时不能请求识别或原生内联。"""
    with pytest.raises(ValueError, match="URL 媒体不支持"):
        await send_api.send_media(
            "image", "file:///media/image.png", "some_stream",
            context_mode=context_mode,
        )


@pytest.mark.asyncio
async def test_send_media_rejects_unsupported_native_type() -> None:
    """不支持原生内联的媒体不可静默降级成成功。"""
    with pytest.raises(ValueError, match="native"):
        await send_api.send_media("voice", "aGVsbG8=", "some_stream", context_mode="native")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "label"),
    [("image", "图片"), ("emoji", "表情包"), ("voice", "语音"), ("video", "视频")],
)
async def test_send_media_description_uses_recognition(
    monkeypatch: pytest.MonkeyPatch, media_type: send_api.MediaType, label: str,
) -> None:
    """描述策略对每类媒体使用对应识别入口。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)
    recognize = AsyncMock(return_value="识别结果")
    monkeypatch.setattr("src.app.plugin_system.api.media_api.recognize_media", recognize)

    assert await send_api.send_media(
        media_type, "aGVsbG8=", "some_stream", context_mode="description",
        adapter_signature="onebot:adapter:napcat",
    )
    recognize.assert_awaited_once_with("aGVsbG8=", media_type)
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    assert captured.message.content["media"][0]["context_mode"] == "description"
    assert captured.message.processed_plain_text == f"[{label}:识别结果]"
    assert captured.message.content["text"] == captured.message.processed_plain_text


@pytest.mark.asyncio
async def test_send_media_description_without_result_uses_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """识别未得到描述时保留占位符，不宣称历史包含描述。"""
    captured = _Captured()
    fakes = _build_fakes(captured)
    fakes["adapter_manager"].register("onebot:adapter:napcat")
    _apply_fakes(monkeypatch, fakes)
    monkeypatch.setattr(
        "src.app.plugin_system.api.media_api.recognize_media", AsyncMock(return_value=None),
    )

    assert await send_api.send_media(
        "image", "aGVsbG8=", "some_stream", context_mode="description",
        adapter_signature="onebot:adapter:napcat",
    )
    assert isinstance(captured.message, Message)
    assert isinstance(captured.message.content, dict)
    assert captured.message.content["media"][0]["context_mode"] == "placeholder"
    assert captured.message.processed_plain_text == "[图片]"


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
            message: Message,
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
