"""聊天日记提醒在 LLM payload 中的替换与流隔离测试。"""

from __future__ import annotations

from typing import cast

import pytest

from src.app.plugin_system.api import llm_api
from src.app.plugin_system.base import BasePlugin
from src.core.prompt import SystemReminderStore
from src.kernel.llm import (
    LLMContextManager,
    LLMPayload,
    LLMRequest,
    ModelSet,
    ReminderSourceSpec,
)
from src.kernel.llm.payload.content import Text
from src.kernel.llm.roles import ROLE

from ..diary.events import ChatDiaryEventHandler
from ..diary.injection import REMINDER_NAME, refresh_diary_payloads
from ..diary.runtime import DiaryRuntime


class _DiaryRuntime(DiaryRuntime):
    """供事件测试使用的内存 runtime。"""

    ready = True

    def __init__(self, content: str = "") -> None:
        """保留真实提醒生命周期，日记读取和调度仅在内存模拟。"""
        self.content = content
        self.read_streams: list[str] = []
        self.observed: list[str] = []
        self.started = 0
        self._closed = False
        self._reminders: set[str] = set()

    def start(self) -> None:
        """记录启动调用。"""
        self.started += 1

    def observe_stream(self, stream_id: str) -> None:
        """记录被唤醒的流。"""
        self.observed.append(stream_id)

    async def reminder_content(self, stream_id: str) -> str:
        """返回测试日记并记录读取的聊天流。"""
        self.read_streams.append(stream_id)
        return self.content


class _RuntimeOwner:
    """持有内存日记 runtime。"""

    def __init__(self, runtime: _DiaryRuntime) -> None:
        """绑定测试日记运行时。"""
        self.diary = runtime


class _Plugin:
    """提供事件处理器所需的 owner。"""

    def __init__(self, runtime: _DiaryRuntime) -> None:
        """建立事件侧最小 Owner。"""
        self.runtime_owner = _RuntimeOwner(runtime)


def _new_request(stream_id: str, store: SystemReminderStore) -> LLMRequest:
    """创建真实聊天请求，并以 in-memory reminder store 注入 Actor 提醒。"""
    request = llm_api.create_llm_request(
        cast(ModelSet, []),
        request_name="test_chat",
        with_reminder="actor",
        stream_id=stream_id,
    )
    request.context_manager = LLMContextManager(
        reminder_sources=[
            ReminderSourceSpec(
                bucket=f"stream:{stream_id}:actor",
                wrap_with_system_tag=True,
            ),
        ],
    )
    return request


def _text_parts(payload: LLMPayload) -> list[str]:
    """返回 payload 中的 Text 内容。"""
    return [part.text for part in payload.content if isinstance(part, Text)]


@pytest.fixture
def reminder_store(monkeypatch: pytest.MonkeyPatch) -> SystemReminderStore:
    """安装不连接生产存储的真实内存 reminder store。"""
    from src.app.plugin_system.api import prompt_api
    from src.core import prompt

    store = SystemReminderStore()
    monkeypatch.setattr(prompt_api, "_get_system_reminder_store", lambda: store)
    monkeypatch.setattr(prompt, "get_system_reminder_store", lambda: store)
    return store


def test_real_context_manager_replaces_old_draft_without_touching_other_reminders(
    reminder_store: SystemReminderStore,
) -> None:
    """旧 reminder 被新 manager 重新注入后，刷新只替换日记块。"""
    stream_id = "stream-a"
    reminder_store.set(
        f"stream:{stream_id}:actor",
        REMINDER_NAME,
        "旧稿",
        insert_type="dynamic",
    )
    reminder_store.set(
        f"stream:{stream_id}:actor",
        "another_reminder",
        "保留提醒",
        insert_type="dynamic",
    )
    request = _new_request(stream_id, reminder_store)
    request.add_payload(LLMPayload(ROLE.USER, Text("第一条用户消息")))
    request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("回复")))
    request.add_payload(LLMPayload(ROLE.USER, Text("最新用户消息")))

    assert refresh_diary_payloads(request.payloads, "新稿")
    assert refresh_diary_payloads(request.payloads, "再次更新")
    flattened = [text for payload in request.payloads for text in _text_parts(payload)]
    assert sum(f"[{REMINDER_NAME}]" in text for text in flattened) == 1
    assert any("再次更新" in text for text in flattened)
    assert any("another_reminder" in text for text in flattened)
    assert "第一条用户消息" in flattened
    assert "最新用户消息" in flattened


