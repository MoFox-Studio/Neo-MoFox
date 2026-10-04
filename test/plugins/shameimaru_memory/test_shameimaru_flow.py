"""Shameimaru Memory 周期任务与回复前注入器测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.shameimaru_memory import job as job_module
from plugins.shameimaru_memory.config import ShameimaruMemoryConfig
from plugins.shameimaru_memory.event_handler import ShameimaruPromptInjector
from plugins.shameimaru_memory.models import NewsEntry, PersonRef, SummaryEntry
from plugins.shameimaru_memory.store import ShameimaruMemoryStore
from src.core.components.types import EventType
from src.core.models.message import Message
from src.kernel.event import EventDecision
from src.kernel.llm import LLMPayload, Text


def _config(tmp_path: Path) -> ShameimaruMemoryConfig:
    config = ShameimaruMemoryConfig()
    config.storage.data_dir = str(tmp_path)
    return config


def _plugin(tmp_path: Path) -> MagicMock:
    plugin = MagicMock()
    plugin.config = _config(tmp_path)
    return plugin


def _message(
    *,
    platform: str = "qq",
    sender_id: str = "123",
    sender_name: str = "小明",
    text: str = "你好",
    time: float = 1000.0,
    group_id: str = "",
    sender_role: str | None = None,
) -> Message:
    return Message(
        message_id=f"msg-{sender_id}-{time}",
        time=time,
        processed_plain_text=text,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_role=sender_role,
        platform=platform,
        chat_type="group",
        group_id=group_id,
    )


# ----------------------------------------------------------------------
# 摘要更新事件
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_job_skips_empty_group(tmp_path: Path, mocker) -> None:
    mocker.patch.object(job_module.stream_api, "get_stream_ids_from_db", AsyncMock(return_value=[]))
    plugin = _plugin(tmp_path)
    stats = await job_module.run_summary_job(plugin)
    assert stats["summarized"] == 0
    assert stats["groups"] == 0


@pytest.mark.asyncio
async def test_summary_job_generates_and_persists(tmp_path: Path, mocker) -> None:
    messages = [
        _message(
            sender_id="1",
            sender_name="小明",
            text="今天去爬山了",
            time=100.0,
            group_id="10001",
        ),
        _message(
            sender_id="2",
            sender_name="小红",
            text="下次一起啊",
            time=110.0,
            group_id="10001",
        ),
    ]
    mocker.patch.object(job_module.stream_api, "get_stream_messages", AsyncMock(return_value=messages))
    mocker.patch.object(job_module.stream_api, "get_stream", AsyncMock(return_value=None))
    mocker.patch.object(job_module.stream_api, "get_stream_info", AsyncMock(return_value=None))
    mocker.patch.object(
        job_module,
        "call_sub_agent",
        AsyncMock(return_value="小明和小红约定下周一起去爬山。"),
    )
    plugin = _plugin(tmp_path)

    # 摘要任务只处理摘要文件中已注册的群聊，需先注册
    store = job_module._build_store(plugin)
    await store.ensure_group("s1")

    stats = await job_module.run_summary_job(plugin)
    assert stats["summarized"] == 1

    group = await store.get_group_summary("s1")
    assert len(group.entries) == 1
    entry = group.entries[0]
    assert "爬山" in entry.content
    assert {ref.person_id for ref in entry.participants} == {"qq:1", "qq:2"}
    # platform/group_id 应从消息兜底补全
    assert group.platform == "qq"
    assert group.group_id == "10001"

    # 二次运行：无水位逻辑，同一消息窗口会被再次摘要并追加条目
    stats2 = await job_module.run_summary_job(plugin)
    assert stats2["summarized"] == 1
    assert len((await store.get_group_summary("s1")).entries) == 2


@pytest.mark.asyncio
async def test_summary_job_fills_platform_from_stream_info(tmp_path: Path, mocker) -> None:
    """DB 流记录提供 platform/group_id/group_name。"""
    mocker.patch.object(
        job_module.stream_api,
        "get_stream_messages",
        AsyncMock(return_value=[_message(sender_id="1", text="你好", time=100.0)]),
    )
    mocker.patch.object(job_module.stream_api, "get_stream", AsyncMock(return_value=None))
    mocker.patch.object(
        job_module.stream_api,
        "get_stream_info",
        AsyncMock(
            return_value={
                "stream_id": "s1",
                "platform": "qq",
                "group_id": "20002",
                "group_name": "测试群",
            }
        ),
    )
    mocker.patch.object(job_module, "call_sub_agent", AsyncMock(return_value="群里的寒暄。"))
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.ensure_group("s1")

    stats = await job_module.run_summary_job(plugin)
    assert stats["summarized"] == 1

    group = await store.get_group_summary("s1")
    assert group.platform == "qq"
    assert group.group_id == "20002"
    assert group.group_name == "测试群"


@pytest.mark.asyncio
async def test_summary_job_skips_no_meaningful_content(tmp_path: Path, mocker) -> None:
    mocker.patch.object(
        job_module.stream_api,
        "get_stream_messages",
        AsyncMock(return_value=[_message(text="哈哈", time=200.0)]),
    )
    mocker.patch.object(job_module.stream_api, "get_stream", AsyncMock(return_value=None))
    mocker.patch.object(job_module, "call_sub_agent", AsyncMock(return_value="NO_MEANINGFUL_CONTENT"))
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.ensure_group("s1")

    stats = await job_module.run_summary_job(plugin)
    assert stats["summarized"] == 0
    assert len((await store.get_group_summary("s1")).entries) == 0


# ----------------------------------------------------------------------
# 新闻记录事件
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_news_job_creates_entries_and_updates_personas_on_eviction(
    tmp_path: Path, mocker
) -> None:
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="小梅宣布下个月要搬家了。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )

    plugin.config.news.max_entries = 1
    # 第一次调用（新闻整理）：返回两条新闻
    # 第二次调用（人物背景更新）：返回人物背景文本
    call_log: list[str] = []
    persona_users: list[str] = []

    async def _fake_sub_agent(**kwargs: Any) -> str:
        call_log.append(kwargs["request_name"])
        if kwargs["request_name"] == "shameimaru_news":
            return (
                '[{"title": "小梅要搬家", '
                '"content": "小梅宣布下个月搬家，原因是工作调动。", '
                '"participants": [{"person_id": "qq:1", "name": "小梅"}]}, '
                '{"title": "小红学画画", '
                '"content": "小红开始学画画，报了周末班。", '
                '"participants": [{"person_id": "qq:2", "name": "小红"}]}]'
            )
        persona_users.append(kwargs["user"])
        return "小梅因工作调动即将搬家，她目前从事文案工作。"

    mocker.patch.object(job_module, "call_sub_agent", side_effect=_fake_sub_agent)

    stats = await job_module.run_news_job(plugin)
    assert stats["created"] == 2
    assert stats["evicted"] == 1

    # 人物层提示词应携带被维护人物的名字，避免 LLM 误把内容主角当作该人物
    assert persona_users, "人物层应发起更新调用"
    assert "人物名字：小梅" in persona_users[0]

    # 使用全新 store 实例（模拟重启）验证落盘结果
    fresh_store = ShameimaruMemoryStore(plugin.config)
    news = await fresh_store.get_news()
    assert len(news) == 1
    assert news[0].title == "小红学画画"

    # 参与处理的摘要被标记为废弃而非删除（保留供 Dreaming 读取）
    group = await fresh_store.get_group_summary("s1")
    assert len(group.entries) == 1
    assert group.entries[0].deprecated is True
    assert group.group_name == "群A"

    # 被淘汰的新闻涉及小梅，人物背景应更新
    assert call_log[-1] == "shameimaru_persona"
    assert "搬家" in await fresh_store.get_persona("qq:1")
    assert await fresh_store.get_persona("qq:2") == ""


@pytest.mark.asyncio
async def test_news_job_skips_without_summaries(tmp_path: Path, mocker) -> None:
    plugin = _plugin(tmp_path)
    mocker.patch.object(job_module, "call_sub_agent", AsyncMock(return_value="[]"))
    stats = await job_module.run_news_job(plugin)
    assert stats["groups"] == 0
    assert stats["processed"] == 0
    assert stats["created"] == 0


@pytest.mark.asyncio
async def test_news_job_keeps_summaries_on_sub_agent_failure(tmp_path: Path, mocker) -> None:
    """子 agent 调用失败（空响应）时，已生成的摘要必须保留，等待下轮重试。"""
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="小梅宣布下个月要搬家了。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )

    mocker.patch.object(job_module, "call_sub_agent", AsyncMock(return_value=""))

    stats = await job_module.run_news_job(plugin)
    assert stats["processed"] == 1
    assert stats["created"] == 0

    # 摘要未被清空、未被标记废弃：下轮仍可重试消费
    group = await store.get_group_summary("s1")
    assert len(group.entries) == 1
    assert group.entries[0].content == "小梅宣布下个月要搬家了。"
    assert group.entries[0].deprecated is False


@pytest.mark.asyncio
async def test_news_job_does_not_reconsume_deprecated_summaries(
    tmp_path: Path, mocker
) -> None:
    """已废弃的摘要不会被新闻层再次消费，防止生成重复新闻。"""
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="小梅宣布下个月要搬家了。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )

    call_log: list[str] = []

    async def _fake_sub_agent(**kwargs: Any) -> str:
        call_log.append(kwargs["request_name"])
        if kwargs["request_name"] == "shameimaru_news":
            return (
                '[{"title": "小梅要搬家", "content": "小梅下月搬家。", '
                '"participants": [{"person_id": "qq:1", "name": "小梅"}]}]'
            )
        return ""

    mocker.patch.object(job_module, "call_sub_agent", side_effect=_fake_sub_agent)

    # 第一轮：消费未废弃摘要并标记废弃
    stats1 = await job_module.run_news_job(plugin)
    assert stats1["created"] == 1
    assert (await store.get_group_summary("s1")).entries[0].deprecated is True

    # 第二轮：无未废弃摘要，不再调用子 agent、不再生成新闻
    call_log.clear()
    stats2 = await job_module.run_news_job(plugin)
    assert stats2["processed"] == 0
    assert stats2["created"] == 0
    assert call_log == []
    assert len(await store.get_news()) == 1


@pytest.mark.asyncio
async def test_dream_job_reads_deprecated_summaries(tmp_path: Path, mocker) -> None:
    """知识层（Dreaming）会读取已被新闻层标记为废弃的摘要，了解群聊主题。"""
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="群里经常聊到小梅与小紫的关系。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )

    # 新闻层消费后，摘要被标记为废弃
    async def _news_agent(**kwargs: Any) -> str:
        if kwargs["request_name"] == "shameimaru_news":
            return (
                '[{"title": "小梅与小紫是姐妹", "content": "小梅和小紫是亲姐妹。", '
                '"participants": [{"person_id": "qq:1", "name": "小梅"}]}]'
            )
        return ""

    mocker.patch.object(job_module, "call_sub_agent", side_effect=_news_agent)
    stats = await job_module.run_news_job(plugin)
    assert stats["created"] == 1
    assert (await store.get_group_summary("s1")).entries[0].deprecated is True

    # Dreaming 子 agent 的输入应包含已废弃摘要的内容
    captured_user: dict[str, str] = {}

    async def _dream_agent(**kwargs: Any) -> str:
        if kwargs["request_name"] == "shameimaru_dream":
            captured_user["user"] = kwargs["user"]
        return "[]"

    fake_service = MagicMock()
    fake_service.create = AsyncMock(return_value={"action": "create", "mode": "created"})
    mocker.patch.object(job_module.service_api, "get_service", return_value=fake_service)
    mocker.patch.object(job_module, "call_sub_agent", side_effect=_dream_agent)

    await job_module.run_dream_job(plugin)
    assert "小梅与小紫的关系" in captured_user["user"]


@pytest.mark.asyncio
async def test_news_job_resolves_participants_from_summary_roster(
    tmp_path: Path, mocker
) -> None:
    """子 agent 返回的 person_id 必须映射回摘要清单中的真实 ID，编造的丢弃。"""
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="小梅宣布下个月要搬家了。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )
    captured: dict[str, str] = {}

    async def _fake_sub_agent(**kwargs: Any) -> str:
        if kwargs["request_name"] == "shameimaru_news":
            captured["user"] = kwargs["user"]
            return (
                '[{"title": "小梅要搬家", "content": "小梅下月搬家。", '
                '"participants": ['
                '{"person_id": "qq:999", "name": "小梅"}, '
                '{"person_id": "qq:2", "name": "小红"}]}]'
            )
        return ""

    mocker.patch.object(job_module, "call_sub_agent", side_effect=_fake_sub_agent)

    stats = await job_module.run_news_job(plugin)
    assert stats["created"] == 1
    assert stats["processed"] == 1

    # 可用人物清单已传给子 agent
    assert "可用人物清单" in captured["user"]
    assert "qq:1（小梅）" in captured["user"]

    # 编造的 qq:999 通过 name 匹配映射回真实 qq:1；qq:2 不在清单中，被丢弃
    fresh_store = ShameimaruMemoryStore(plugin.config)
    news = await fresh_store.get_news()
    assert len(news) == 1
    assert [ref.person_id for ref in news[0].participants] == ["qq:1"]
    assert news[0].participants[0].name == "小梅"

    # 参与处理的摘要被标记为废弃（保留供 Dreaming 读取）
    group = await fresh_store.get_group_summary("s1")
    assert len(group.entries) == 1
    assert group.entries[0].deprecated is True


@pytest.mark.asyncio
async def test_news_job_processes_each_group_separately(tmp_path: Path, mocker) -> None:
    """每个群聊单独分组调用一次子 agent，互不混合，摘要分别清空。"""
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="A 群：小梅宣布搬家。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )
    await store.append_summary(
        "s2",
        SummaryEntry(
            timestamp=2.0,
            content="B 群：小红开始学画画。",
            participants=[PersonRef(person_id="qq:2", name="小红")],
        ),
        group_name="群B",
        max_entries=50,
    )

    captured_users: list[str] = []

    async def _fake_sub_agent(**kwargs: Any) -> str:
        if kwargs["request_name"] == "shameimaru_news":
            captured_users.append(kwargs["user"])
            return (
                '[{"title": "新闻", "content": "内容。", '
                '"participants": []}]'
            )
        return ""

    mocker.patch.object(job_module, "call_sub_agent", side_effect=_fake_sub_agent)

    stats = await job_module.run_news_job(plugin)
    assert stats["processed"] == 2
    assert stats["created"] == 2
    assert len(captured_users) == 2

    # 每个群单独分组：各自包含自己的群名与摘要，互不混合
    group_a_user = next(u for u in captured_users if "群A" in u)
    group_b_user = next(u for u in captured_users if "群B" in u)
    assert "A 群：小梅宣布搬家" in group_a_user
    assert "B 群：小红开始学画画" in group_b_user
    assert "小红开始学画画" not in group_a_user
    assert "A 群：小梅宣布搬家" not in group_b_user

    # 两个群的摘要均被标记为废弃（保留供 Dreaming 读取）
    fresh_store = ShameimaruMemoryStore(plugin.config)
    assert (await fresh_store.get_group_summary("s1")).entries[0].deprecated is True
    assert (await fresh_store.get_group_summary("s2")).entries[0].deprecated is True


# ----------------------------------------------------------------------
# 知识层：Dreaming 整理事件
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dream_job_knowledge_izes_news(tmp_path: Path, mocker) -> None:
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_summary(
        "s1",
        SummaryEntry(
            timestamp=1.0,
            content="群里经常聊到小梅与小紫的关系。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        group_name="群A",
        max_entries=50,
    )
    await store.append_news(
        NewsEntry(
            id="n1",
            timestamp=2.0,
            title="小梅与小紫是姐妹",
            content="小梅和小紫是亲姐妹，小紫比小梅大三岁。",
            participants=[
                PersonRef(person_id="qq:1", name="小梅"),
                PersonRef(person_id="qq:3", name="小紫"),
            ],
        ),
        max_entries=50,
    )
    await store.append_news(
        NewsEntry(
            id="n2",
            timestamp=3.0,
            title="群里讨论午饭吃什么",
            content="大家讨论了附近新开的拉面店。",
            participants=[PersonRef(person_id="qq:2", name="小红")],
        ),
        max_entries=50,
    )

    fake_service = MagicMock()
    fake_service.create = AsyncMock(return_value={"action": "create", "mode": "created"})
    mocker.patch.object(
        job_module.service_api, "get_service", return_value=fake_service
    )
    mocker.patch.object(
        job_module,
        "call_sub_agent",
        side_effect=(
            lambda **kwargs: (
                '[{"news_id": "n1", "title": "小梅与小紫为亲姐妹", '
                '"content": "小梅与小紫是亲姐妹，小紫大三岁。", '
                '"knowledge_type": "身份关系"}, '
                '{"news_id": "n1", "title": "小梅与小紫为亲姐妹", '
                '"content": "小梅与小紫是亲姐妹，小紫大三岁。", '
                '"knowledge_type": "身份关系"}]'
                if kwargs["request_name"] == "shameimaru_dream"
                else "小梅与小紫是亲姐妹，两人关系密切。"
            )
        ),
    )

    stats = await job_module.run_dream_job(plugin)
    assert stats["created"] == 1
    assert stats["deleted"] == 1

    # 同一响应中重复的 news_id 只写入一次知识库
    assert fake_service.create.await_count == 1

    # 使用全新 store 实例（模拟重启）验证落盘结果：n1 被知识化删除，n2 保留
    fresh_store = ShameimaruMemoryStore(plugin.config)
    remaining = await fresh_store.get_news()
    assert [entry.id for entry in remaining] == ["n2"]

    # 知识写入 booku_memory_store
    fake_service.create.assert_awaited_once()
    call_kwargs = fake_service.create.await_args.kwargs
    assert call_kwargs["bucket"] == "knowledge"
    assert call_kwargs["memory_type"] == "knowledge"
    assert call_kwargs["related_people"] == ["qq:1", "qq:3"]
    assert call_kwargs["knowledge_type"] == "身份关系"
    assert call_kwargs["source"] == "shameimaru_memory"

    # 被知识化的新闻涉及人物背景更新
    assert "亲姐妹" in await fresh_store.get_persona("qq:1")


@pytest.mark.asyncio
async def test_dream_job_skips_when_service_missing(tmp_path: Path, mocker) -> None:
    plugin = _plugin(tmp_path)
    mocker.patch.object(job_module.service_api, "get_service", return_value=None)
    stats = await job_module.run_dream_job(plugin)
    assert stats["skipped"] is True


# ----------------------------------------------------------------------
# 回复前注入器
# ----------------------------------------------------------------------


def _build_prompt_params(stream_id: str = "stream-abc", extra: str = "") -> dict[str, Any]:
    return {
        "name": "neo_default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": extra, "stream_id": stream_id},
        "policies": {},
        "strict": False,
    }


@pytest.mark.asyncio
async def test_injector_writes_stream_reminders_for_unread_persons(
    tmp_path: Path, mocker
) -> None:
    from src.core.prompt import get_system_reminder_store, reset_system_reminder_store

    reset_system_reminder_store()
    plugin = _plugin(tmp_path)
    store = ShameimaruMemoryStore(plugin.config)
    await store.append_news(
        NewsEntry(
            id="n1",
            timestamp=1.0,
            title="小梅新换了工作",
            content="小梅从广告公司跳槽到了游戏公司。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        max_entries=50,
    )
    await store.set_persona("qq:1", "小梅喜欢摄影，性格开朗。")

    fake_stream = MagicMock()
    fake_stream.context.unread_messages = [
        _message(sender_id="1", sender_name="小梅", text="在吗")
    ]
    mocker.patch.object(
        __import__("plugins.shameimaru_memory.event_handler", fromlist=["stream_api"]).stream_api,
        "get_stream",
        AsyncMock(return_value=fake_stream),
    )

    handler = ShameimaruPromptInjector(plugin=plugin)
    params = _build_prompt_params()
    decision, out = await handler.execute(EventType.ON_PROMPT_BUILD, params)

    assert decision is EventDecision.SUCCESS
    # 不直接修改 prompt
    assert out["values"]["extra"] == ""

    # 相关新闻写入流私有 system reminder（dynamic），并包含引导文本
    news_reminder = get_system_reminder_store().get(
        "stream:stream-abc:actor", names=["相关新闻"]
    )
    assert "小梅新换了工作" in news_reminder
    assert "供你回复时参考" in news_reminder
    assert "不要一次性罗列" in news_reminder
    item = get_system_reminder_store().get_items(
        "stream:stream-abc:actor", names=["相关新闻"]
    )[0]
    assert item.insert_type.value == "dynamic"
    assert item.consume_type.value == "forever"

    # 相关人物背景写入流私有 system reminder（dynamic），并包含引导文本
    persona_reminder = get_system_reminder_store().get(
        "stream:stream-abc:actor", names=["相关人物背景"]
    )
    assert "小梅喜欢摄影" in persona_reminder
    assert "长期了解到的背景信息" in persona_reminder
    assert "保持言行一致" in persona_reminder
    item = get_system_reminder_store().get_items(
        "stream:stream-abc:actor", names=["相关人物背景"]
    )[0]
    assert item.insert_type.value == "dynamic"


@pytest.mark.asyncio
async def test_injector_collects_persons_from_flushed_history(tmp_path: Path, mocker) -> None:
    """neo_default_chatter 时序：unread 已 flush 进 history 时，仍能收集当前对话人物。"""
    from src.core.prompt import get_system_reminder_store, reset_system_reminder_store

    reset_system_reminder_store()
    plugin = _plugin(tmp_path)
    store = job_module._build_store(plugin)
    await store.append_news(
        NewsEntry(
            id="n1",
            timestamp=1.0,
            title="小梅新换了工作",
            content="小梅从广告公司跳槽到了游戏公司。",
            participants=[PersonRef(person_id="qq:1", name="小梅")],
        ),
        max_entries=50,
    )

    # 模拟 neo 的 flush 时序：unread 为空，当前对话消息已在 history 中
    fake_stream = MagicMock()
    fake_stream.chat_type = "group"
    fake_stream.context.unread_messages = []
    fake_stream.context.history_messages = [
        _message(sender_id="9", sender_name="路人", text="很久以前", time=1.0),
        _message(
            sender_id="bot-id", sender_name="小狐狸", text="我在",
            time=2.0, sender_role="bot",
        ),
        _message(sender_id="1", sender_name="小梅", text="在吗", time=3.0),
    ]
    mocker.patch.object(
        __import__("plugins.shameimaru_memory.event_handler", fromlist=["stream_api"]).stream_api,
        "get_stream",
        AsyncMock(return_value=fake_stream),
    )

    handler = ShameimaruPromptInjector(plugin=plugin)
    params = _build_prompt_params()
    decision, out = await handler.execute(EventType.ON_PROMPT_BUILD, params)
    assert decision is EventDecision.SUCCESS

    # 历史中的小梅被识别，bot 消息被排除
    news_reminder = get_system_reminder_store().get(
        "stream:stream-abc:actor", names=["相关新闻"]
    )
    assert "小梅新换了工作" in news_reminder


@pytest.mark.asyncio
async def test_injector_removes_reminders_without_matching_content(
    tmp_path: Path, mocker
) -> None:
    from src.core.prompt import get_system_reminder_store, reset_system_reminder_store

    reset_system_reminder_store()
    plugin = _plugin(tmp_path)
    mocker.patch.object(
        __import__("plugins.shameimaru_memory.event_handler", fromlist=["stream_api"]).stream_api,
        "get_stream",
        AsyncMock(return_value=None),
    )
    handler = ShameimaruPromptInjector(plugin=plugin)

    # 无 unread 人物 → 两个 reminder 均被删除（不存在）
    params = _build_prompt_params()
    decision, out = await handler.execute(EventType.ON_PROMPT_BUILD, params)
    assert decision is EventDecision.SUCCESS
    assert get_system_reminder_store().get("stream:stream-abc:actor") == ""


@pytest.mark.asyncio
async def test_injector_skips_other_prompt(tmp_path: Path, mocker) -> None:
    from src.core.prompt import get_system_reminder_store, reset_system_reminder_store

    reset_system_reminder_store()
    plugin = _plugin(tmp_path)
    handler = ShameimaruPromptInjector(plugin=plugin)

    params = _build_prompt_params()
    params["name"] = "other_prompt"
    decision, out = await handler.execute(EventType.ON_PROMPT_BUILD, params)
    assert decision is EventDecision.SUCCESS
    assert get_system_reminder_store().get("stream:stream-abc:actor") == ""


@pytest.mark.asyncio
async def test_reminder_dynamic_injected_at_tail_once_across_turns() -> None:
    """dynamic reminder 注入到对话尾部，且全上下文不重复出现。"""
    from src.core.prompt import (
        STREAM_BUCKET_PREFIX,
        SystemReminderConsumeType,
        SystemReminderInsertType,
        get_system_reminder_store,
        reset_system_reminder_store,
    )
    from src.kernel.llm import (
        LLMContextManager,
        ROLE,
        ReminderSourceSpec,
    )

    reset_system_reminder_store()
    stream_id = "stream-abc"
    bucket = f"{STREAM_BUCKET_PREFIX}{stream_id}:actor"

    # 模拟 default_chatter create_request(with_reminder="actor") 的 reminder 源
    context = LLMContextManager(
        reminder_sources=[
            ReminderSourceSpec(bucket="actor", wrap_with_system_tag=True),
            ReminderSourceSpec(bucket=bucket, wrap_with_system_tag=True),
        ]
    )
    payloads: list[LLMPayload] = []
    payloads = context.add_payload(payloads, LLMPayload(ROLE.SYSTEM, Text("系统设定")))

    store = get_system_reminder_store()
    old_content = "- [10:00] 小梅新换了工作：从广告公司跳槽到游戏公司。"

    # 第 1 轮：写入 reminder 后追加用户消息
    store.set(
        bucket,
        "相关新闻",
        old_content,
        insert_type=SystemReminderInsertType.DYNAMIC,
        consume=SystemReminderConsumeType.FOREVER,
    )
    payloads = context.add_payload(payloads, LLMPayload(ROLE.USER, Text("用户消息1")))
    assert payloads[-1].role == ROLE.USER
    assert old_content in _payload_text(payloads[-1])
    assert _count_text(payloads, old_content) == 1

    # 第 2 轮：reminder 内容更新后再追加用户消息
    new_content = "- [11:00] 小梅要搬家了：因工作调动下月搬家。"
    store.set(
        bucket,
        "相关新闻",
        new_content,
        insert_type=SystemReminderInsertType.DYNAMIC,
        consume=SystemReminderConsumeType.FOREVER,
    )
    payloads = context.add_payload(payloads, LLMPayload(ROLE.USER, Text("用户消息2")))

    # 旧内容从全上下文剥离，新内容只出现一次且在对话尾部
    assert _count_text(payloads, old_content) == 0
    assert _count_text(payloads, new_content) == 1
    assert payloads[-1].role == ROLE.USER
    assert new_content in _payload_text(payloads[-1])
    assert "用户消息1" in _payload_text(payloads[1])
    assert "用户消息2" in _payload_text(payloads[-1])


def _payload_text(payload: LLMPayload) -> str:
    """提取 payload 中全部 Text 段。"""
    parts: list[str] = []
    for part in payload.content:
        if isinstance(part, Text):
            parts.append(part.text)
    return "\n".join(parts)


def _count_text(payloads: list[LLMPayload], text: str) -> int:
    """统计文本在全部 payload 中的出现次数。"""
    return sum(_payload_text(payload).count(text) for payload in payloads)
