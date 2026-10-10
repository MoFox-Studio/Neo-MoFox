"""保存实际引用消息的去重副本，供候选和正式记忆长期核对来源。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.plugin_system.api import person_api

from .domain import EvidenceInput, EvidenceMessageInput
from .framework_bridge import read_message_snapshots
from .models import EvidenceMessageSnapshotModel
from .schema import VNextSchema

MessageReader = Callable[
    [tuple[tuple[str, str], ...]], Awaitable[tuple[dict[str, object], ...]]
]


async def _read_source_messages(
    references: tuple[tuple[str, str], ...],
) -> tuple[dict[str, object], ...]:
    """通过插件的只读消息桥接读取精确来源。"""
    return tuple(item.to_dict() for item in await read_message_snapshots(references))


def _json_value(value: Any) -> Any:
    """把来源消息中的时间及容器转换为可持久化 JSON。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("来源消息时间必须带时区")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("来源消息包含不可持久化内容")


class EvidenceService:
    """在调用方事务中保存消息副本，不提供普通 Actor 任意消息查询。"""

    def __init__(
        self, schema: VNextSchema, message_reader: MessageReader | None = None
    ) -> None:
        """绑定插件数据库及精确来源读取函数。"""
        self._schema = schema
        self._message_reader = message_reader or _read_source_messages

    async def read_messages(
        self, references: tuple[tuple[str, str], ...]
    ) -> tuple[dict[str, object], ...]:
        """仅返回已保存副本；调用方须先验证关联记忆或候选权限。"""
        keys = tuple(dict.fromkeys(references))
        if not keys:
            return ()
        async with self._schema.database.session() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(EvidenceMessageSnapshotModel).where(
                            tuple_(
                                EvidenceMessageSnapshotModel.stream_id,
                                EvidenceMessageSnapshotModel.message_id,
                            ).in_(keys)
                        )
                    )
                ).all()
            )
        by_key = {(row.stream_id, row.message_id): row for row in rows}
        result: list[dict[str, object]] = []
        for key in keys:
            row = by_key.get(key)
            if row is None:
                continue
            payload = dict(row.payload)
            payload.update(
                message_id=row.message_id,
                stream_id=row.stream_id,
                source="evidence_snapshot",
                captured_at=row.captured_at.isoformat(),
                redacted=row.redacted_at is not None,
            )
            if (
                not payload.get("person_id")
                and payload.get("speaker_is_bot") is False
                and str(payload.get("sender_role") or "").casefold() != "bot"
                and payload.get("platform")
                and payload.get("sender_id")
                and payload.get("sender_id") not in {"bot", "system"}
            ):
                payload["person_id"] = person_api.generate_person_id(
                    str(payload["platform"]),
                    str(payload["sender_id"]),
                )
            # person_id 只标识发送账号，不证明发送者是真人。
            if (
                payload.get("speaker_is_bot") is True
                or str(payload.get("sender_role") or "").casefold() == "bot"
                or str(payload.get("speaker_kind") or "").upper() == "BOT"
                or payload.get("person_id") == "bot"
            ):
                payload["speaker_kind"] = "BOT"
            elif payload.get("person_id"):
                payload["speaker_kind"] = "ACCOUNT"
            else:
                payload["speaker_kind"] = "UNKNOWN"
            result.append(payload)
        return tuple(result)

    async def prepare_evidence(
        self, evidence: tuple[EvidenceInput, ...]
    ) -> tuple[EvidenceInput, ...]:
        """补齐被引用消息的真实快照，缺失或已隐私删除时拒绝新写入。"""
        references = tuple(
            dict.fromkeys(
                (message.stream_id, message.message_id)
                for item in evidence
                for message in item.messages
            )
        )
        if not references:
            return evidence
        if any(not stream or not message for stream, message in references):
            raise ValueError("新证据必须指定准确的 stream_id 和 message_id")
        saved = {
            (str(row["stream_id"]), str(row["message_id"])): row
            for row in await self.read_messages(references)
        }
        supplied = {
            (message.stream_id, message.message_id): message.snapshot
            for item in evidence
            for message in item.messages
            if message.snapshot is not None
        }
        missing = tuple(
            key for key in references if key not in saved and key not in supplied
        )
        needs_reply_metadata = tuple(
            key
            for key in references
            if not (saved.get(key) or {}).get("redacted")
            and "reply_to" not in (saved.get(key) or {})
            and "reply_to" not in (supplied.get(key) or {})
        )
        fetch_references = tuple(dict.fromkeys((*missing, *needs_reply_metadata)))
        fetched = (
            {
                (str(row.get("stream_id") or ""), str(row.get("message_id") or "")): row
                for row in await self._message_reader(fetch_references)
            }
            if fetch_references
            else {}
        )
        payloads: dict[tuple[str, str], dict[str, object]] = {}
        for key in references:
            saved_payload = saved.get(key)
            supplied_payload = supplied.get(key)
            fetched_payload = fetched.get(key)
            source_payload = saved_payload or supplied_payload or fetched_payload
            if source_payload is None:
                raise ValueError("引用的原始消息不存在，无法保存可核对来源")
            payload = dict(source_payload)
            if payload.get("redacted"):
                raise ValueError("引用的来源已隐私删除，不允许恢复或重新使用")
            if "reply_to" not in payload:
                for candidate in (supplied_payload, fetched_payload):
                    if (
                        candidate is not None
                        and not candidate.get("redacted")
                        and "reply_to" in candidate
                    ):
                        payload["reply_to"] = candidate["reply_to"]
                        break
            if (
                str(payload.get("stream_id") or ""),
                str(payload.get("message_id") or ""),
            ) != key:
                raise ValueError("来源快照与引用消息标识不一致")
            if payload.get("time") is None:
                raise ValueError("来源快照缺少消息时间")
            if not any(
                payload.get(field)
                for field in ("sender_id", "person_id", "sender_name", "speaker")
            ):
                raise ValueError("来源快照缺少发送者")
            if not any(
                payload.get(field)
                for field in ("processed_plain_text", "content", "text")
            ):
                raise ValueError("来源快照缺少理解记忆所需的消息内容")
            payloads[key] = _json_value(payload)
        return tuple(
            replace(
                item,
                messages=tuple(
                    replace(
                        message,
                        snapshot=payloads[(message.stream_id, message.message_id)],
                    )
                    for message in item.messages
                ),
            )
            for item in evidence
        )

    async def persist_snapshots(
        self, session: AsyncSession, messages: tuple[EvidenceMessageInput, ...]
    ) -> None:
        """首次保存快照，并仅补齐既有快照缺失的回复目标元数据。"""
        for message in messages:
            if message.snapshot is None:
                raise ValueError("消息证据尚未准备长期副本")
            await session.execute(
                insert(EvidenceMessageSnapshotModel)
                .values(
                    stream_id=message.stream_id,
                    message_id=message.message_id,
                    payload=_json_value(message.snapshot),
                    captured_at=datetime.now(UTC),
                    redacted_at=None,
                )
                .on_conflict_do_nothing(index_elements=["stream_id", "message_id"])
            )
            saved = await session.get(
                EvidenceMessageSnapshotModel, (message.stream_id, message.message_id)
            )
            if saved is None or saved.redacted_at is not None:
                raise ValueError("来源副本不可用或已隐私删除")
            payload = _json_value(message.snapshot)
            if "reply_to" not in saved.payload and "reply_to" in payload:
                saved.payload = {**saved.payload, "reply_to": payload["reply_to"]}

    async def redact_messages(self, references: tuple[tuple[str, str], ...]) -> int:
        """管理端显式隐私删除消息正文和人物信息，保留不可恢复标记。"""
        keys = _normalized_references(references)
        if not keys:
            return 0
        async with self._schema.database.session() as session:
            result = await session.execute(
                update(EvidenceMessageSnapshotModel)
                .where(
                    tuple_(
                        EvidenceMessageSnapshotModel.stream_id,
                        EvidenceMessageSnapshotModel.message_id,
                    ).in_(keys)
                )
                .values(payload={"redacted": True}, redacted_at=datetime.now(UTC))
            )
        return result.rowcount

    async def synchronize_redactions_from(self, source: EvidenceService) -> int:
        """从来源库同步隐私删除标记，防止备份恢复后重新暴露已删除来源。"""
        async with source._schema.database.session() as session:
            tombstones = tuple(
                (
                    await session.execute(
                        select(
                            EvidenceMessageSnapshotModel.stream_id,
                            EvidenceMessageSnapshotModel.message_id,
                            EvidenceMessageSnapshotModel.captured_at,
                            EvidenceMessageSnapshotModel.redacted_at,
                        ).where(EvidenceMessageSnapshotModel.redacted_at.is_not(None))
                    )
                ).all()
            )
        async with self._schema.database.session() as session:
            for row in tombstones:
                await session.execute(
                    insert(EvidenceMessageSnapshotModel)
                    .values(
                        stream_id=row.stream_id,
                        message_id=row.message_id,
                        captured_at=row.captured_at,
                        payload={"redacted": True},
                        redacted_at=row.redacted_at,
                    )
                    .on_conflict_do_update(
                        index_elements=["stream_id", "message_id"],
                        set_={
                            "payload": {"redacted": True},
                            "redacted_at": row.redacted_at,
                        },
                    )
                )
        return len(tombstones)


def _normalized_references(
    references: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """按原顺序返回去重后的非空来源引用。"""
    return tuple(
        dict.fromkeys(
            (stream_id.strip(), message_id.strip())
            for stream_id, message_id in references
            if isinstance(stream_id, str)
            and stream_id.strip()
            and isinstance(message_id, str)
            and message_id.strip()
        )
    )