def test_diary_stays_at_tail_while_flashback_remains_in_history(
    reminder_store: SystemReminderStore,
) -> None:
    """跨轮和延续历史的请求只在末尾放最新日记，固定闪回仍留在前面的 User 中。"""
    from src.app.plugin_system.api import prompt_api

    from ..vnext.flashback_service import FlashbackCandidate
    from ..vnext.runtime_components import VNextFlashbackEventHandler

    stream_id = "stream-a"
    candidate = FlashbackCandidate("memory-example", "旧安排", "时间还没定。", "安排")
    name = f"engram_memory_flashback_{candidate.memory_id}"
    handler = VNextFlashbackEventHandler(cast(BasePlugin, _Plugin(_DiaryRuntime())))
    handler._upsert_stream_reminder(stream_id, name, candidate)
    items = reminder_store.get_items(f"stream:{stream_id}:actor", names=[name])
    assert len(items) == 1
    assert items[0].insert_type is prompt_api.SystemReminderInsertType.FIXED
    assert items[0].consume_type is prompt_api.SystemReminderConsumeType.FOREVER

    reminder_store.set(
        f"stream:{stream_id}:actor", REMINDER_NAME, "第一版日记", insert_type="dynamic"
    )
    request = _new_request(stream_id, reminder_store)
    request.add_payload(LLMPayload(ROLE.USER, Text("第一轮")))
    request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("第一轮回复")))
    reminder_store.set(
        f"stream:{stream_id}:actor", REMINDER_NAME, "第二版日记", insert_type="dynamic"
    )
    request.add_payload(LLMPayload(ROLE.USER, Text("第二轮")))
    assert refresh_diary_payloads(request.payloads, "第二版日记")
    assert any(name in text for text in _text_parts(request.payloads[0]))
    assert not any(REMINDER_NAME in text for text in _text_parts(request.payloads[0]))
    assert any("第二版日记" in text for text in _text_parts(request.payloads[-1]))

    reminder_store.set(
        f"stream:{stream_id}:actor", REMINDER_NAME, "第三版日记", insert_type="dynamic"
    )
    resumed = _new_request(stream_id, reminder_store)
    for payload in request.payloads:
        resumed.add_payload(payload)
    resumed.add_payload(LLMPayload(ROLE.ASSISTANT, Text("第二轮回复")))
    resumed.add_payload(LLMPayload(ROLE.USER, Text("第三轮")))
    assert refresh_diary_payloads(resumed.payloads, "第三版日记")
    flattened = [text for payload in resumed.payloads for text in _text_parts(payload)]
    assert sum(f"[{name}]" in text for text in flattened) == 1
    assert sum(f"[{REMINDER_NAME}]" in text for text in flattened) == 1
    assert any(
        candidate.to_prompt_block() in text for text in _text_parts(resumed.payloads[0])
    )
    assert all(
        REMINDER_NAME not in text
        for payload in resumed.payloads[:-1]
        for text in _text_parts(payload)
    )
    assert any("第三版日记" in text for text in _text_parts(resumed.payloads[-1]))
    assert not any(name in text for text in _text_parts(resumed.payloads[-1]))
    assert not any("第一版日记" in text or "第二版日记" in text for text in flattened)


