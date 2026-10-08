"""聊天日记的事务、续读、隔离、生成与请求替换测试。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, TypeVar, cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event

from src.app.plugin_system.types import ROLE, EventType, LLMPayload, Text

from ..config import EngramMemoryConfig
from ..diary import runtime as diary_runtime
from ..diary import service as diary_service
from ..diary.config import DiaryConfig
from ..diary.events import ChatDiaryEventHandler
from ..diary.service import DiaryService, DiarySource, StreamDetails
from ..diary.store import Diary, DiaryStore, Progress
from ..vnext.framework_bridge import ManagedTaskHandle

StoredValue = TypeVar("StoredValue", Diary, Progress)


def _required(value: StoredValue | None) -> StoredValue:
    """断言存储读取结果存在，并保留日记或处理位置的类型。"""
    assert value is not None
    return value


@pytest.fixture
def diary_path() -> Iterator[str]:
    """提供结束后自动清理的独立日记路径。"""
    with TemporaryDirectory(prefix="engram-diary-test-") as directory:
        yield str(Path(directory) / "diary.db")


@pytest.mark.asyncio
async def test_private_diary_does_not_require_person_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊日记不扫描历史核对人物，路由信息存在即可整理聊天。"""
    monkeypatch.setattr(diary_service.stream_api, "get_stream_info", AsyncMock(return_value={
        "platform": "custom", "chat_type": "private", "person_id": "unresolved-profile",
    }))
    read_messages = AsyncMock(side_effect=AssertionError("不应核对人物"))
    monkeypatch.setattr(diary_service.stream_api, "get_stream_messages", read_messages)
    details = await DiarySource().details("example-stream")
    assert details == StreamDetails("example-stream", "custom", "private", "", "")
    read_messages.assert_not_called()


def test_diary_config_ignores_obsolete_recovery_messages() -> None:
    """忽略废弃的补回设置，保留触发配置和其他字段的严格校验。"""
    data = {
        "diary": {
            "recovery_messages": 200,
            "private": {"message_threshold": 37},
        }
    }
    config = EngramMemoryConfig.from_dict(data)
    assert "recovery_messages" not in config.diary.model_dump()
    assert config.diary.private.message_threshold == 37
    assert data["diary"]["recovery_messages"] == 200
    with pytest.raises(ValueError, match="unsupported"):
        EngramMemoryConfig.from_dict({"diary": {"unsupported": 1}})


def test_diary_config_body_char_budget() -> None:
    """整篇日记预算具有默认值，允许正整数配置并拒绝零值。"""
    assert DiaryConfig().body_char_budget == 800
    config = EngramMemoryConfig.from_dict({"diary": {"body_char_budget": 400}})
    assert config.diary.body_char_budget == 400
    with pytest.raises(ValueError, match="body_char_budget"):
        DiaryConfig(body_char_budget=0)


@pytest.mark.asyncio
async def test_diary_atomic_commit_and_stale_rejection(diary_path: str) -> None:
    """正文与位置同批保存，正文不变也前进，过期结果不覆盖。"""
    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        original = await store.ensure_stream(
            "s1",
            "group",
            start_time=1,
            bootstrap_through=2,
            now=10,
        )
        await store.commit_batch(
            original,
            through_id=1,
            now=11,
            diary=Diary(
                "s1",
                "2026-01-01",
                "当天回顾",
                1,
                5,
                11,
            ),
        )
        saved = await store.progress("s1")
        assert saved is not None and saved.cursor_id == 1
        assert saved.last_success_at == 11
        with pytest.raises(RuntimeError, match="过期"):
            await store.commit_batch(
                original,
                through_id=2,
                now=12,
                diary=Diary(
                    "s1",
                    "2026-01-01",
                    "不应覆盖",
                    2,
                    6,
                    12,
                ),
            )
        assert _required(await store.get_day("s1", "2026-01-01")).body == "当天回顾"
        await store.commit_batch(
            saved,
            through_id=2,
            now=13,
            diary=Diary(
                "s1",
                "2026-01-01",
                "当天回顾",
                2,
                6,
                13,
            ),
        )
        assert _required(await store.progress("s1")).cursor_id == 2
        restored = await store.ensure_stream(
            "s1",
            "group",
            start_time=999,
            bootstrap_through=999,
            now=999,
        )
        assert restored.start_time == 1 and restored.bootstrap_through == 2
        assert restored.cursor_id == 2
    finally:
        await store.close()


