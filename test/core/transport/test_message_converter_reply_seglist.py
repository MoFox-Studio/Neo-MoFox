"""MessageConverter 对 reply seglist 解析的单元测试。

覆盖场景：
- 适配器在 seglist 前置 reply 段时，``Message.reply_to`` 应被正确解析。
- 引用内容中的媒体不应污染当前消息的 ``media``（media 隔离逻辑）。
- 兼容旧的纯文本前缀格式（``[回复<`` / ``「回复：``）。
- 直接 reply 段（非 seglist）仍能解析 ``reply_to``。
"""

from __future__ import annotations

import pytest

from src.core.transport.message_receive.converter import MessageConverter


def _make_envelope(segments: list[dict]) -> dict:
    """构造一个最小可用的 MessageEnvelope。"""
    return {
        "direction": "incoming",
        "message_info": {
            "message_id": "m1",
            "platform": "qq",
            "time": 1700000000.0,
            "user_info": {
                "platform": "qq",
                "user_id": "u1",
                "user_nickname": "Alice",
            },
        },
        "message_segment": segments,
    }


@pytest.mark.asyncio
async def test_seglist_with_reply_segment_extracts_reply_to() -> None:
    """seglist 内含 reply 段时，应解析出 reply_to。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {
            "type": "seglist",
            "data": [
                {"type": "reply", "data": "msg_abc"},
                {"type": "text", "data": "[回复<Alice(123)>："},
                {"type": "text", "data": "你好"},
                {"type": "text", "data": "]，说："},
            ],
        },
        {"type": "text", "data": "我同意"},
    ])

    message = await converter.envelope_to_message(envelope)

    assert message.reply_to == "msg_abc"
    # 文本应包含引用预览与后续正文
    assert "我同意" in (message.processed_plain_text or "")
    assert "Alice" in (message.processed_plain_text or "")


@pytest.mark.asyncio
async def test_seglist_with_reply_segment_isolates_media() -> None:
    """seglist 内含 reply 段时，被引用消息中的图片不应进入当前消息 media。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {
            "type": "seglist",
            "data": [
                {"type": "reply", "data": "msg_abc"},
                {"type": "text", "data": "[回复<Bob(456)>："},
                {"type": "image", "data": "aGVsbG8="},  # 被引用消息中的图片
                {"type": "text", "data": "]，说："},
            ],
        },
        {"type": "text", "data": "正文内容"},
    ])

    message = await converter.envelope_to_message(envelope)

    assert message.reply_to == "msg_abc"
    # 引用内容中的图片不应被当作当前消息的媒体
    assert message.media == []


@pytest.mark.asyncio
async def test_seglist_legacy_prefix_still_isolates_media() -> None:
    """无 reply 段但首段文本以 [回复< 开头时，仍应隔离 media（兼容旧格式）。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {
            "type": "seglist",
            "data": [
                {"type": "text", "data": "[回复<Carol(789)>："},
                {"type": "image", "data": "aGVsbG8="},
                {"type": "text", "data": "]，说："},
            ],
        },
        {"type": "text", "data": "正文"},
    ])

    message = await converter.envelope_to_message(envelope)

    # 旧格式没有 reply 段，reply_to 应为 None
    assert message.reply_to is None
    # 但 media 隔离仍应生效
    assert message.media == []


@pytest.mark.asyncio
async def test_seglist_legacy_prefix_alt_format_isolates_media() -> None:
    """旧格式「回复：前缀同样应隔离 media。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {
            "type": "seglist",
            "data": [
                {"type": "text", "data": "「回复：hi」"},
                {"type": "image", "data": "aGVsbG8="},
            ],
        },
    ])

    message = await converter.envelope_to_message(envelope)

    assert message.reply_to is None
    assert message.media == []


@pytest.mark.asyncio
async def test_direct_reply_segment_extracts_reply_to() -> None:
    """顶层直接 reply 段（非 seglist）应解析出 reply_to。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {"type": "reply", "data": "msg_xyz"},
        {"type": "text", "data": "hello"},
    ])

    message = await converter.envelope_to_message(envelope)

    assert message.reply_to == "msg_xyz"


@pytest.mark.asyncio
async def test_seglist_without_reply_merges_media() -> None:
    """普通 seglist（非 reply 引用内容）应正常合并 media。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {
            "type": "seglist",
            "data": [
                {"type": "text", "data": "前缀"},
                {"type": "image", "data": "aGVsbG8="},
            ],
        },
    ])

    message = await converter.envelope_to_message(envelope)

    # 非引用 seglist，media 应被合并
    assert message.reply_to is None
    assert len(message.media) == 1
    assert message.media[0]["type"] == "image"


@pytest.mark.asyncio
async def test_reply_seglist_followed_by_at_preserves_at_users() -> None:
    """reply seglist 内的 at 段应被合并到当前消息 at_users。"""
    converter = MessageConverter()
    envelope = _make_envelope([
        {
            "type": "seglist",
            "data": [
                {"type": "reply", "data": "msg_abc"},
                {"type": "text", "data": "[回复<Dave(111)>："},
                {"type": "at", "data": "Eve:222"},
                {"type": "text", "data": "]，说："},
            ],
        },
        {"type": "text", "data": "正文"},
    ])

    message = await converter.envelope_to_message(envelope)

    assert message.reply_to == "msg_abc"
    # seglist 内的 at 应被合并
    assert any(u.get("user_id") == "222" for u in message.at_users)