@pytest.mark.parametrize("preload", [False, True])
@pytest.mark.asyncio
async def test_private_persona_is_fixed_in_first_user_and_refreshes_without_duplicates(
    reminder_store: SystemReminderStore,
    monkeypatch: pytest.MonkeyPatch,
    preload: bool,
) -> None:
    """私聊固定首 User，跨轮更新唯一正文，并与末尾指引和日记互不干扰。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.app.plugin_system.api import prompt_api
    from src.app.plugin_system.types import EventType

    from .. import plugin as plugin_module
    from ..config import EngramMemoryConfig
    from ..prompts import MEMORY_GUIDE_REMINDER
    from ..vnext import persona_injection

    stream_id = "stream-private"
    plugin = plugin_module.EngramMemoryPlugin(EngramMemoryConfig())
    persona = SimpleNamespace(
        person_id="person-example",
        impression_text="他希望我叫他小树。①",
        is_current=True,
    )
    read_persona = AsyncMock(return_value=persona)
    plugin.runtime_owner = cast(
        plugin_module.VNextRuntimeOwner,
        SimpleNamespace(
            persona_service=SimpleNamespace(get_persona=read_persona),
        ),
    )
    get_stream = AsyncMock(
        return_value={"chat_type": "private", "person_id": "person-example"}
    )
    monkeypatch.setattr(persona_injection.stream_api, "get_stream_info", get_stream)
    handler = persona_injection.VNextPrivatePersonaEventHandler(plugin)
    if preload:
        await handler.execute(EventType.ON_CHATTER_STEP, {"stream_id": stream_id})
        read_persona.assert_awaited_once_with("person-example")
    else:
        read_persona.assert_not_awaited()
    reminder_store.set(
        f"stream:{stream_id}:actor",
        "engram_memory_guide",
        MEMORY_GUIDE_REMINDER,
        insert_type="dynamic",
    )
    reminder_store.set(
        f"stream:{stream_id}:actor", REMINDER_NAME, "今日回顾", insert_type="dynamic"
    )
    request = _new_request(stream_id, reminder_store)
    request.add_payload(LLMPayload(ROLE.USER, Text("第一轮")))
    request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("第一轮回复")))
    request.add_payload(LLMPayload(ROLE.USER, Text("第二轮")))
    params = {
        "request_name": "example_chat",
        "meta_data": {"stream_id": stream_id},
        "payloads": request.payloads,
    }
    await handler.execute(EventType.BEFORE_LLM_REQUEST, params)
    items = reminder_store.get_items(
        f"stream:{stream_id}:actor", names=[persona_injection.REMINDER_NAME]
    )
    assert len(items) == 1
    assert items[0].insert_type is prompt_api.SystemReminderInsertType.FIXED
    assert items[0].consume_type is prompt_api.SystemReminderConsumeType.FOREVER
    assert stream_id in plugin._persona_reminder_streams
    assert all(
        call.args == ("person-example",) for call in read_persona.await_args_list
    )
    assert any(
        persona.impression_text in text for text in _text_parts(params["payloads"][0])
    )
    assert all(
        persona_injection.REMINDER_NAME not in text
        for payload in params["payloads"][1:]
        for text in _text_parts(payload)
    )
    assert any(
        "engram_memory_guide" in text for text in _text_parts(params["payloads"][-1])
    )
    assert any("今日回顾" in text for text in _text_parts(params["payloads"][-1]))

    persona.impression_text = "他希望我叫他阿树。①"
    resumed = _new_request(stream_id, reminder_store)
    for payload in params["payloads"]:
        resumed.add_payload(payload)
    resumed.add_payload(LLMPayload(ROLE.ASSISTANT, Text("第二轮回复")))
    resumed.add_payload(LLMPayload(ROLE.USER, Text("第三轮")))
    params["payloads"] = resumed.payloads
    await handler.execute(EventType.BEFORE_LLM_REQUEST, params)
    flattened = [
        text for payload in params["payloads"] for text in _text_parts(payload)
    ]
    assert (
        sum(f"[{persona_injection.REMINDER_NAME}]" in text for text in flattened) == 1
    )
    assert any(
        persona.impression_text in text for text in _text_parts(params["payloads"][0])
    )
    assert not any("小树" in text for text in flattened)
    assert any(
        "engram_memory_guide" in text for text in _text_parts(params["payloads"][-1])
    )
    assert any("今日回顾" in text for text in _text_parts(params["payloads"][-1]))
    other = _new_request("stream-other", reminder_store)
    other.add_payload(LLMPayload(ROLE.USER, Text("另一私聊")))
    assert all(
        persona_injection.REMINDER_NAME not in text
        for text in _text_parts(other.payloads[0])
    )


@pytest.mark.parametrize(
    "missing", ["group", "stream", "identity", "snapshot", "uncertified", "empty"]
)
@pytest.mark.asyncio
async def test_private_persona_removes_old_block_when_no_current_impression(
    reminder_store: SystemReminderStore,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """群聊或缺少可信当前印象时移除自身旧块，不生成、不回退旧残留。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.app.plugin_system.types import EventType

    from .. import plugin as plugin_module
    from ..config import EngramMemoryConfig
    from ..vnext import persona_injection

    plugin = plugin_module.EngramMemoryPlugin(EngramMemoryConfig())
    info = {
        "chat_type": "group" if missing == "group" else "private",
        "person_id": "" if missing == "identity" else "person-example",
    }
    persona = SimpleNamespace(
        person_id="person-example",
        impression_text="" if missing == "empty" else "未经认证的旧印象",
        is_current=missing != "uncertified",
    )
    lookup = AsyncMock(return_value=None if missing == "snapshot" else persona)
    plugin.runtime_owner = cast(
        plugin_module.VNextRuntimeOwner,
        SimpleNamespace(
            persona_service=SimpleNamespace(get_persona=lookup),
        ),
    )
    monkeypatch.setattr(
        persona_injection.stream_api,
        "get_stream_info",
        AsyncMock(
            return_value=None if missing == "stream" else info,
        ),
    )
    stream_id = "stream-example"
    reminder_store.set(
        f"stream:{stream_id}:actor",
        persona_injection.REMINDER_NAME,
        "旧印象",
        insert_type="fixed",
    )
    reminder_store.set(
        f"stream:{stream_id}:actor", REMINDER_NAME, "保留日记", insert_type="dynamic"
    )
    plugin._persona_reminder_streams.add(stream_id)
    request = _new_request(stream_id, reminder_store)
    request.add_payload(LLMPayload(ROLE.USER, Text("聊天正文")))
    params = {
        "request_name": "example_chat",
        "meta_data": {"stream_id": stream_id},
        "payloads": request.payloads,
    }
    await persona_injection.VNextPrivatePersonaEventHandler(plugin).execute(
        EventType.BEFORE_LLM_REQUEST, params
    )
    assert not reminder_store.get_items(
        f"stream:{stream_id}:actor", names=[persona_injection.REMINDER_NAME]
    )
    assert stream_id not in plugin._persona_reminder_streams
    texts = [text for payload in params["payloads"] for text in _text_parts(payload)]
    assert not any("旧印象" in text or "未经认证" in text for text in texts)
    assert "聊天正文" in texts
    assert any("保留日记" in text for text in texts)
    if missing in {"group", "stream", "identity"}:
        lookup.assert_not_awaited()
    else:
        lookup.assert_awaited_once_with("person-example")


