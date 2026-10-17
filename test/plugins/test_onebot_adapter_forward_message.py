"""测试 onebot_adapter 转发消息解析功能。

覆盖递归解析合并转发、转发内的引用（reply）消息解析，
以及图片占位阈值与最大递归层数的可配置行为。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from mofox_wire import MessageEnvelope
from mofox_wire.types import UserRole

from plugins.onebot_adapter.config import OneBotAdapterConfig
from plugins.onebot_adapter.plugin import OneBotAdapter, OneBotAdapterPlugin
from plugins.onebot_adapter.src.handlers.to_core import message_handler as mh_module
from plugins.onebot_adapter.src.handlers.to_core.message_handler import MessageHandler
from src.core.transport.message_receive.converter import MessageConverter


class _FakeCoreSink:
    """满足 BaseAdapter 初始化所需的最小 CoreSink 替身。"""

    def set_outgoing_handler(self, _handler) -> None:
        pass

    def remove_outgoing_handler(self, _handler) -> None:
        pass

    async def push_outgoing(self, _message) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send(self, _message) -> None:
        pass

    async def send_many(self, _messages) -> None:
        pass


def _build_config(*, forward_image_threshold: int = 5, forward_max_depth: int = 3) -> OneBotAdapterConfig:
    """构造测试用 OneBotAdapterConfig。"""
    return OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 60,
                "forward_image_threshold": forward_image_threshold,
                "forward_max_depth": forward_max_depth,
            },
        }
    )


def _build_handler(*, forward_image_threshold: int = 5, forward_max_depth: int = 3) -> MessageHandler:
    """构造已加载测试配置的 MessageHandler。"""
    config = _build_config(
        forward_image_threshold=forward_image_threshold,
        forward_max_depth=forward_max_depth,
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    return adapter.message_handler


def _flatten_text(seg: dict) -> list[str]:
    """递归收集 seglist 中的所有文本内容。"""
    texts: list[str] = []
    if seg.get("type") == "text":
        texts.append(str(seg.get("data", "")))
    elif seg.get("type") == "seglist":
        for sub in seg.get("data", []):
            texts.extend(_flatten_text(sub))
    return texts


def _find_segs(seg: dict, seg_type: str) -> list[dict]:
    """递归查找指定类型的所有消息段。"""
    found: list[dict] = []
    if seg.get("type") == seg_type:
        found.append(seg)
    if seg.get("type") == "seglist":
        for sub in seg.get("data", []):
            found.extend(_find_segs(sub, seg_type))
    return found


class TestForwardMessageImageThreshold:
    """测试图片占位阈值可配置行为。"""

    @pytest.mark.asyncio
    async def test_below_threshold_parses_images_to_base64(self, monkeypatch) -> None:
        handler = _build_handler(forward_image_threshold=5)
        async def _fake_get_image_base64(url: str) -> str:
            return f"b64:{url}"
        monkeypatch.setattr(mh_module, "get_image_base64", _fake_get_image_base64)

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {"type": "image", "data": {"url": "http://img/a", "sub_type": 0}},
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        image_segs = _find_segs(result, "image")
        assert len(image_segs) == 1
        assert image_segs[0]["data"] == "b64:http://img/a"

    @pytest.mark.asyncio
    async def test_at_or_above_threshold_uses_placeholders(self, monkeypatch) -> None:
        handler = _build_handler(forward_image_threshold=1)
        async def _fake_get_image_base64(_url: str) -> str:
            raise AssertionError("不应解析图片为 base64")
        monkeypatch.setattr(mh_module, "get_image_base64", _fake_get_image_base64)

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {"type": "image", "data": {"url": "http://img/a", "sub_type": 0}},
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        assert "【Alice】:" in _flatten_text(result)
        assert "[图片]" in _flatten_text(result)
        assert _find_segs(result, "image") == []

    @pytest.mark.asyncio
    async def test_zero_threshold_always_uses_placeholders(self, monkeypatch) -> None:
        handler = _build_handler(forward_image_threshold=0)
        async def _fake_get_image_base64(_url: str) -> str:
            raise AssertionError("不应解析图片为 base64")
        monkeypatch.setattr(mh_module, "get_image_base64", _fake_get_image_base64)

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {"type": "image", "data": {"url": "http://img/a", "sub_type": 0}},
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        assert "[图片]" in _flatten_text(result)


class TestForwardMessageReplyParsing:
    """测试转发消息内的引用（reply）消息解析。"""

    @pytest.mark.asyncio
    async def test_reply_inside_forward_reads_embedded_text(self, monkeypatch) -> None:
        """转发内容中的引用消息直接读取 reply 段自带的 text/qq 预览。"""
        handler = _build_handler()

        async def _fake_get_message_detail(message_id):
            raise AssertionError("转发内 reply 不应调用 get_msg")

        monkeypatch.setattr(mh_module, "get_message_detail", _fake_get_message_detail)

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {"type": "reply", "data": {"id": 111, "qq": "2", "text": "原始引用文本"}},
                    {"type": "text", "data": "看这个"},
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list, raw_message={})
        assert result is not None
        texts = _flatten_text(result)
        joined = "".join(texts)
        assert "[回复<2(2)>：" in joined
        assert "原始引用文本" in joined
        assert "看这个" in joined

    @pytest.mark.asyncio
    async def test_reply_resolves_by_message_seq_within_forward(self, monkeypatch) -> None:
        """转发记录内 reply.data.id 指向 message_seq 时，应定位并解析被引用消息原文。"""
        handler = _build_handler()

        async def _fake_get_message_detail(message_id):
            raise AssertionError("转发内 reply 不应调用 get_msg")

        monkeypatch.setattr(mh_module, "get_message_detail", _fake_get_message_detail)

        # 模拟真实转发数据：第一条 message_seq=9511，第二条 reply 的 data.id="9511"
        forward_list = [
            {
                "sender": {"nickname": "一闪", "user_id": 2488036428},
                "message_seq": 9511,
                "message_id": 1716559280,
                "message": [{"type": "text", "data": {"text": "1678466448"}}],
            },
            {
                "sender": {"nickname": "一闪", "user_id": 2488036428},
                "message_seq": 9512,
                "message_id": 1844526065,
                "message": [
                    {"type": "reply", "data": {"id": "9511"}},
                    {"type": "text", "data": {"text": "19464648467"}},
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list, raw_message={})
        assert result is not None
        joined = "".join(_flatten_text(result))
        assert "1678466448" in joined
        assert "19464648467" in joined
        assert "回复<一闪(2488036428)>" in joined

    @pytest.mark.asyncio
    async def test_reply_without_embedded_text_uses_placeholder(self) -> None:
        """转发内 reply 缺少内嵌 text/qq 时，退化为可读占位符。"""
        handler = _build_handler()

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [{"type": "reply", "data": {"id": 222}}],
            },
        ]

        result = await handler.handle_forward_message(forward_list, raw_message={})
        assert result is not None
        joined = "".join(_flatten_text(result))
        assert "[回复<未知用户>：[无法获取被引用的消息]]，说：" in joined

    @pytest.mark.asyncio
    async def test_reply_detail_unavailable_uses_embedded_text(self) -> None:
        """转发内 reply 自带内嵌 text/qq 时，直接用于构建预览。"""
        handler = _build_handler()

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {
                        "type": "reply",
                        "data": {"id": 333, "qq": "999", "text": "内嵌被引用文本"},
                    },
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list, raw_message={})
        assert result is not None
        joined = "".join(_flatten_text(result))
        assert "内嵌被引用文本" in joined
        assert "[回复<999(999)>：" in joined


class TestForwardMessageDepthLimit:
    """测试转发消息最大递归层数可配置行为。"""

    def test_max_depth_config_limits_nesting(self) -> None:
        config = _build_config(forward_max_depth=2)
        assert config.features.forward_max_depth == 2

    @pytest.mark.asyncio
    async def test_deep_nested_forward_collapses_at_limit(self) -> None:
        handler = _build_handler(forward_max_depth=2)

        # 三层嵌套转发：层0 → 层1 → 层2，最内层为文本。
        # 配置深度为 2，层 2 的转发段应被折叠为占位符。
        forward_list = [
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [
                    {
                        "type": "forward",
                        "data": {
                            "content": [
                                {
                                    "sender": {"nickname": "Deep", "user_id": 4},
                                    "message": [
                                        {
                                            "type": "forward",
                                            "data": {
                                                "content": [
                                                    {
                                                        "sender": {"nickname": "Deepest", "user_id": 5},
                                                        "message": [
                                                            {
                                                                "type": "forward",
                                                                "data": {
                                                                    "content": [
                                                                        {
                                                                            "sender": {"nickname": "Inner", "user_id": 6},
                                                                            "message": [{"type": "text", "data": "最深层文本"}],
                                                                        },
                                                                    ],
                                                                },
                                                            },
                                                        ],
                                                    },
                                                ],
                                            },
                                        },
                                    ],
                                },
                            ],
                        },
                    },
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        texts = _flatten_text(result)
        joined = "".join(texts)
        assert "最深层文本" not in joined
        assert "【转发消息】" in joined

    @pytest.mark.asyncio
    async def test_within_depth_limit_nested_content_parsed(self) -> None:
        handler = _build_handler(forward_max_depth=3)

        forward_list = [
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [
                    {
                        "type": "forward",
                        "data": {
                            "content": [
                                {
                                    "sender": {"nickname": "Deep", "user_id": 4},
                                    "message": [{"type": "text", "data": "二级内容"}],
                                },
                            ],
                        },
                    },
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        joined = "".join(_flatten_text(result))
        assert "二级内容" in joined
        assert "合并转发消息内容：" in joined


class TestForwardMessageNestedFetch:
    """测试嵌套转发仅携带 id 时通过 API 递归获取。"""

    @pytest.mark.asyncio
    async def test_nested_forward_with_id_is_fetched_via_api(self, monkeypatch) -> None:
        handler = _build_handler()

        fetch_mock = AsyncMock(
            return_value=[
                {
                    "sender": {"nickname": "Remote", "user_id": 6},
                    "message": [{"type": "text", "data": "远程拉取内容"}],
                },
            ]
        )
        monkeypatch.setattr(mh_module, "get_forward_message", fetch_mock)

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [{"type": "forward", "data": {"id": "F123"}}],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        assert "远程拉取内容" in "".join(_flatten_text(result))
        fetch_mock.assert_awaited()


class TestForwardConfigAccessors:
    """测试配置访问辅助方法。"""

    def test_threshold_accessor_reads_config(self) -> None:
        handler = _build_handler(forward_image_threshold=2)
        assert handler._get_forward_image_threshold() == 2

    def test_depth_accessor_reads_config(self) -> None:
        handler = _build_handler(forward_max_depth=4)
        assert handler._get_forward_max_depth() == 4

    def test_accessors_fallback_to_defaults_without_plugin(self) -> None:
        class _AdapterWithoutPlugin:
            plugin = None

        handler = MessageHandler(cast(Any, _AdapterWithoutPlugin()))
        assert handler._get_forward_image_threshold() == 5
        assert handler._get_forward_max_depth() == 3


class TestForwardMultipleSegments:
    """测试转发单条消息内多个消息段都会按顺序被处理。"""

    @pytest.mark.asyncio
    async def test_text_and_at_inside_forward_are_kept(self) -> None:
        handler = _build_handler()

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {"type": "text", "data": "早上好"},
                    {"type": "at", "data": {"qq": "123"}},
                    {"type": "text", "data": "请看看"},
                ],
            },
        ]

        result = await handler.handle_forward_message(forward_list)
        assert result is not None
        joined = "".join(_flatten_text(result))
        assert "早上好" in joined
        assert "@123" in joined
        assert "请看看" in joined


class TestForwardConverterIntegration:
    """测试转发消息产出能通过核心 MessageConverter，不触发嵌套截断。"""

    @pytest.mark.asyncio
    async def test_forward_with_reply_and_nested_forward_converts_without_truncation(
        self, monkeypatch
    ) -> None:
        handler = _build_handler()

        async def _fake_get_message_detail(message_id):
            raise AssertionError("转发内 reply 不应调用 get_msg")

        monkeypatch.setattr(mh_module, "get_message_detail", _fake_get_message_detail)

        async def _fake_get_forward_message(segment, *, adapter=None):
            return [
                {
                    "sender": {"nickname": "Carol", "user_id": 3},
                    "message": [{"type": "forward", "data": {"id": "F9"}}],
                },
            ]
        monkeypatch.setattr(mh_module, "get_forward_message", _fake_get_forward_message)

        forward_list = [
            {
                "sender": {"nickname": "Alice", "user_id": 1},
                "message": [
                    {"type": "reply", "data": {"id": 111, "qq": "2", "text": "原始引用文本"}},
                    {"type": "text", "data": "看这个"},
                ],
            },
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [{"type": "forward", "data": {"id": "F1"}}],
            },
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [{"type": "forward", "data": {"id": "F2"}}],
            },
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [{"type": "forward", "data": {"id": "F3"}}],
            },
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [{"type": "forward", "data": {"id": "F4"}}],
            },
            {
                "sender": {"nickname": "Carol", "user_id": 3},
                "message": [{"type": "forward", "data": {"id": "F5"}}],
            },
        ]

        seg_result = await handler.handle_forward_message(forward_list, raw_message={})
        assert seg_result is not None

        envelope: MessageEnvelope = {
            "direction": "incoming",
            "message_info": {
                "platform": "qq",
                "message_id": "111",
                "user_info": {
                    "platform": "qq",
                    "role": UserRole.MEMBER,
                    "user_id": "1",
                },
            },
            "message_segment": [seg_result],  # type: ignore[typeddict-item]
        }

        converter = MessageConverter()
        message = await converter.envelope_to_message(envelope)

        plain_text = message.processed_plain_text or ""
        assert "[嵌套内容过深]" not in plain_text
        assert "原始引用文本" in plain_text
        assert "看这个" in plain_text
        assert message.reply_to is None