class ChatSource(DiarySource):
    """只在内存提供按主键排序的真实消息形状。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        """保存测试消息，不调用框架数据库或适配器。"""
        self.rows = rows
        self.stream_details = {
            "s1": StreamDetails("s1", "qq", "group", "example-group", ""),
        }

    async def details(self, stream_id: str) -> StreamDetails | None:
        """返回测试流的公开路由身份。"""
        return self.stream_details.get(stream_id)

    async def page(
        self, progress: Any, through_id: int, *, limit: int
    ) -> list[dict[str, Any]]:
        """以固定主键上界读取一页。"""
        return [
            row
            for row in self.rows
            if row["stream_id"] == progress.stream_id
            and progress.cursor_id < row["id"] <= through_id
        ][:limit]

    async def window(self, progress: Any) -> dict[str, int]:
        """返回固定回填起点内尚未处理的最新消息水位。"""
        rows = [
            row
            for row in self.rows
            if row["stream_id"] == progress.stream_id and row["id"] > progress.cursor_id
        ]
        if progress.cursor_id < progress.bootstrap_through:
            rows = [row for row in rows if row["time"] >= progress.start_time]
        return {
            "last_id": max((row["id"] for row in rows), default=progress.cursor_id),
            "pending_count": len(rows),
        }

    async def context(
        self, details: StreamDetails, first: Any, limit: int
    ) -> list[dict[str, Any]]:
        """只提供少量明确标为前文的消息。"""
        return (
            [row for row in self.rows if row["id"] < first["id"]][-limit:]
            if limit
            else []
        )

    async def formatted(
        self, details: Any, rows: Any, zone: Any
    ) -> list[dict[str, object]]:
        """保留顺序与发言内容用于模型输入核对。"""
        return [dict(row) for row in rows]


def chat_row(number: int, moment: str, stream_id: str = "s1") -> dict[str, Any]:
    """构造指定时区日期的实际消息记录。"""
    return {
        "id": number,
        "message_id": f"m{number}",
        "stream_id": stream_id,
        "time": datetime.fromisoformat(moment).timestamp(),
        "content": f"发言{number}",
    }


class ManagedTasks:
    """在当前事件循环内执行并等待运行时创建的托管任务。"""

    def __init__(self) -> None:
        """初始化受测任务句柄集合。"""
        self.handles: list[ManagedTaskHandle] = []

    def create(
        self, coroutine: Any, *, name: str, daemon: bool = True
    ) -> ManagedTaskHandle:
        """用 asyncio task 替代框架任务管理器。"""
        task = asyncio.create_task(coroutine, name=name)
        handle = ManagedTaskHandle(name, task)
        self.handles.append(handle)
        return handle

    async def wait(self) -> None:
        """等待当前所有受管任务完成。"""
        tasks = [handle.task for handle in self.handles if handle.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.handles.clear()


def install_task_harness(monkeypatch: pytest.MonkeyPatch) -> ManagedTasks:
    """将 runtime 的任务入口替换为可等待的本地任务。"""
    harness = ManagedTasks()
    monkeypatch.setattr(diary_runtime, "create_managed_task", harness.create)
    monkeypatch.setattr(diary_runtime, "cancel_managed_task", lambda task_id: True)
    return harness


@pytest.mark.asyncio
async def test_diary_default_group_and_private_triggers(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """群默认须同时达到三小时和 200 条，私聊默认达到 100 条即可。"""

    async def check_trigger(
        database_path: str,
        chat_type: str,
        count: int,
        elapsed: int,
        expected: bool,
    ) -> None:
        """用 schedule_once 验证默认策略的消息阈值。"""
        harness = install_task_harness(monkeypatch)
        config = DiaryConfig(database_path=database_path)
        source = ChatSource(
            [
                chat_row(index, "2026-10-03T08:00:00+08:00")
                for index in range(1, count + 1)
            ]
        )
        source.stream_details["s1"] = StreamDetails(
            "s1",
            "qq",
            chat_type,
            "example-group" if chat_type == "group" else "",
            "example-user" if chat_type == "private" else "",
        )
        service = DiaryService(
            config,
            DiaryStore(database_path),
            source=source,
            generator=lambda payload: asyncio.sleep(0, result="正文"),
        )
        clock = datetime.fromisoformat("2026-10-03T12:00:00+08:00").timestamp()
        runtime = diary_runtime.DiaryRuntime(
            config, service=service, clock=lambda: clock
        )
        await runtime.initialize()
        await runtime.store.ensure_stream(
            "s1",
            chat_type,
            start_time=0,
            bootstrap_through=0,
            now=clock - elapsed,
        )
        runtime._known.add("s1")
        try:
            await runtime.schedule_once()
            assert bool(runtime._running) is expected
            await harness.wait()
        finally:
            await runtime.close()

    for index, (chat_type, count, elapsed, expected) in enumerate(
        (
            ("group", 199, 10800, False),
            ("group", 200, 1, False),
            ("group", 200, 10800, True),
            ("private", 99, 10800, False),
            ("private", 100, 1, True),
        )
    ):
        database_path = str(Path(diary_path).with_name(f"diary-trigger-{index}.db"))
        await check_trigger(database_path, chat_type, count, elapsed, expected)


@pytest.mark.asyncio
async def test_diary_runtime_serializes_streams_and_processes_fixed_watermark(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行时限制跨流并发、同流重入，生成期间到达消息留待下一轮。"""
    harness = install_task_harness(monkeypatch)
    config = DiaryConfig(database_path=diary_path, max_concurrency=1, batch_messages=10)
    config.group.trigger_mode = "messages"
    config.group.message_threshold = 1
    source = ChatSource(
        [
            chat_row(1, "2026-10-03T08:00:00+08:00", "s1"),
            chat_row(1, "2026-10-03T08:00:00+08:00", "s2"),
        ]
    )
    source.stream_details["s2"] = StreamDetails(
        "s2", "qq", "group", "another-group", ""
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    generated: list[str] = []

    async def generate(payload: dict[str, object]) -> str:
        """暂停首个生成并记录实际流。"""
        messages = cast(list[dict[str, object]], payload["new_messages"])
        generated.append(str(messages[0]["message_id"]))
        if len(generated) == 1:
            entered.set()
            await release.wait()
            source.rows.append(chat_row(2, "2026-10-03T08:01:00+08:00", "s1"))
        return "完整日记"

    service = DiaryService(
        config, DiaryStore(diary_path), source=source, generator=generate
    )
    now = datetime.fromisoformat("2026-10-03T12:00:00+08:00").timestamp()
    runtime = diary_runtime.DiaryRuntime(config, service=service, clock=lambda: now)
    await runtime.initialize()
    for stream_id in ("s1", "s2"):
        await runtime.store.ensure_stream(
            stream_id, "group", start_time=0, bootstrap_through=0, now=now - 1
        )
    runtime._known.update(("s1", "s2"))
    try:
        await runtime.schedule_once()
        await entered.wait()
        await runtime.schedule_once()
        assert len(harness.handles) == 1
        release.set()
        await harness.wait()
        assert _required(await runtime.store.progress("s1")).cursor_id == 1
        await runtime.schedule_once()
        await harness.wait()
        assert generated == ["m1", "m2"]
        assert _required(await runtime.store.progress("s1")).cursor_id == 2
        await runtime.schedule_once()
        await harness.wait()
        assert generated == ["m1", "m2", "m1"]
        assert _required(await runtime.store.progress("s2")).cursor_id == 1
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_diary_runtime_retries_three_total_attempts_then_resumes_on_new_message(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一批次总共失败三次后暂停，新消息使流重新进入调度。"""
    harness = install_task_harness(monkeypatch)
    config = DiaryConfig(database_path=diary_path, retry_limit=2)
    config.group.trigger_mode = "messages"
    config.group.message_threshold = 1
    source = ChatSource([chat_row(1, "2026-10-03T08:00:00+08:00")])
    clock = [datetime.fromisoformat("2026-10-03T12:00:00+08:00").timestamp()]
    calls = 0

    async def generate(payload: dict[str, object]) -> str:
        """前 3 次请求失败，后续请求成功。"""
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise ValueError("测试生成失败")
        return "恢复后的日记"

    service = DiaryService(
        config, DiaryStore(diary_path), source=source, generator=generate
    )
    runtime = diary_runtime.DiaryRuntime(
        config, service=service, clock=lambda: clock[0]
    )
    await runtime.initialize()
    await runtime.store.ensure_stream(
        "s1", "group", start_time=0, bootstrap_through=0, now=clock[0] - 1
    )
    runtime._known.add("s1")
    try:
        for attempt in range(3):
            await runtime.schedule_once()
            await harness.wait()
            if attempt < 2:
                clock[0] = runtime._retry_at["s1"]
        assert calls == 3
        assert runtime._exhausted["s1"] == 1
        assert "s1" not in runtime._retry_at
        assert _required(await runtime.store.progress("s1")).cursor_id == 0
        source.rows.append(chat_row(2, "2026-10-03T08:01:00+08:00"))
        await runtime.schedule_once()
        await harness.wait()
        assert calls == 4
        assert _required(await runtime.store.progress("s1")).cursor_id == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_diary_runtime_restart_preserves_initial_window_and_shanghai_six_day_start(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次回填从上海今天前六天开始，跨日期重启也不滚动起点或上界。"""
    now = datetime.fromisoformat("2026-10-03T00:30:00+08:00").timestamp()
    window_calls: list[dict[str, Any]] = []

    async def get_window(stream_id: str, **kwargs: Any) -> dict[str, int]:
        """记录公开消息窗口读取并返回移动水位。"""
        window_calls.append(kwargs)
        return {"last_id": 41 + len(window_calls), "pending_count": 0}

    config = DiaryConfig(database_path=diary_path)
    source = ChatSource([])
    monkeypatch.setattr(source, "bootstrap_window", get_window)
    service = DiaryService(
        config,
        DiaryStore(diary_path),
        source=source,
        generator=lambda payload: asyncio.sleep(0, result=""),
    )
    first_runtime = diary_runtime.DiaryRuntime(
        config, service=service, clock=lambda: now
    )
    await first_runtime.initialize()
    try:
        first = await service.prepare_stream(source.stream_details["s1"], now)
        expected_start = datetime.fromisoformat("2026-09-27T00:00:00+08:00").timestamp()
        assert first.start_time == expected_start
        assert first.bootstrap_through == 42
    finally:
        await first_runtime.close()

    restarted_service = DiaryService(
        config,
        DiaryStore(diary_path),
        source=source,
        generator=lambda payload: asyncio.sleep(0, result=""),
    )
    restarted_runtime = diary_runtime.DiaryRuntime(
        config,
        service=restarted_service,
        clock=lambda: now + timedelta(days=1).total_seconds(),
    )
    await restarted_runtime.initialize()
    try:
        restored = await restarted_service.prepare_stream(
            source.stream_details["s1"], now + 86400
        )
        assert restored.start_time == first.start_time
        assert restored.bootstrap_through == first.bootstrap_through
        assert restored.initialized_at == first.initialized_at
        assert len(window_calls) == 1
    finally:
        await restarted_runtime.close()


@pytest.mark.asyncio
async def test_diary_scheduler_recovers_without_new_messages(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动读取失败后保持托管轮询，无需新消息即可恢复调度。"""
    harness = install_task_harness(monkeypatch)
    config = DiaryConfig(database_path=diary_path)
    source = ChatSource([])
    runtime = diary_runtime.DiaryRuntime(
        config,
        service=DiaryService(config, DiaryStore(diary_path), source=source),
    )
    restored = asyncio.Event()
    list_streams = AsyncMock(side_effect=[RuntimeError("恢复读取失败"), ()])

    async def signal_schedule() -> None:
        """标记调度已进入正常轮询。"""
        restored.set()

    monkeypatch.setattr(runtime.store, "list_streams", list_streams)
    monkeypatch.setattr(runtime, "schedule_once", signal_schedule)
    monkeypatch.setattr(
        diary_runtime.stream_api, "get_stream_ids_from_db", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(diary_runtime, "_POLL_SECONDS", 0.001)
    await runtime.initialize()
    try:
        runtime.start()
        await asyncio.wait_for(restored.wait(), timeout=1.0)
        assert list_streams.await_count == 2
        assert runtime._task is not None
    finally:
        for handle in harness.handles:
            if handle.task is not None:
                handle.task.cancel()
        await runtime.close()


@pytest.mark.asyncio
async def test_diary_runtime_reminder_isolates_seven_days_without_uncovered_messages(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提醒保留本地日期时间和最近七日正文，不展示时区或未归档原消息。"""
    now = datetime.fromisoformat("2026-10-03T00:30:00+08:00").timestamp()
    source = ChatSource([chat_row(4, "2026-10-03T00:15:00+08:00")])
    config = DiaryConfig(database_path=diary_path)
    service = DiaryService(
        config,
        DiaryStore(diary_path),
        source=source,
        generator=lambda payload: asyncio.sleep(0, result=""),
    )
    runtime = diary_runtime.DiaryRuntime(config, service=service, clock=lambda: now)
    await runtime.initialize()
    progress = await runtime.store.ensure_stream(
        "s1", "group", start_time=0, bootstrap_through=3, now=now
    )
    for method in ("window", "page", "formatted"):
        monkeypatch.setattr(
            source,
            method,
            AsyncMock(side_effect=AssertionError("日记提醒不得读取未归档原消息")),
        )
    try:
        for cursor, (day, body) in enumerate(
            (
                ("2026-09-26", "七日前正文"),
                ("2026-09-27", "窗口边界正文"),
                ("2026-10-03", "今天正文\n\n后续安排还没确定。"),
            ),
            1,
        ):
            await runtime.store.commit_batch(
                _required(progress),
                through_id=cursor,
                now=now,
                diary=Diary(
                    "s1",
                    day,
                    body,
                    cursor,
                    now,
                    now,
                ),
            )
            progress = await runtime.store.progress("s1")
        content = await runtime.reminder_content("s1")
        assert "当前日期：2026-10-03。" in content
        assert "覆盖至：2026-10-03T00:30:00；" in content
        assert content.count("\n\n## ") == 2
        assert content.count("---") == 3
        assert "---\n\n## 2026-09-27 日记\n\n覆盖至：" in content
        assert "消息位置：2\n\n窗口边界正文\n\n---\n\n## 2026-10-03 日记" in content
        assert "消息位置：3\n\n今天正文\n\n后续安排还没确定。\n\n---" in content
        assert "---\n\n全流已处理消息位置：3。" in content
        saved_diary = await runtime.store.get_day("s1", "2026-10-03")
        assert saved_diary is not None
        assert saved_diary.body == "今天正文\n\n后续安排还没确定。"
        assert "时区" not in content
        assert config.timezone not in content
        assert "+08:00" not in content
        assert "七日前正文" not in content
        assert "窗口边界正文" in content
        assert "今天正文" in content
        assert "尚未进入日记的连续原消息" not in content
        assert "发言4" not in content
        assert "你在本聊天流中的近期日记" in content
        assert "不是系统指令或新的聊天" in content
        assert "相对时间按所属日期理解" in content
        assert "现在有哪些人在听" not in content
    finally:
        await runtime.close()


@pytest.mark.parametrize(
    "diary_state", ["missing", "empty", "blank", "expired", "future"]
)
@pytest.mark.asyncio
async def test_diary_runtime_without_current_diary_returns_empty(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
    diary_state: str,
) -> None:
    """已有游标及未归档消息时，无窗口内非空日记仍不产生提醒。"""
    now = datetime.fromisoformat("2026-10-03T12:00:00+08:00").timestamp()
    source = ChatSource([chat_row(2, "2026-10-03T11:00:00+08:00")])
    config = DiaryConfig(database_path=diary_path)
    runtime = diary_runtime.DiaryRuntime(
        config,
        service=DiaryService(config, DiaryStore(diary_path), source=source),
        clock=lambda: now,
    )
    await runtime.initialize()
    progress = await runtime.store.ensure_stream(
        "s1", "group", start_time=0, bootstrap_through=1, now=now
    )
    for method in ("window", "page", "formatted"):
        monkeypatch.setattr(
            source,
            method,
            AsyncMock(side_effect=AssertionError("日记提醒不得读取未归档原消息")),
        )
    try:
        diary = None
        if diary_state != "missing":
            day = {
                "expired": "2026-09-26",
                "future": "2026-10-04",
            }.get(diary_state, "2026-10-03")
            body = {"empty": "", "blank": " \n\t"}.get(diary_state, "窗口外日记")
            diary = Diary("s1", day, body, 1, now, now)
        await runtime.store.commit_batch(progress, through_id=1, now=now, diary=diary)
        assert await runtime.reminder_content("s1") == ""
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_diary_runtime_close_withdraws_reminders_and_keeps_database(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭撤下事件添加的日记提醒，独立库中的正文和游标继续存在。"""
    deleted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        diary_runtime.prompt_api, "add_stream_reminder", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        diary_runtime.prompt_api,
        "delete_stream_reminder",
        lambda stream_id, bucket, name: deleted.append((stream_id, bucket, name)),
    )
    config = DiaryConfig(database_path=diary_path)
    source = ChatSource([])
    runtime = diary_runtime.DiaryRuntime(
        config,
        service=DiaryService(config, DiaryStore(diary_path), source=source),
        clock=lambda: datetime.fromisoformat("2026-10-03T12:00:00+08:00").timestamp(),
    )
    await runtime.initialize()
    progress = await runtime.store.ensure_stream(
        "s1", "group", start_time=0, bootstrap_through=1, now=1
    )
    await runtime.store.commit_batch(
        progress,
        through_id=1,
        now=2,
        diary=Diary(
            "s1",
            "2026-10-03",
            "保留正文",
            1,
            1,
            2,
        ),
    )
    handler = ChatDiaryEventHandler(
        cast(
            Any,
            SimpleNamespace(
                runtime_owner=SimpleNamespace(diary=runtime),
            ),
        )
    )
    await handler.execute(EventType.ON_CHATTER_STEP, {"stream_id": "s1"})
    await runtime.close()
    assert deleted == [("s1", "actor", diary_runtime.REMINDER_NAME)]

    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        assert _required(await store.progress("s1")).cursor_id == 1
        assert _required(await store.get_day("s1", "2026-10-03")).body == "保留正文"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_diary_batches_keep_order_dates_and_full_replacement(
    diary_path: str,
) -> None:
    """跨午夜归属各自日期，同日新批次收到旧稿并返回完整替换。"""
    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        await store.ensure_stream(
            "s1", "group", start_time=0, bootstrap_through=4, now=1
        )
        source = ChatSource(
            [
                chat_row(index, moment)
                for index, moment in enumerate(
                    [
                        "2026-01-01T23:59:59+08:00",
                        "2026-01-02T00:00:00+08:00",
                        "2026-01-02T00:00:01+08:00",
                        "2026-01-02T00:00:02+08:00",
                    ],
                    1,
                )
            ]
        )
        seen: list[dict[str, Any]] = []

        async def generate(payload: dict[str, object]) -> str:
            """模拟可以完整改写旧稿的模型。"""
            seen.append(payload)
            return f"完整正文{len(seen)}"

        service = DiaryService(
            DiaryConfig(batch_messages=2), store, source=source, generator=generate
        )
        details = StreamDetails("s1", "qq", "group", "example-group", "")
        for _ in range(3):
            assert await service.process_batch(details, 4, now=2)
        assert [row["id"] for payload in seen for row in payload["new_messages"]] == [
            1,
            2,
            3,
            4,
        ]
        assert [payload["target_date"] for payload in seen] == [
            "2026-01-01",
            "2026-01-02",
            "2026-01-02",
        ]
        assert seen[-1]["existing_diary"] == "完整正文2"
        assert seen[0]["previous_diaries"] == []
        assert seen[1]["previous_diaries"] == [
            {"date": "2026-01-01", "body": "完整正文1"}
        ]
        assert seen[2]["previous_diaries"] == seen[1]["previous_diaries"]
        assert _required(await store.get_day("s1", "2026-01-01")).body == "完整正文1"
        assert _required(await store.get_day("s1", "2026-01-02")).body == "完整正文3"
        assert _required(await store.progress("s1")).cursor_id == 4
        assert not await service.process_batch(details, 4, now=3)
    finally:
        await store.close()


@pytest.mark.parametrize("chat_type", ["group", "private"])
@pytest.mark.parametrize(
    ("context_days", "missing_days"), [(1, False), (3, False), (7, False), (7, True)]
)
@pytest.mark.asyncio
async def test_diary_generation_reads_previous_days_but_only_updates_target(
    diary_path: str, chat_type: str, context_days: int, missing_days: bool
) -> None:
    """按目标自然日提供同流前序完整日记，保存仅替换目标日且不补缺日。"""
    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        first_date = datetime.fromisoformat("2025-12-30").date()
        target_date = datetime.fromisoformat("2026-01-07").date()
        for stream_id in ("s1", "s2"):
            progress = await store.ensure_stream(
                stream_id, chat_type, start_time=0, bootstrap_through=12, now=1
            )
            for index in range(1, 12):
                previous_date = first_date + timedelta(days=index - 1)
                if missing_days and previous_date.day == 4:
                    continue
                body = f"{stream_id}-{previous_date.isoformat()}"
                if previous_date.day == 6:
                    body += "完整前序正文" * 1000
                if missing_days and previous_date.day == 3:
                    body = "   "
                await store.commit_batch(
                    progress,
                    through_id=index,
                    now=index + 1,
                    diary=Diary(
                        stream_id,
                        previous_date.isoformat(),
                        body,
                        index,
                        float(index),
                        index + 1,
                    ),
                )
                saved_progress = await store.progress(stream_id)
                assert saved_progress is not None
                progress = saved_progress
        before = await store.diaries("s1", first_date.isoformat())
        other_before = await store.diaries("s2", first_date.isoformat())
        config = DiaryConfig()
        config.policy_for(chat_type).context_days = context_days
        other_type = "private" if chat_type == "group" else "group"
        config.policy_for(other_type).context_days = 2
        seen: list[dict[str, object]] = []

        async def generate(payload: dict[str, object]) -> str:
            """记录日记整理的真实输入，返回目标日完整正文。"""
            seen.append(payload)
            return "目标日期的新正文"

        service = DiaryService(
            config,
            store,
            source=ChatSource([chat_row(12, "2026-01-07T00:30:00+08:00")]),
            generator=generate,
        )
        details = StreamDetails("s1", "qq", chat_type, "example-group", "example-user")
        assert await service.process_batch(
            details,
            12,
            now=datetime.fromisoformat("2026-10-05T12:00:00+08:00").timestamp(),
        )
        expected = [
            {"date": item.day, "body": item.body}
            for item in before
            if (target_date - timedelta(days=context_days - 1)).isoformat()
            <= item.day
            < target_date.isoformat()
            and item.body.strip()
        ]
        assert len(seen) == 1
        assert seen[0]["target_date"] == "2026-01-07"
        assert seen[0]["existing_diary"] == "s1-2026-01-07"
        assert seen[0]["previous_diaries"] == expected
        if context_days == 7 and not missing_days:
            assert [item["date"] for item in expected] == [
                f"2026-01-{number:02d}" for number in range(1, 7)
            ]
        after = await store.diaries("s1", first_date.isoformat())
        assert tuple(item for item in after if item.day != "2026-01-07") == tuple(
            item for item in before if item.day != "2026-01-07"
        )
        target = await store.get_day("s1", "2026-01-07")
        assert target is not None and target.body == "目标日期的新正文"
        assert await store.diaries("s2", first_date.isoformat()) == other_before
        progress = await store.progress("s1")
        assert progress is not None and progress.cursor_id == 12
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_diary_failure_retains_state_and_new_arrivals(diary_path: str) -> None:
    """失败保留原状态，生成期间新增消息不丢也不混入固定批次。"""
    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        await store.ensure_stream(
            "s1", "private", start_time=0, bootstrap_through=1, now=1
        )
        source = ChatSource([chat_row(1, "2026-01-01T08:00:00+08:00")])
        calls = 0

        async def generate(payload: dict[str, object]) -> str:
            """首轮失败，后续成功时到达另一条消息。"""
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("模型失败")
            source.rows.append(chat_row(2, "2026-01-01T08:00:01+08:00"))
            return "今天的回顾"

        service = DiaryService(DiaryConfig(), store, source=source, generator=generate)
        details = StreamDetails("s1", "qq", "private", "", "example-user")
        with pytest.raises(ValueError, match="模型失败"):
            await service.process_batch(details, 1, now=2)
        assert _required(await store.progress("s1")).cursor_id == 0
        assert await store.get_day("s1", "2026-01-01") is None
        assert await service.process_batch(details, 1, now=3)
        assert _required(await store.progress("s1")).cursor_id == 1
        assert source.rows[-1]["id"] == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_diary_date_window_and_stream_isolation(diary_path: str) -> None:
    """自然日窗口不补篇、不混流，过期日记仍保留。"""
    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        for stream_id in ("s1", "s2"):
            progress = await store.ensure_stream(
                stream_id,
                "private",
                start_time=1,
                bootstrap_through=3,
                now=1,
            )
            for cursor, day in enumerate(("2026-01-01", "2026-01-03", "2026-01-08"), 1):
                await store.commit_batch(
                    _required(progress),
                    through_id=cursor,
                    now=cursor + 1,
                    diary=Diary(
                        stream_id,
                        day,
                        f"{stream_id}-{day}",
                        cursor,
                        float(cursor),
                        cursor + 1,
                    ),
                )
                progress = await store.progress(stream_id)
        window = await store.diaries("s1", "2026-01-02")
        assert [item.day for item in window] == ["2026-01-03", "2026-01-08"]
        assert all(item.body.startswith("s1-") for item in window)
        assert _required(await store.get_day("s1", "2026-01-01")).body == "s1-2026-01-01"
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["qq", "other"])
@pytest.mark.parametrize("chat_type", ["group", "private"])
async def test_diary_generation_and_reminder_without_adapter_config(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    chat_type: str,
) -> None:
    """各平台群私聊均能调度、生成和注入日记，不查询适配器配置。"""
    get_config = Mock(side_effect=AssertionError("日记不应读取适配器配置"))
    monkeypatch.setattr(diary_service.config_api, "get_config", get_config)
    tasks = ManagedTasks()
    monkeypatch.setattr(diary_runtime, "create_managed_task", tasks.create)
    rows = [chat_row(1, "2026-01-01T08:00:00+08:00")]
    now = float(rows[0]["time"]) + 1
    details = StreamDetails("s1", platform, chat_type, "example-group", "example-user")
    source = ChatSource(rows)
    source.stream_details["s1"] = details
    config = DiaryConfig()
    config.database_path = diary_path
    generator = AsyncMock(return_value="当天的聊天回顾")
    service = DiaryService(config, DiaryStore(diary_path), source=source, generator=generator)
    runtime = diary_runtime.DiaryRuntime(config, service=service, clock=lambda: now)
    runtime.set_reminder = Mock()
    await runtime.initialize()
    try:
        await runtime.store.ensure_stream("s1", chat_type, start_time=1, bootstrap_through=1, now=now)
        runtime._known.add("s1")
        await runtime.schedule_once()
        assert tasks.handles and tasks.handles[0].task is not None
        await tasks.handles[0].task
        generator.assert_awaited_once()
        assert generator.await_args is not None
        assert cast(dict[str, Any], generator.await_args.args[0])["new_messages"] == rows
        diary = await runtime.store.get_day("s1", "2026-01-01")
        progress = await runtime.store.progress("s1")
        assert diary is not None and diary.body == "当天的聊天回顾"
        assert progress is not None and progress.cursor_id == 1
        assert "当天的聊天回顾" in await runtime.reminder_content("s1")
        config.policy_for(chat_type).enabled = False
        assert await runtime.reminder_content("s1") == ""
        assert not await service.process_batch(details, 1, now=now)
        get_config.assert_not_called()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_diary_count_uses_all_saved_messages_without_adapter_config(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分页计数包含全部已保存消息，不读取名单，达到阈值即停止。"""
    get_config = Mock(side_effect=AssertionError("日记不应读取适配器配置"))
    monkeypatch.setattr(diary_service.config_api, "get_config", get_config)
    config = DiaryConfig()
    config.database_path = diary_path
    config.batch_messages = 40
    config.group.message_threshold = 100
    source = DiarySource()
    source._windows["s1"] = [
        {
            **chat_row(index, "2026-01-01T08:00:00+08:00"),
            "sender_id": "789" if index % 2 == 0 else "101",
        }
        for index in range(1, 251)
    ]
    service = DiaryService(config, DiaryStore(diary_path), source=source)
    runtime = diary_runtime.DiaryRuntime(config, service=service)
    details = StreamDetails("s1", "qq", "group", "123", "")
    progress = Progress("s1", "group", 0, 0, 0, 1, None, "")
    assert await runtime._count_messages(details, progress, 250) == 100
    config.group.message_threshold = 300
    assert await runtime._count_messages(details, progress, 250) == 250
    get_config.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "private"])
async def test_diary_disabled_during_generation_preserves_body_and_cursor(
    diary_path: str,
    chat_type: str,
) -> None:
    """生成期间关闭本类型日记时，已有正文和处理游标均不改变。"""
    rows = [chat_row(1, "2026-01-01T08:00:00+08:00"), chat_row(2, "2026-01-01T09:00:00+08:00")]
    config = DiaryConfig()
    source = ChatSource(rows)
    source.stream_details["s1"] = StreamDetails("s1", "other", chat_type, "example-group", "example-user")
    store = DiaryStore(diary_path)
    await store.initialize()
    progress = await store.ensure_stream("s1", chat_type, start_time=1, bootstrap_through=2, now=1)
    original = Diary("s1", "2026-01-01", "保留的旧日记", 1, float(rows[0]["time"]), 2)
    await store.commit_batch(progress, through_id=1, now=2, diary=original)

    async def generate(payload: dict[str, object]) -> str:
        """在生成结果返回前关闭当前类型的日记。"""
        assert payload["existing_diary"] == original.body
        config.policy_for(chat_type).enabled = False
        return "不应提交的新正文"

    service = DiaryService(config, store, source=source, generator=generate)
    try:
        before = await store.progress("s1")
        assert before is not None
        with pytest.raises(RuntimeError, match="生成期间聊天日记已关闭"):
            await service.process_batch(source.stream_details["s1"], 2, now=3)
        assert await store.progress("s1") == before
        assert await store.get_day("s1", original.day) == original
    finally:
        await store.close()


@pytest.mark.parametrize("datetime_messages", [False, True])
@pytest.mark.asyncio
async def test_diary_public_pagination_keeps_equal_times_and_concurrent_arrivals(
    monkeypatch: pytest.MonkeyPatch,
    datetime_messages: bool,
) -> None:
    """现有偏移分页定位成功锚点，新增导致的重复页不会丢掉前面的消息。"""
    rows = [chat_row(index, "2026-10-03T08:00:00+08:00") for index in range(1, 271)]
    inserted = False
    reads: list[tuple[float, float]] = []

    async def stream_page(
        stream_id: str, limit: int = 100, offset: int = 0
    ) -> list[Any]:
        """分页途中插入一条同时间消息，使页偏移重复一条旧记录。"""
        nonlocal inserted
        if offset and not inserted:
            inserted = True
            rows.append(chat_row(271, "2026-10-03T08:00:00+08:00"))
        descending = list(reversed(rows))[offset : offset + limit]
        return [
            SimpleNamespace(
                **{
                    **row,
                    "time": datetime.fromtimestamp(row["time"], UTC)
                    if datetime_messages
                    else row["time"],
                }
            )
            for row in reversed(descending)
        ]

    async def time_window(
        stream_id: str, start: float, end: float, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """模拟公开时间查询，返回同时间戳所有数据库行。"""
        reads.append((start, end))
        return [dict(row) for row in rows if start <= row["time"] <= end]

    monkeypatch.setattr(diary_service.stream_api, "get_stream_messages", stream_page)
    monkeypatch.setattr(
        diary_service.message_api, "get_messages_by_time_in_chat_inclusive", time_window
    )
    progress = Progress("s1", "group", 103, 0, 0, 1, 2, "m103")
    source = DiarySource()
    window = await source.window(progress)
    assert window == {"last_id": 270, "pending_count": 167}
    material = await source.page(progress, 270, limit=500)
    assert [row["id"] for row in material] == list(range(104, 271))
    assert len(reads) == 1
    assert await source.window(progress) == {"last_id": 271, "pending_count": 168}
    assert len(reads) == 2
    assert await source.window(progress) == {"last_id": 271, "pending_count": 168}
    assert len(reads) == 2
    restarted = DiarySource()
    saved = Progress("s1", "group", 270, 0, 0, 1, 2, "m270")
    assert await restarted.window(saved) == {"last_id": 271, "pending_count": 1}
    assert [row["id"] for row in await restarted.page(saved, 271, limit=100)] == [271]


@pytest.mark.asyncio
async def test_diary_missing_source_anchor_refuses_to_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原锚点被外部删除时不能静默前进到最新消息。"""

    async def empty_page(stream_id: str, **kwargs: Any) -> list[Any]:
        """模拟被删除的聊天历史。"""
        return []

    monkeypatch.setattr(diary_service.stream_api, "get_stream_messages", empty_page)
    with pytest.raises(RuntimeError, match="锚点已不存在"):
        await DiarySource().window(Progress("s1", "group", 1, 0, 0, 1, 2, "m1"))


@pytest.mark.asyncio
async def test_diary_bootstrap_does_not_cache_messages_outside_its_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """初次窗口之外已到达的新消息仍必须进入下一轮，不能被缓存标记掩盖。"""
    rows = [
        chat_row(1, "2026-10-03T08:00:00+08:00"),
        chat_row(2, "2026-10-03T08:00:01+08:00"),
    ]

    async def messages_by_time(
        stream_id: str,
        start_time: float,
        end_time: float,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """只复制指定时间窗口中的实际记录。"""
        return [dict(row) for row in rows if start_time <= row["time"] <= end_time]

    async def stream_messages(
        stream_id: str,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[Any]:
        """模拟公开接口的倒序分页、页内正序行为。"""
        selected = list(reversed(rows))[offset : offset + limit]
        return [
            SimpleNamespace(message_id=row["message_id"], time=row["time"])
            for row in reversed(selected)
        ]

    monkeypatch.setattr(
        diary_service.message_api,
        "get_messages_by_time_in_chat_inclusive",
        messages_by_time,
    )
    monkeypatch.setattr(
        diary_service.stream_api, "get_stream_messages", stream_messages
    )
    source = DiarySource()
    assert await source.bootstrap_window(
        "s1",
        start_time=rows[0]["time"],
        end_time=rows[0]["time"],
    ) == {"last_id": 1, "pending_count": 1}
    progress = Progress("s1", "group", 1, rows[0]["time"], 1, 1, 1, "m1")
    assert await source.window(progress) == {"last_id": 2, "pending_count": 1}
    assert [row["message_id"] for row in await source.page(progress, 2, limit=100)] == [
        "m2"
    ]


@pytest.mark.asyncio
async def test_diary_commit_rolls_back_body_position_and_anchor(
    diary_path: str,
) -> None:
    """正文写入中断时，已执行的进度更新及锚点必须一起回滚。"""
    store = DiaryStore(diary_path)
    await store.initialize()
    try:
        progress = await store.ensure_stream(
            "s1", "group", start_time=0, bootstrap_through=2, now=1
        )
        await store.commit_batch(
            progress,
            through_id=1,
            message_id="m1",
            now=2,
            diary=Diary(
                "s1",
                "2026-10-03",
                "原正文",
                1,
                1,
                2,
            ),
        )
        progress = await store.progress("s1")

        def fail_body_write(
            connection: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            """在数据库开始写日记正文时注入异常。"""
            if statement.startswith("INSERT INTO chat_diary_days"):
                raise RuntimeError("测试事务中断")

        async with store.database.session() as session:
            engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", fail_body_write)
        try:
            with pytest.raises(RuntimeError, match="事务中断"):
                await store.commit_batch(
                    _required(progress),
                    through_id=2,
                    message_id="m2",
                    now=3,
                    diary=Diary(
                        "s1",
                        "2026-10-03",
                        "不能写入的正文",
                        2,
                        2,
                        3,
                    ),
                )
        finally:
            event.remove(engine, "before_cursor_execute", fail_body_write)
        assert await store.progress("s1") == progress
        assert _required(await store.get_day("s1", "2026-10-03")).body == "原正文"
        assert progress is not None
        assert progress.cursor_message_id == "m1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_diary_formats_actual_bot_and_placeholder_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 Bot 发言、旁观者和无可读图片各自保持原始来源身份。"""
    from zoneinfo import ZoneInfo

    async def bot_identity(platform: str) -> dict[str, str]:
        """提供实际发送账号的公开查询形状。"""
        return {"bot_id": "example-bot"}

    monkeypatch.setattr(
        diary_service.adapter_api, "get_bot_info_by_platform", bot_identity
    )
    rows = [
        {
            **chat_row(1, "2026-10-03T08:00:00+08:00"),
            "sender_id": "user",
            "content": "试着重启看看",
        },
        {
            **chat_row(2, "2026-10-03T08:00:00+08:00"),
            "person_id": "bot",
            "sender_id": "example-bot",
        },
        {
            **chat_row(3, "2026-10-03T08:00:00+08:00"),
            "sender_id": "other",
            "message_type": "image",
            "content": "[图片]",
        },
    ]
    result = await DiarySource().formatted(
        StreamDetails("s1", "qq", "group", "123", ""),
        rows,
        ZoneInfo("Asia/Shanghai"),
    )
    assert [row["role"] for row in result] == ["participant", "bot", "participant"]
    assert result[0]["text"] == "试着重启看看"
    assert result[-1]["text"] == "[图片]"
    assert result[-1]["time"] == "2026-10-03T08:00:00+08:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("body_char_budget", [8, 800])
@pytest.mark.parametrize(
    ("response_text", "expected_error"),
    [
        ('{"body":"今天看到他们讨论安排，还没有确定。"}', None),
        ('```json\n{"body":"今天看到他们讨论安排，还没有确定。"}\n```', None),
        ('```\n{"body":"今天看到他们讨论安排，还没有确定。"}\n```', None),
        (
            ' \r\n```json\r\n{"body":"今天看到他们讨论安排，还没有确定。"}\r\n```\r\n ',
            None,
        ),
        (" \n ", "空响应"),
        ("```json\n \n```", "空响应"),
        ("不是 JSON", "无效 JSON"),
        ('```json\n{"body":"正文"}', "无效 JSON"),
        ('前言\n```json\n{"body":"正文"}\n```', "无效 JSON"),
        ('```json\n{"body":"正文"}\n```\n后记', "无效 JSON"),
        ('```python\n{"body":"正文"}\n```', "无效 JSON"),
        ('{"body":', "无效 JSON"),
        ('{"body":null}', "唯一 body 文本字段"),
        ('{"body":"正文","extra":true}', "唯一 body 文本字段"),
        ("[]", "唯一 body 文本字段"),
    ],
)
async def test_diary_request_is_clean_and_persona_is_complete(
    diary_path: str,
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
    expected_error: str | None,
    body_char_budget: int,
) -> None:
    """独立请求保留人设、段落和整篇预算指令，合法正文不因超预算而截断。"""
    persona = {"name": "示例Bot", "personality": "自然说话", "safety": "遵守事实"}
    monkeypatch.setattr(
        diary_service.config_api,
        "get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(model_dump=lambda **kwargs: persona),
        ),
    )

    def actor_models(task: str) -> list[Any]:
        """校验日记复用人设表达的 Actor 模型任务。"""
        assert task == "actor"
        return []

    monkeypatch.setattr(diary_service.llm_api, "get_model_set_by_task", actor_models)
    create_request = diary_service.llm_api.create_llm_request
    requests: list[Any] = []

    def capture_request(*args: Any, **kwargs: Any) -> Any:
        """保持实际 context manager 和 payload 行为，仅禁止 provider 调用。"""
        request = create_request(*args, **kwargs)

        async def send(self: Any, *, stream: bool) -> asyncio.Future[str]:
            """返回一次确定性日记响应，不访问外部模型。"""
            response: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            response.set_result(response_text)
            return response

        monkeypatch.setattr(type(request), "send", send)
        requests.append(request)
        return request

    monkeypatch.setattr(diary_service.llm_api, "create_llm_request", capture_request)
    service = DiaryService(
        DiaryConfig(body_char_budget=body_char_budget), DiaryStore(diary_path)
    )
    payload: dict[str, object] = {
        "target_date": "2026-10-03",
        "timezone": "Asia/Shanghai",
        "existing_diary": "旧稿",
        "previous_diaries": [{"date": "2026-10-02", "body": "昨天聊到一起玩"}],
        "preceding_context": [{"text": "有空一起玩"}],
        "new_messages": [{"text": "时间还没定"}],
    }
    if expected_error is not None:
        with pytest.raises(ValueError, match=expected_error):
            await service.generate(payload)
    else:
        assert await service.generate(payload) == "今天看到他们讨论安排，还没有确定。"
    request = requests[0]
    assert request.request_name == diary_service.DIARY_REQUEST_NAME
    assert [item.role for item in request.payloads] == [ROLE.SYSTEM, ROLE.USER]
    system = cast(Text, cast(LLMPayload, request.payloads[0]).content[0]).text
    user = cast(Text, cast(LLMPayload, request.payloads[1]).content[0]).text
    assert system.startswith(
        "下面是你的完整人设。你的身份、经历、性格、表达习惯和相处边界都以它为准：\n"
        + json.dumps(persona, ensure_ascii=False)
        + "\n\n"
    )
    assert system.endswith(diary_service.DIARY_INSTRUCTIONS)
    assert f"当天整篇正文的写作预算为 {body_char_budget} 字" in system
    assert "已有底稿与本批新增内容合计使用这一预算" in system
    for instruction in (
        "用你的第一人称和自然口吻",
        "段落之间用空行分隔",
        "每段通常两三句",
        "整篇正文尽量控制在写作预算内",
        "压缩时仍须保留取消、纠正、结果",
        "这篇日记写的是你在这个聊天里的经历",
        "不预设亲密",
        "留给自己以后接着相处时看",
        "不补造自己当时的心情",
        "不只记录问题、任务和结果",
        "只旁观要写看到他们聊",
        "聊天中真实说出的承诺和约定仍按来源保留",
        "本人陈述、别人转述、建议、计划和实际结果要分清",
        "不能只顾最新消息而全删前文",
        "previous_diaries 是这个聊天在目标日期之前的日记",
        "只供理解前情、延续话题和辨认变化，不能改写",
        "只更新 target_date 对应的日记",
        "不把历史的明天当现在的明天",
        "唯一字段 body 为更新后的完整当天正文",
    ):
        assert instruction in system
    assert "写进日记不表示他愿意让别人知道" not in system
    assert json.loads(user) == payload
    assert "<system_reminder>" not in system + user