@pytest.mark.parametrize(
    "request_name",
    ["engram_chat_diary_update", "engram_vnext_persona_update", "background_task"],
)
@pytest.mark.asyncio
async def test_private_persona_skips_internal_or_non_actor_requests(
    reminder_store: SystemReminderStore,
    monkeypatch: pytest.MonkeyPatch,
    request_name: str,
) -> None:
    """内部生成与未接入记忆提醒的请求不会读取人物或改写其输入。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.app.plugin_system.types import EventType

    from .. import plugin as plugin_module
    from ..config import EngramMemoryConfig
    from ..vnext import persona_injection

    plugin = plugin_module.EngramMemoryPlugin(EngramMemoryConfig())
    lookup, get_stream = AsyncMock(), AsyncMock()
    plugin.runtime_owner = cast(
        plugin_module.VNextRuntimeOwner,
        SimpleNamespace(
            persona_service=SimpleNamespace(get_persona=lookup),
        ),
    )
    monkeypatch.setattr(persona_injection.stream_api, "get_stream_info", get_stream)
    payloads = [LLMPayload(ROLE.USER, Text("独立输入"))]
    if request_name != "background_task":
        payloads[0].content.append(
            Text("<system_reminder>\n[engram_memory_guide]\n指引\n</system_reminder>")
        )
    original = list(_text_parts(payloads[0]))
    params = {
        "request_name": request_name,
        "meta_data": {"stream_id": "stream-example"},
        "payloads": payloads,
    }
    await persona_injection.VNextPrivatePersonaEventHandler(plugin).execute(
        EventType.BEFORE_LLM_REQUEST, params
    )
    assert _text_parts(params["payloads"][0]) == original
    get_stream.assert_not_awaited()
    lookup.assert_not_awaited()


def test_reminder_payload_is_scoped_to_stream_and_removal_is_local(
    reminder_store: SystemReminderStore,
) -> None:
    """上下文 manager 按 stream 读取提醒，刷新删除仅影响自己的标记块。"""
    reminder_store.set(
        "stream:stream-a:actor", REMINDER_NAME, "A 日记", insert_type="dynamic"
    )
    reminder_store.set(
        "stream:stream-b:actor", REMINDER_NAME, "B 日记", insert_type="dynamic"
    )
    for stream_id, expected in (("stream-a", "A 日记"), ("stream-b", "B 日记")):
        request = _new_request(stream_id, reminder_store)
        request.add_payload(LLMPayload(ROLE.USER, Text(f"来自 {stream_id}")))
        assert any(expected in text for text in _text_parts(request.payloads[-1]))

    payloads = [
        LLMPayload(
            ROLE.USER,
            [
                Text(f"<system_reminder>\n[{REMINDER_NAME}]\n旧\n</system_reminder>"),
                Text("历史正文中提到 [engram_memory_chat_diary] 但不是独立块"),
            ],
        ),
    ]
    assert refresh_diary_payloads(payloads, "")
    assert _text_parts(payloads[0]) == [
        "历史正文中提到 [engram_memory_chat_diary] 但不是独立块"
    ]


@pytest.mark.asyncio
async def test_events_wake_without_awaiting_generation_and_skip_internal_requests(
    reminder_store: SystemReminderStore,
) -> None:
    """消息事件只同步唤醒，生成请求不读或改写自己的 source payload。"""
    from src.core.components.types import EventType
    from src.core.models.message import Message
    from src.kernel.event import EventDecision

    runtime = _DiaryRuntime("日记正文")
    handler = ChatDiaryEventHandler(cast(BasePlugin, _Plugin(runtime)))
    decision, _ = await handler.execute(EventType.ON_ALL_PLUGIN_LOADED, {})
    assert decision is EventDecision.SUCCESS
    assert runtime.started == 1

    message = Message(stream_id="stream-a", chat_type="group", content="原消息")
    decision, params = await handler.execute(
        EventType.ON_MESSAGE_RECEIVED, {"message": message}
    )
    assert decision is EventDecision.SUCCESS
    assert params["message"] is message
    assert runtime.observed == ["stream-a"]
    assert runtime.read_streams == []
    await handler.execute(EventType.AFTER_MESSAGE_SENT, {"message": message})
    assert runtime.observed == ["stream-a", "stream-a"]
    assert runtime.read_streams == []

    payloads = [LLMPayload(ROLE.USER, Text("日记生成任务输入"))]
    for request_name in ("engram_chat_diary_update", "engram_vnext_persona_update"):
        decision, output = await handler.execute(
            EventType.BEFORE_LLM_REQUEST,
            {
                "request_name": request_name,
                "meta_data": {"stream_id": "stream-a"},
                "payloads": payloads,
            },
        )
        assert decision is EventDecision.SUCCESS
        assert output["payloads"] is payloads
        assert _text_parts(payloads[0]) == ["日记生成任务输入"]
    assert runtime.read_streams == []


@pytest.mark.asyncio
async def test_chatter_step_deletes_reminder_when_runtime_returns_empty(
    reminder_store: SystemReminderStore,
) -> None:
    """无日记、关闭或无许可时空内容会清除流私有 reminder。"""
    from src.core.components.types import EventType
    from src.kernel.event import EventDecision

    runtime = _DiaryRuntime("")
    handler = ChatDiaryEventHandler(cast(BasePlugin, _Plugin(runtime)))
    reminder_store.set(
        "stream:stream-a:actor",
        REMINDER_NAME,
        "旧日记",
        insert_type="dynamic",
    )

    decision, _ = await handler.execute(
        EventType.ON_CHATTER_STEP, {"stream_id": "stream-a"}
    )

    assert decision is EventDecision.SUCCESS
    assert runtime.read_streams == ["stream-a"]
    assert reminder_store.get("stream:stream-a:actor", names=[REMINDER_NAME]) == ""


@pytest.mark.parametrize("content", ["当前日记正文", ""])
@pytest.mark.asyncio
async def test_before_request_uses_stream_metadata_and_current_diary(
    reminder_store: SystemReminderStore,
    content: str,
) -> None:
    """回复前仅保留最新日记，无日记时剥离旧块，正常原消息保持不变。"""
    from src.core.components.types import EventType
    from src.kernel.event import EventDecision

    runtime = _DiaryRuntime(content)
    handler = ChatDiaryEventHandler(cast(BasePlugin, _Plugin(runtime)))
    reminder_store.set(
        "stream:stream-a:actor",
        REMINDER_NAME,
        "旧版提醒",
        insert_type="dynamic",
    )
    request = _new_request("stream-a", reminder_store)
    request.add_payload(LLMPayload(ROLE.USER, Text("前一轮用户原文")))
    request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("前一轮回复")))
    request.add_payload(LLMPayload(ROLE.USER, Text("本轮用户原文")))
    source_payloads = request.payloads
    event_payloads = list(source_payloads)
    params = {
        "request_name": "test_chat",
        "meta_data": {"stream_id": "stream-a"},
        "payloads": event_payloads,
    }

    decision, output = await handler.execute(EventType.BEFORE_LLM_REQUEST, params)
    assert decision is EventDecision.SUCCESS
    assert output["payloads"] is event_payloads
    assert runtime.read_streams == ["stream-a"]
    assert sum(
        text.startswith(f"<system_reminder>\n[{REMINDER_NAME}]\n")
        for payload in event_payloads
        for text in _text_parts(payload)
    ) == (1 if content else 0)
    assert all(
        REMINDER_NAME not in text
        for payload in event_payloads[:-1]
        for text in _text_parts(payload)
    )
    texts = [text for payload in event_payloads for text in _text_parts(payload)]
    assert "前一轮用户原文" in texts
    assert "前一轮回复" in texts
    assert "本轮用户原文" in texts
    assert not any("旧版提醒" in text for text in texts)
    if content:
        assert any(content in text for text in _text_parts(event_payloads[-1]))
    expected = f"[{REMINDER_NAME}]\n{content}" if content else ""
    assert (
        reminder_store.get("stream:stream-a:actor", names=[REMINDER_NAME]) == expected
    )
    assert request.payloads is source_payloads
    assert any("旧版提醒" in text for text in _text_parts(source_payloads[-1]))
