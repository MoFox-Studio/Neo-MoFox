"""message_api 模块测试。"""

from __future__ import annotations

import pytest

from src.app.plugin_system.api import message_api


@pytest.mark.asyncio
async def test_get_messages_by_time_in_chat_invalid_stream_id() -> None:
    """stream_id 为空时应抛出 ValueError。"""
    with pytest.raises(ValueError, match="stream_id 不能为空"):
        await message_api.get_messages_by_time_in_chat(
            stream_id="",
            start_time=1.0,
            end_time=2.0,
        )


@pytest.mark.asyncio
async def test_get_messages_by_time_in_chat_applies_filter_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开启 filter_bot 时应使用 Bot 消息过滤。"""

    async def fake_query_messages(**_: object) -> list[dict[str, object]]:
        return [
            {"sender_id": "bot", "time": 1.0},
            {"sender_id": "user1", "time": 2.0},
        ]

    async def fake_apply_filter_bot(
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        return [m for m in messages if m.get("sender_id") != "bot"]

    monkeypatch.setattr(message_api, "_query_messages", fake_query_messages)
    monkeypatch.setattr(message_api, "_apply_filter_bot", fake_apply_filter_bot)

    result = await message_api.get_messages_by_time_in_chat(
        stream_id="stream_1",
        start_time=1.0,
        end_time=3.0,
        filter_bot=True,
    )

    assert result == [{"sender_id": "user1", "time": 2.0}]


@pytest.mark.asyncio
async def test_is_command_message_uses_command_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命令识别应委托给 CommandManager。"""

    class _FakeCommandManager:
        def is_command(self, text: str) -> bool:
            return text.strip().startswith("/")

    monkeypatch.setattr(message_api, "_get_command_manager", lambda: _FakeCommandManager())

    assert message_api._is_command_message({"content": "/help"}) is True
    assert message_api._is_command_message({"content": "hello"}) is False


@pytest.mark.asyncio
async def test_rows_to_message_dicts_uses_new_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """消息映射应输出最新结构字段，不包含旧别名字段。"""

    async def fake_load_person_info_map(_: list[str]) -> dict[str, dict[str, object]]:
        return {
            "p1": {
                "person_id": "p1",
                "user_id": "u1",
                "nickname": "Alice",
                "cardname": "A",
            }
        }

    monkeypatch.setattr(message_api, "_load_person_info_map", fake_load_person_info_map)

    rows = [
        {
            "id": 1,
            "message_id": "m1",
            "time": 10.0,
            "stream_id": "s1",
            "person_id": "p1",
            "message_type": "text",
            "content": "hello",
            "processed_plain_text": "hello",
            "reply_to": None,
            "platform": "test",
        }
    ]

    result = await message_api._rows_to_message_dicts(rows)

    assert result[0]["stream_id"] == "s1"
    assert result[0]["sender_id"] == "u1"
    assert result[0]["sender_name"] == "Alice"
    assert "chat_id" not in result[0]
    assert "user_id" not in result[0]


@pytest.mark.asyncio
async def test_build_readable_messages_with_details_merge_and_absolute() -> None:
    """格式化函数应支持 absolute 时间与 merge。"""
    messages = [
        {"time": 1000.0, "sender_name": "Alice", "content": "第一条"},
        {"time": 1001.0, "sender_name": "Alice", "content": "第二条"},
    ]

    text, details = await message_api.build_readable_messages_with_details(
        messages=messages,
        merge_messages=True,
        timestamp_mode="absolute",
    )

    assert "Alice: 第一条 / 第二条" in text
    assert len(details) == 1


@pytest.mark.asyncio
async def test_get_person_ids_from_messages_returns_sorted_unique() -> None:
    """person_id 提取应去重并排序。"""
    messages = [
        {"person_id": "p2"},
        {"person_id": "p1"},
        {"person_id": "p2"},
        {},
    ]

    result = await message_api.get_person_ids_from_messages(messages)

    assert result == ["p1", "p2"]
