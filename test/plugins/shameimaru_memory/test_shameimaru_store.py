"""Shameimaru Memory 存储层与工具函数测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from plugins.shameimaru_memory.config import ShameimaruMemoryConfig
from plugins.shameimaru_memory.models import (
    GroupSummary,
    NewsEntry,
    PersonRef,
    SummaryEntry,
)
from plugins.shameimaru_memory.store import ShameimaruMemoryStore
from plugins.shameimaru_memory.sub_agent import extract_json_array
from plugins.shameimaru_memory.utils import (
    format_local_time,
    is_group_message,
    message_time,
    person_id_of,
    person_name_of,
)
from src.core.models.message import Message


def _config(tmp_path: Path) -> ShameimaruMemoryConfig:
    config = ShameimaruMemoryConfig()
    config.storage.data_dir = str(tmp_path)
    return config


def _message(
    *,
    platform: str = "qq",
    sender_id: str = "123",
    sender_name: str = "小明",
    text: str = "你好",
    chat_type: str = "group",
    time: float = 1000.0,
    sender_role: str | None = None,
) -> Message:
    return Message(
        message_id=f"msg-{sender_id}-{text}",
        time=time,
        processed_plain_text=text,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_role=sender_role,
        platform=platform,
        chat_type=chat_type,
    )


# ----------------------------------------------------------------------
# utils
# ----------------------------------------------------------------------


def test_person_id_of_builds_platform_prefixed_id() -> None:
    assert person_id_of(_message(platform="qq", sender_id="123")) == "qq:123"


def test_person_id_of_keeps_existing_prefixed_id() -> None:
    message = _message(platform="qq", sender_id="qq:123")
    assert person_id_of(message) == "qq:123"


def test_person_id_of_empty_when_missing_info() -> None:
    assert person_id_of(_message(platform="", sender_id="123")) == ""
    assert person_id_of(_message(platform="qq", sender_id="")) == ""


def test_person_name_of_falls_back_to_sender_id() -> None:
    message = _message(sender_name="", sender_id="999")
    assert person_name_of(message) == "999"


def test_message_time_and_group_detection() -> None:
    assert message_time(_message(time=42.0)) == 42.0
    assert is_group_message(_message(chat_type="group")) is True
    assert is_group_message(_message(chat_type="private")) is False


def test_format_local_time() -> None:
    formatted = format_local_time(0.0)
    assert isinstance(formatted, str) and len(formatted) == 5


# ----------------------------------------------------------------------
# models 序列化
# ----------------------------------------------------------------------


def test_models_roundtrip() -> None:
    summary = SummaryEntry(
        timestamp=1.0,
        content="第一段内容。\n\n第二段内容。",
        participants=[PersonRef(person_id="qq:1", name="A")],
    )
    restored = SummaryEntry.from_dict(summary.to_dict())
    assert restored is not None
    assert restored.content == summary.content
    assert restored.participants[0].person_id == "qq:1"

    news = NewsEntry(
        id="news-1",
        timestamp=2.0,
        title="标题",
        content="内容",
        participants=[PersonRef(person_id="qq:1", name="A")],
    )
    news_restored = NewsEntry.from_dict(news.to_dict())
    assert news_restored is not None
    assert news_restored.id == "news-1"
    assert news_restored.participants[0].person_id == "qq:1"

    group = GroupSummary(stream_id="s1", group_name="测试群", entries=[summary])
    group_restored = GroupSummary.from_dict(group.to_dict())
    assert group_restored is not None
    assert len(group_restored.entries) == 1
    assert group_restored.group_name == "测试群"


# ----------------------------------------------------------------------
# store
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_summary_cap_eviction(tmp_path: Path) -> None:
    store = ShameimaruMemoryStore(_config(tmp_path))
    for index in range(3):
        await store.append_summary(
            "s1",
            SummaryEntry(
                timestamp=float(index),
                content=f"摘要{index}",
                participants=[PersonRef(person_id="qq:1", name="A")],
            ),
            platform="qq",
            group_id="10001",
            group_name="测试群",
            max_entries=2,
        )

    group = await store.get_group_summary("s1")
    assert [entry.content for entry in group.entries] == ["摘要1", "摘要2"]
    assert group.group_name == "测试群"

    # 新 store 实例（模拟重启）仍能读取
    store2 = ShameimaruMemoryStore(_config(tmp_path))
    group2 = await store2.get_group_summary("s1")
    assert len(group2.entries) == 2


@pytest.mark.asyncio
async def test_store_deprecated_summaries_evicted_by_cap(tmp_path: Path) -> None:
    """废弃摘要不占用新摘要名额：总数超上限时按时间淘汰最旧（含废弃）。"""
    store = ShameimaruMemoryStore(_config(tmp_path))
    for index in range(3):
        await store.append_summary(
            "s1",
            SummaryEntry(timestamp=float(index), content=f"摘要{index}"),
            max_entries=3,
        )

    # 全部标记为废弃
    await store.deprecate_group_summaries("s1")
    group = await store.get_group_summary("s1")
    assert len(group.entries) == 3
    assert all(entry.deprecated for entry in group.entries)

    # 追加新摘要触发淘汰：最旧的废弃摘要（摘要0）被删除，新摘要保留
    await store.append_summary(
        "s1",
        SummaryEntry(timestamp=3.0, content="新摘要"),
        max_entries=3,
    )
    group = await store.get_group_summary("s1")
    assert [entry.content for entry in group.entries] == ["摘要1", "摘要2", "新摘要"]
    assert group.entries[-1].deprecated is False


@pytest.mark.asyncio
async def test_store_news_eviction_returns_evicted(tmp_path: Path) -> None:
    store = ShameimaruMemoryStore(_config(tmp_path))
    first = NewsEntry(id="n0", timestamp=0.0, title="t0", content="c0")
    evicted: list[NewsEntry] = []
    evicted.extend(await store.append_news(first, max_entries=2))
    evicted.extend(
        await store.append_news(
            NewsEntry(id="n1", timestamp=1.0, title="t1", content="c1"), max_entries=2
        )
    )
    evicted.extend(
        await store.append_news(
            NewsEntry(id="n2", timestamp=2.0, title="t2", content="c2"), max_entries=2
        )
    )
    assert [entry.id for entry in evicted] == ["n0"]
    assert [entry.id for entry in await store.get_news()] == ["n1", "n2"]


@pytest.mark.asyncio
async def test_store_remove_news_and_personas(tmp_path: Path) -> None:
    store = ShameimaruMemoryStore(_config(tmp_path))
    await store.append_news(
        NewsEntry(id="a", timestamp=1.0, title="t", content="c"), max_entries=10
    )
    await store.append_news(
        NewsEntry(id="b", timestamp=2.0, title="t2", content="c2"), max_entries=10
    )
    removed = await store.remove_news(["a", "missing"])
    assert [entry.id for entry in removed] == ["a"]
    assert [entry.id for entry in await store.get_news()] == ["b"]

    assert await store.get_persona("qq:1") == ""
    await store.set_persona("qq:1", "他喜欢摄影。")
    assert await store.get_persona("qq:1") == "他喜欢摄影。"
    assert await store.get_all_personas() == {"qq:1": "他喜欢摄影。"}


@pytest.mark.asyncio
async def test_store_ensure_group_registers_once(tmp_path: Path) -> None:
    store = ShameimaruMemoryStore(_config(tmp_path))
    assert await store.ensure_group("s1", platform="qq", group_name="测试群") is True
    assert await store.ensure_group("s1", platform="qq", group_name="测试群") is False

    group = await store.get_group_summary("s1")
    assert group.stream_id == "s1"
    assert group.platform == "qq"
    assert group.group_name == "测试群"
    assert group.entries == []

    # 新实例（模拟重启）仍能读取注册记录
    store2 = ShameimaruMemoryStore(_config(tmp_path))
    assert await store2.ensure_group("s1") is False


@pytest.mark.asyncio
async def test_store_news_eviction_by_timestamp_not_file_order(tmp_path: Path) -> None:
    """淘汰按时间升序取最旧，与文件写入顺序无关。"""
    store = ShameimaruMemoryStore(_config(tmp_path))
    # 故意先写时间较新的，再写时间较旧的
    await store.append_news(
        NewsEntry(id="new", timestamp=99.0, title="t-new", content="c-new"),
        max_entries=2,
    )
    await store.append_news(
        NewsEntry(id="old", timestamp=1.0, title="t-old", content="c-old"),
        max_entries=2,
    )
    # 第三条触发淘汰：应淘汰时间最旧的 old，而不是文件顺序靠前的 new
    evicted = await store.append_news(
        NewsEntry(id="mid", timestamp=50.0, title="t-mid", content="c-mid"),
        max_entries=2,
    )
    assert [entry.id for entry in evicted] == ["old"]
    assert [entry.id for entry in await store.get_news()] == ["mid", "new"]


@pytest.mark.asyncio
async def test_store_watcher_reloads_memory_on_external_change(tmp_path: Path) -> None:
    """外部修改本地文件后，内存缓存应自动同步为文件内容。"""
    import json

    store = ShameimaruMemoryStore(_config(tmp_path), watch_interval=0.05)
    await store.set_persona("qq:1", "旧文本")
    assert await store.get_persona("qq:1") == "旧文本"

    await store.start_watcher()
    try:
        # 外部进程直接改写文件（模拟 WebUI/其它进程修改）
        store.personas_path.write_text(
            json.dumps({"version": 1, "persons": {"qq:1": "外部改写"}}),
            encoding="utf-8",
        )
        for _ in range(100):
            await asyncio.sleep(0.05)
            if (await store.get_persona("qq:1")) == "外部改写":
                break
        assert await store.get_persona("qq:1") == "外部改写"
    finally:
        await store.close()


async def test_collect_participants_skips_bot_messages() -> None:
    """bot 自身消息不应进入参与人物清单。"""
    from plugins.shameimaru_memory.job import _collect_participants

    messages = [
        _message(sender_id="1", sender_name="小明", text="你好"),
        _message(
            sender_id="bot-id", sender_name="小狐狸", text="我在",
            sender_role="bot",
        ),
        _message(sender_id="2", sender_name="小红", text="在吗"),
    ]
    refs = _collect_participants(messages)
    assert [ref.person_id for ref in refs] == ["qq:1", "qq:2"]


def test_build_chat_flow_excludes_bot_messages() -> None:
    """摘要聊天记录应排除 bot 自身消息，防止记忆以 Bot 为主角。"""
    from plugins.shameimaru_memory.job import _build_chat_flow

    messages = [
        _message(sender_id="1", sender_name="小明", text="今天去爬山了"),
        _message(
            sender_id="bot-id", sender_name="小狐狸", text="下次一起啊",
            sender_role="bot",
        ),
        _message(sender_id="2", sender_name="小红", text="好啊"),
    ]
    flow = _build_chat_flow(messages)
    assert "小明: 今天去爬山了" in flow
    assert "小红: 好啊" in flow
    assert "下次一起啊" not in flow


# ----------------------------------------------------------------------
# sub_agent 解析
# ----------------------------------------------------------------------


def test_extract_json_array_plain() -> None:
    assert extract_json_array('[{"title": "a"}]') == [{"title": "a"}]


def test_extract_json_array_with_fence() -> None:
    text = '```json\n[{"title": "a"}, {"title": "b"}]\n```'
    assert extract_json_array(text) == [{"title": "a"}, {"title": "b"}]


def test_extract_json_array_embedded() -> None:
    text = '解释一下。\n[{"title": "a"}]\n以上。'
    assert extract_json_array(text) == [{"title": "a"}]


def test_extract_json_array_invalid_returns_empty() -> None:
    assert extract_json_array("没有任何值得整理的内容") == []
    assert extract_json_array("") == []
    assert extract_json_array("[]") == []
