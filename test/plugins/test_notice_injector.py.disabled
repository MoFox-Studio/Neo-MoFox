"""notice_injector 插件测试。

覆盖：
- NoticeStore 原子性消费与 stream_id 分桶隔离
- NoticeCollector.execute 存储逻辑与非 notice envelope 忽略
- _prompt_build_handler prompt 注入与消费后清空
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.notice_injector.store import NoticeStore


# ─── 每个测试使用独立 store 实例，不污染全局单例 ───────────────────────────

@pytest.fixture(autouse=True)
def reset_store() -> None:
    """测试前后重置 NoticeStore 单例，防止用例间状态泄漏。"""
    NoticeStore._instance = None
    yield
    NoticeStore._instance = None


# ═══════════════════════════════════════════════════════════
# NoticeStore
# ═══════════════════════════════════════════════════════════


def test_store_singleton() -> None:
    """get_instance 应始终返回同一对象。"""
    a = NoticeStore.get_instance()
    b = NoticeStore.get_instance()
    assert a is b


def test_store_append_and_pop_all() -> None:
    """append 后 pop_all 应返回全部条目并清空。"""
    store = NoticeStore.get_instance()
    store.append("stream_A", {"text": "戳了戳你", "notice_type": "poke"})
    store.append("stream_A", {"text": "上传了文件", "notice_type": "group_upload"})

    result = store.pop_all("stream_A")

    assert len(result) == 2
    assert result[0]["notice_type"] == "poke"
    assert result[1]["notice_type"] == "group_upload"


def test_store_pop_all_clears_entries() -> None:
    """pop_all 之后再次 pop_all 应返回空列表（原子性消费）。"""
    store = NoticeStore.get_instance()
    store.append("stream_A", {"text": "某条 notice", "notice_type": "poke"})
    store.pop_all("stream_A")

    assert store.pop_all("stream_A") == []


def test_store_pop_all_missing_stream_returns_empty() -> None:
    """对不存在的 stream_id 执行 pop_all 应返回空列表，不抛异常。"""
    store = NoticeStore.get_instance()
    assert store.pop_all("nonexistent") == []


def test_store_stream_isolation() -> None:
    """不同 stream_id 的 notice 互不干扰。"""
    store = NoticeStore.get_instance()
    store.append("stream_A", {"text": "A 的 notice", "notice_type": "poke"})
    store.append("stream_B", {"text": "B 的 notice", "notice_type": "group_upload"})

    result_a = store.pop_all("stream_A")
    result_b = store.pop_all("stream_B")

    assert len(result_a) == 1 and result_a[0]["text"] == "A 的 notice"
    assert len(result_b) == 1 and result_b[0]["text"] == "B 的 notice"


# ═══════════════════════════════════════════════════════════
# NoticeCollector
# ═══════════════════════════════════════════════════════════


def _make_collector() -> Any:
    """创建 NoticeCollector 实例（绕过 BasePlugin 依赖）。"""
    from plugins.notice_injector.event_handler import NoticeCollector

    mock_plugin = MagicMock()
    return NoticeCollector(plugin=mock_plugin)


def _make_notice_kwargs(
    stream_id_override: str | None = None,
    notice_type: str = "poke",
    text: str = "某人戳了戳你",
    is_notice: bool = True,
    message_type: str = "notice",
) -> dict[str, Any]:
    """构造 ON_RECEIVED_OTHER_MESSAGE 事件的 kwargs。"""
    if stream_id_override:
        group_info = {"group_id": "fake_id", "platform": "qq", "group_name": "G"}
    else:
        group_info = {"group_id": "group123", "platform": "qq", "group_name": "Test"}

    return {
        "raw": {
            "message_info": {
                "platform": "qq",
                "message_type": message_type,
                "message_id": "notice",
                "group_info": group_info,
                "user_info": {"user_id": "u1", "user_nickname": "测试用户"},
                "extra": {
                    "is_notice": is_notice,
                    "notice_type": notice_type,
                    "text_description": text,
                },
            }
        },
        "processed": "",
    }


@pytest.mark.asyncio
async def test_collector_stores_notice() -> None:
    """execute 应将 notice 条目存入 NoticeStore。"""
    with patch(
        "plugins.notice_injector.event_handler.extract_stream_id",
        return_value="fake_stream_id",
    ):
        collector = _make_collector()
        kwargs = _make_notice_kwargs(text="某人戳了戳你", notice_type="poke")
        decision, result_params = await collector.execute("", kwargs)

    from src.kernel.event import EventDecision
    assert decision is EventDecision.SUCCESS
    assert result_params is kwargs

    entries = NoticeStore.get_instance().pop_all("fake_stream_id")
    assert len(entries) == 1
    assert entries[0]["text"] == "某人戳了戳你"
    assert entries[0]["notice_type"] == "poke"


@pytest.mark.asyncio
async def test_collector_does_not_fill_processed() -> None:
    """execute 不应填充 processed 字段，notice 不进入普通消息流程。"""
    with patch(
        "plugins.notice_injector.event_handler.extract_stream_id",
        return_value="sid",
    ):
        collector = _make_collector()
        kwargs = _make_notice_kwargs()
        await collector.execute("", kwargs)

    # processed 应保持原样（空字符串），不由 NoticeCollector 修改
    assert kwargs["processed"] == ""


@pytest.mark.asyncio
async def test_collector_ignores_non_notice_message_type() -> None:
    """message_type 不是 notice 的 envelope 应被忽略，不存入 store。"""
    with patch(
        "plugins.notice_injector.event_handler.extract_stream_id",
        return_value="sid",
    ):
        collector = _make_collector()
        kwargs = _make_notice_kwargs(message_type="message")
        await collector.execute("", kwargs)

    assert NoticeStore.get_instance().pop_all("sid") == []


@pytest.mark.asyncio
async def test_collector_ignores_non_is_notice_flag() -> None:
    """is_notice 为 False 的 envelope 应被忽略。"""
    with patch(
        "plugins.notice_injector.event_handler.extract_stream_id",
        return_value="sid",
    ):
        collector = _make_collector()
        kwargs = _make_notice_kwargs(is_notice=False)
        await collector.execute("", kwargs)

    assert NoticeStore.get_instance().pop_all("sid") == []


@pytest.mark.asyncio
async def test_collector_ignores_empty_text_description() -> None:
    """text_description 为空时不存入 store。"""
    with patch(
        "plugins.notice_injector.event_handler.extract_stream_id",
        return_value="sid",
    ):
        collector = _make_collector()
        kwargs = _make_notice_kwargs(text="")
        await collector.execute("", kwargs)

    assert NoticeStore.get_instance().pop_all("sid") == []


# ═══════════════════════════════════════════════════════════
# _prompt_build_handler
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_prompt_handler_injects_notice() -> None:
    """有积累 notice 时应注入 extra 并消费记录。"""
    from plugins.notice_injector.plugin import _prompt_build_handler
    from src.kernel.event import EventDecision

    store = NoticeStore.get_instance()
    store.append("sid_x", {"text": "某人戳了戳你", "notice_type": "poke"})

    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "sid_x"},
        "policies": {},
        "strict": False,
    }

    decision, result = await _prompt_build_handler("on_prompt_build", params)

    assert decision == EventDecision.SUCCESS
    assert "近期群内动态" in result["values"]["extra"]
    assert "某人戳了戳你" in result["values"]["extra"]


@pytest.mark.asyncio
async def test_prompt_handler_clears_after_consume() -> None:
    """消费后 store 应清空，二次调用不重复注入。"""
    from plugins.notice_injector.plugin import _prompt_build_handler

    store = NoticeStore.get_instance()
    store.append("sid_y", {"text": "上传了文件", "notice_type": "group_upload"})

    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "sid_y"},
        "policies": {},
        "strict": False,
    }

    await _prompt_build_handler("on_prompt_build", params)

    # 第二次调用，store 已清空
    params2: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "sid_y"},
        "policies": {},
        "strict": False,
    }
    _, result2 = await _prompt_build_handler("on_prompt_build", params2)
    assert result2["values"]["extra"] == ""


@pytest.mark.asyncio
async def test_prompt_handler_stream_isolation() -> None:
    """应仅注入当前 stream_id 的 notice，不跨会话混入。"""
    from plugins.notice_injector.plugin import _prompt_build_handler

    store = NoticeStore.get_instance()
    store.append("sid_a", {"text": "A 群的事", "notice_type": "poke"})
    store.append("sid_b", {"text": "B 群的事", "notice_type": "group_ban"})

    params_a: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "sid_a"},
        "policies": {},
        "strict": False,
    }
    _, result_a = await _prompt_build_handler("on_prompt_build", params_a)

    assert "A 群的事" in result_a["values"]["extra"]
    assert "B 群的事" not in result_a["values"]["extra"]
    # B 群数据应仍然完好
    assert store.pop_all("sid_b")[0]["text"] == "B 群的事"


@pytest.mark.asyncio
async def test_prompt_handler_skips_other_templates() -> None:
    """非 default_chatter_user_prompt 模板不应触发注入。"""
    from plugins.notice_injector.plugin import _prompt_build_handler

    store = NoticeStore.get_instance()
    store.append("sid_c", {"text": "某条 notice", "notice_type": "poke"})

    params: dict[str, Any] = {
        "name": "some_other_prompt",
        "template": "{extra}",
        "values": {"extra": "", "stream_id": "sid_c"},
        "policies": {},
        "strict": False,
    }
    _, result = await _prompt_build_handler("on_prompt_build", params)

    assert result["values"]["extra"] == ""
    # store 中的记录应未被消费
    assert len(store.pop_all("sid_c")) == 1


@pytest.mark.asyncio
async def test_prompt_handler_appends_to_existing_extra() -> None:
    """已有 extra 内容时应追加，而非覆盖。"""
    from plugins.notice_injector.plugin import _prompt_build_handler

    store = NoticeStore.get_instance()
    store.append("sid_d", {"text": "戳了戳你", "notice_type": "poke"})

    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": "行为提醒：不要骂人", "stream_id": "sid_d"},
        "policies": {},
        "strict": False,
    }
    _, result = await _prompt_build_handler("on_prompt_build", params)

    extra = result["values"]["extra"]
    assert "行为提醒：不要骂人" in extra
    assert "戳了戳你" in extra
