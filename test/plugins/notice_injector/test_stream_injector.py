"""notice_injector 流 reminder 注入处理函数的单元测试。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from plugins.notice_injector.config import NoticeInjectorConfig
from plugins.notice_injector.handlers.stream_injector import (
    inject_notice_to_stream_reminder,
    set_config,
)
from src.core.models.stream import ChatStream
from src.core.prompt import get_system_reminder_store, reset_system_reminder_store


@pytest.fixture(autouse=True)
def _isolated_store_and_config() -> None:
    """隔离 system reminder 全局状态与模块级配置。"""
    reset_system_reminder_store()
    set_config(NoticeInjectorConfig())
    yield
    reset_system_reminder_store()
    set_config(None)


def _group_bucket() -> str:
    """群聊 10086 对应的流私有 bucket key。"""
    stream_id = ChatStream.generate_stream_id(platform="qq", group_id="10086")
    return f"stream:{stream_id}:actor"


def _private_bucket(user_id: str = "30001") -> str:
    """私聊 user_id 对应的流私有 bucket。"""
    stream_id = ChatStream.generate_stream_id(platform="qq", user_id=user_id)
    return f"stream:{stream_id}:actor"


def _group_envelope(text_description: str = "张三戳了戳机器人") -> dict[str, Any]:
    """构造群聊 notice envelope。"""
    return {
        "timestamp_ms": 1786900000000,
        "message_info": {
            "platform": "qq",
            "message_id": "notice_test_1",
            "group_info": {"platform": "qq", "group_id": "10086", "name": "测试群"},
            "user_info": {"platform": "qq", "user_id": "20001", "user_nickname": "张三"},
            "extra": {
                "notice_type": "poke",
                "text_description": text_description,
            },
        },
        "message_segment": {"type": "text", "data": text_description},
    }


def test_group_notice_injected_into_stream_private_bucket() -> None:
    """群聊 notice 应注入到 stream:{stream_id}:actor 的流私有 bucket。"""
    envelope = _group_envelope()

    result = asyncio.run(inject_notice_to_stream_reminder("poke", envelope))

    assert result is None  # 默认放行分发，不拦截通知
    store = get_system_reminder_store()
    content = store.get(_group_bucket())
    assert "系统通知[poke]: 张三戳了戳机器人" in content
    assert "notice_poke_1786900000000" in content


def test_private_notice_uses_user_stream() -> None:
    """私聊 notice（无 group_info）应按 user_id 生成 stream_id。"""
    envelope: dict[str, Any] = {
        "timestamp_ms": 1786900000001,
        "message_info": {
            "platform": "qq",
            "message_id": "notice_test_2",
            "user_info": {"platform": "qq", "user_id": "30001", "user_nickname": "李四"},
            "extra": {
                "notice_type": "friend_recall",
                "text_description": "李四撤回了一条消息",
            },
        },
        "message_segment": {"type": "text", "data": "李四撤回了一条消息"},
    }

    result = asyncio.run(inject_notice_to_stream_reminder("friend_recall", envelope))

    assert result is None
    store = get_system_reminder_store()
    assert "系统通知[friend_recall]: 李四撤回了一条消息" in store.get(_private_bucket())


def test_text_fallback_to_message_segment() -> None:
    """extra.text_description 缺失时应回退到首个 text 消息段。"""
    envelope = _group_envelope()
    envelope["message_info"]["extra"] = {"notice_type": "poke"}

    result = asyncio.run(inject_notice_to_stream_reminder("poke", envelope))

    assert result is None
    store = get_system_reminder_store()
    assert "系统通知[poke]: 张三戳了戳机器人" in store.get(_group_bucket())


def test_missing_text_skips_injection() -> None:
    """无可读文本时不注入任何 reminder。"""
    envelope = _group_envelope()
    envelope["message_info"]["extra"] = {"notice_type": "poke"}
    envelope["message_segment"] = {"type": "notify", "data": {}}

    result = asyncio.run(inject_notice_to_stream_reminder("poke", envelope))

    assert result is None
    store = get_system_reminder_store()
    assert store.get(_group_bucket()) == ""


def test_forever_consume_overwrites_per_type() -> None:
    """consume=forever 时按类型命名并覆盖更新，insert_type 为 fixed。"""
    from src.core.prompt import SystemReminderInsertType

    config = NoticeInjectorConfig()
    config.plugin.consume = "forever"
    set_config(config)

    result = asyncio.run(inject_notice_to_stream_reminder("poke", _group_envelope()))
    assert result is None
    result = asyncio.run(inject_notice_to_stream_reminder("poke", _group_envelope()))
    assert result is None

    store = get_system_reminder_store()
    items = store.get_items(_group_bucket())
    assert len(items) == 1  # 覆盖式更新，不累积
    item = items[0]
    assert item.name == "notice_poke"
    assert item.insert_type == SystemReminderInsertType.FIXED


def test_long_text_truncated() -> None:
    """超长文本按 max_text_length 截断。"""
    config = NoticeInjectorConfig()
    config.plugin.max_text_length = 10
    set_config(config)

    result = asyncio.run(
        inject_notice_to_stream_reminder("poke", _group_envelope("很长" * 50))
    )

    assert result is None
    store = get_system_reminder_store()
    content = store.get(_group_bucket())
    assert content.endswith("...")
    assert len(content) < 100


def test_pass_to_core_returns_text() -> None:
    """pass_to_core 开启时返回 (True, text) 交给核心消息流程。"""
    config = NoticeInjectorConfig()
    config.plugin.pass_to_core = True
    set_config(config)

    result = asyncio.run(inject_notice_to_stream_reminder("poke", _group_envelope()))

    assert result == (True, "张三戳了戳机器人")


def test_disabled_plugin_skips_injection() -> None:
    """插件禁用时不注入 reminder。"""
    config = NoticeInjectorConfig()
    config.plugin.enabled = False
    set_config(config)

    result = asyncio.run(inject_notice_to_stream_reminder("poke", _group_envelope()))

    assert result is None
    store = get_system_reminder_store()
    assert store.get(_group_bucket()) == ""
