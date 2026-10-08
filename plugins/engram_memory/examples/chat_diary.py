"""使用临时 SQLite 数据库演示独立聊天日记的按日更新与位置恢复。"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from zoneinfo import ZoneInfo

from ..diary.config import DiaryConfig
from ..diary.service import DiaryService, DiarySource, StreamDetails
from ..diary.store import DiaryStore, Progress


class LocalDiarySource(DiarySource):
    """从内存消息列表提供日记服务所需的本地数据。"""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        """保存示例消息，不连接框架消息存储。"""
        self.messages = messages

    async def page(
        self,
        progress: Progress,
        through_id: int,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """返回游标之后、固定截止位置内的最早消息。"""
        return [
            message
            for message in self.messages
            if progress.cursor_id < int(message["id"]) <= through_id
        ][:limit]

    async def context(
        self,
        details: StreamDetails,
        first: Mapping[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        """示例不需要额外前文。"""
        return []

    async def formatted(
        self,
        details: StreamDetails,
        rows: list[dict[str, Any]],
        zone: ZoneInfo,
    ) -> list[dict[str, object]]:
        """将示例消息转换为日记生成器可读的记录。"""
        return [
            {
                "message_id": str(row["message_id"]),
                "time": datetime.fromtimestamp(float(row["time"]), zone).isoformat(),
                "speaker": "示例参与者",
                "sender_id": "local-user",
                "role": "participant",
                "text": str(row["content"]),
                "message_type": "text",
                "reply_to": None,
            }
            for row in rows
        ]


async def run_example() -> None:
    """验证同日正文替换、跨日归属与游标持久化。"""
    day_one = datetime(2026, 1, 1, 23, 50, tzinfo=UTC).timestamp()
    messages = [
        {
            "id": 1,
            "message_id": "local-message-1",
            "time": day_one,
            "content": "讨论周末安排",
        },
        {
            "id": 2,
            "message_id": "local-message-2",
            "time": day_one + 300,
            "content": "决定周六出发",
        },
        {
            "id": 3,
            "message_id": "local-message-3",
            "time": day_one + 900,
            "content": "确认集合时间",
        },
    ]
    details = StreamDetails("local-group", "local", "group", "local-group", "")
    with TemporaryDirectory(prefix="engram-chat-diary-") as directory:
        database_path = str(Path(directory) / "diary.sqlite")
        config = DiaryConfig(
            database_path=database_path,
            timezone="UTC",
            batch_messages=1,
            context_messages=0,
        )

        async def generate(payload: dict[str, object]) -> str:
            """将底稿和本批消息合并为确定性日记正文。"""
            old_body = str(payload["existing_diary"])
            new_messages = cast(list[dict[str, object]], payload["new_messages"])
            new_text = "；".join(str(message["text"]) for message in new_messages)
            return f"{old_body}；{new_text}" if old_body else new_text

        store = DiaryStore(database_path)
        await store.initialize()
        await store.ensure_stream(
            details.stream_id,
            details.chat_type,
            start_time=day_one,
            bootstrap_through=3,
            now=day_one,
        )
        service = DiaryService(
            config,
            store,
            source=LocalDiarySource(messages),
            generator=generate,
        )
        try:
            for _ in range(3):
                assert await service.process_batch(details, 3, now=day_one + 1800)
            first_day = await store.get_day(details.stream_id, "2026-01-01")
            second_day = await store.get_day(details.stream_id, "2026-01-02")
            assert first_day is not None
            assert first_day.body == "讨论周末安排；决定周六出发"
            assert second_day is not None
            assert second_day.body == "确认集合时间"
        finally:
            await store.close()

        restored_store = DiaryStore(database_path)
        await restored_store.initialize()
        try:
            restored = await restored_store.progress(details.stream_id)
            assert restored is not None and restored.cursor_id == 3
            assert restored.cursor_message_id == "local-message-3"
        finally:
            await restored_store.close()

    print("聊天日记示例通过：同日正文已更新，跨日消息分日保存，重开后游标恢复。")


if __name__ == "__main__":
    asyncio.run(run_example())
