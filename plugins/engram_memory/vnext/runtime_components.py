"""正式记忆的查询、写操作、人物更新事件与闪回组件。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.api.event_api import EventDecision
from src.app.plugin_system.base import (
    BaseAction,
    BaseEventHandler,
    BaseRouter,
    BaseService,
    BaseTool,
)
from src.app.plugin_system.types import EventType

from .domain import (
    CreateMemoryInput,
    EvidenceInput,
    EvidenceMessageInput,
    MemoryChanged,
    ParticipantInput,
    ReviseMemoryInput,
    SubjectInput,
)
from .enums import (
    ActorType,
    EvidenceSourceType,
    MemoryKind,
    ParticipantKind,
    RevisionChangeReason,
    SubjectKind,
)
from .runtime import message_to_snapshot
from .tool_service import ToolContext

if TYPE_CHECKING:
    from .runtime_owner import VNextRuntimeOwner


def _owner(plugin: Any) -> VNextRuntimeOwner:
    """取得插件实例持有的运行服务。"""
    from .runtime_owner import VNextRuntimeOwner

    runtime = getattr(plugin, "runtime_owner", None)
    if not isinstance(runtime, VNextRuntimeOwner):
        raise RuntimeError("Engram Memory 尚未初始化")
    return runtime


def _value(message: object, field: str) -> object:
    """读取公开消息对象或消息映射的动态字段。"""
    return (
        message.get(field)
        if isinstance(message, Mapping)
        else getattr(message, field, None)
    )


def _actor_context(component: BaseAction | BaseTool) -> ToolContext:
    """从框架绑定的聊天流与消息构造调用身份。"""
    if isinstance(component, BaseAction):
        stream = component.chat_stream
        messages = [*stream.context.history_messages, *stream.context.unread_messages]
        message = messages[-1] if messages else None
        stream_id = stream.stream_id
    else:
        message = component.trigger_message
        stream_id = component.get_current_stream_id()
    platform = str(_value(message, "platform") or "").strip()
    sender_id = str(_value(message, "sender_id") or "").strip()
    return ToolContext(
        actor_type=ActorType.ACTOR,
        actor_ref=f"{platform}:{sender_id}"
        if platform and sender_id
        else sender_id or None,
        stream_id=stream_id or None,
    )


def _text(value: object, field: str) -> str:
    """读取必填的非空文本。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value.strip()


def _ids(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    """读取不重复的准确标识数组。"""
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"{field} 必须是{'非空' if required else ''}字符串数组")
    values = tuple(_text(item, field) for item in value)
    if len(set(values)) != len(values):
        raise ValueError(f"{field} 不能重复")
    return values


def _optional_datetime(value: str | None, field: str) -> datetime | None:
    """解析带时区的可选 ISO 时间。"""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 必须包含时区")
    return parsed.astimezone(UTC)


class SourceSelectionError(ValueError):
    """来源选择失败，并携带当前聊天的准确消息目录。"""

    def __init__(self, message: str, sources: list[dict[str, object]]) -> None:
        """保存错误原因与可用来源。"""
        super().__init__(message)
        self.sources = sources


async def _action_source(
    action: BaseAction,
    payload: dict[str, object],
) -> tuple[ToolContext, EvidenceInput]:
    """校验当前流的消息来源并保存原始快照及群私聊信息。"""
    context = _actor_context(action)
    if not context.stream_id:
        raise ValueError("当前聊天流未绑定")
    stream = action.chat_stream
    available = {}
    for message in [*stream.context.history_messages, *stream.context.unread_messages]:
        if str(_value(message, "stream_id") or "") != context.stream_id:
            continue
        try:
            snapshot = message_to_snapshot(message)
        except ValueError:
            continue
        available[snapshot.message_id] = snapshot
    sources = [
        {
            "message_id": item.message_id,
            "time": item.time.isoformat(),
            "person_id": item.snapshot.get("person_id"),
            "speaker": item.speaker,
            "content": item.text,
        }
        for item in available.values()
    ]
    try:
        source_ids = _ids(
            payload.get("source_message_ids"), "source_message_ids", required=True
        )
    except ValueError as error:
        raise SourceSelectionError(str(error), sources) from error
    if any(message_id not in available for message_id in source_ids):
        raise SourceSelectionError(
            "来源 ID 不属于当前聊天上下文，请使用准确的消息 ID", sources
        )
    selected = tuple(available[message_id] for message_id in source_ids)
    evidence = EvidenceInput(
        source_type=EvidenceSourceType.ACTOR_WRITE,
        observed_at=max(item.time for item in selected),
        messages=tuple(
            EvidenceMessageInput(
                message_id=item.message_id,
                stream_id=item.stream_id,
                snapshot={**item.snapshot, "chat_type": stream.context.chat_type},
            )
            for item in selected
        ),
        note=str(payload.get("reason") or "").strip() or None,
    )
    operation = json.dumps(
        {"action": action.name, "stream_id": context.stream_id, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    return ToolContext(
        actor_type=context.actor_type,
        actor_ref=context.actor_ref,
        stream_id=context.stream_id,
        evidence_message_ids=source_ids,
        operation_key=sha256(operation.encode("utf-8")).hexdigest(),
    ), evidence


async def _people(
    action: BaseAction,
    payload: dict[str, object],
) -> tuple[SubjectInput, tuple[ParticipantInput, ...]]:
    """校验准确人物身份；被提及的人不必是来源消息的发言者。"""
    primary_id = _text(payload.get("primary_person_id"), "primary_person_id")
    secondary_ids = _ids(payload.get("secondary_person_ids"), "secondary_person_ids")
    service = _owner(action.plugin).persona_service
    people = []
    for person_id in (primary_id, *secondary_ids):
        person = await service.get_core_person(person_id)
        if person is None:
            raise ValueError(
                "人物 ID 未对应到核心人物记录；请先查询人物，不能用昵称代替 ID"
            )
        people.append(person.person_id)
    if len(set(people)) != len(people):
        raise ValueError("主次人物不能重复或指向同一人物")
    return SubjectInput(SubjectKind.PERSON, person_id=people[0]), tuple(
        ParticipantInput(ParticipantKind.PERSON, person_id=person_id)
        for person_id in people[1:]
    )


def _action_result(error: ValueError) -> tuple[bool, str]:
    """将输入错误和可选来源目录返回给调用模型。"""
    result: dict[str, object] = {"error": str(error)}
    if isinstance(error, SourceSelectionError):
        result["source_messages"] = error.sources
    return False, json.dumps(result, ensure_ascii=False)


_PERSON_FIELDS: dict[str, object] = {
    "primary_person_id": {
        "type": "string",
        "description": "这条记忆主要关于谁，使用查询所得准确人物 ID；不一定是发言者。",
    },
    "secondary_person_ids": {
        "type": "array",
        "items": {"type": "string"},
        "uniqueItems": True,
        "description": "其他相关人物的准确 ID；不能重复主要人物。",
    },
    "source_message_ids": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "uniqueItems": True,
        "description": "支持正文的当前聊天消息 ID，包括必要的转述或指代上下文。",
    },
    "content": {
        "type": "string",
        "description": "自然记清值得记住的事情与聊天背景，区分亲历、转述、计划与不确定判断；不把私下透露写成大家已经知道的事实。",
    },
    "memory_kind": {"type": "string", "enum": [kind.value for kind in MemoryKind]},
}


class VNextMemorySearchTool(BaseTool):
    """查询可按人物、类型和时间筛选的正式记忆。"""

    name = "memory_search"
    description = (
        "搜索正式记忆，帮你想起相关的人和事；结果是线索目录，可用 memory_read 核对，不必在回复里复述。"
        "同一人物或经历已有记忆时优先修订，不重复创建。"
    )

    async def execute(
        self,
        query: str,
        person_ids: list[str] | None = None,
        memory_kinds: list[str] | None = None,
        limit: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> tuple[bool, str | dict[str, object]]:
        """按语义、人物及可选时间范围检索记忆。"""
        result = await _owner(self.plugin).tools.memory_search(
            query,
            _actor_context(self),
            person_ids=tuple(person_ids or ()),
            memory_kinds=tuple(memory_kinds or ()),
            limit=limit,
            start_time=_optional_datetime(start_time, "start_time"),
            end_time=_optional_datetime(end_time, "end_time"),
        )
        return True, {"memories": list(result)}


class VNextMemoryReadTool(BaseTool):
    """读取正式记忆、版本历史及来源。"""

    name = "memory_read"
    description = (
        "通过 Memory ID 回读 current 正文、history 历史版本或 full 来源与审计，帮你核对记得的事情。"
        "回应时仍顾及当时是谁向你说起、现在有哪些人在听，不因读到了就替对方向别人讲出来。"
    )

    async def execute(
        self, memory_id: str, view: str = "current"
    ) -> tuple[bool, str | dict[str, object]]:
        """返回 current、history 或 full 记忆视图。"""
        return True, await _owner(self.plugin).tools.memory_read(
            memory_id, view, _actor_context(self)
        )


class VNextMemoryWriteAction(BaseAction):
    """保存有来源和明确人物关联的正式记忆。"""

    name = "memory_write"
    description = "搜索去重后，记下值得长期保留的自然正文、主次人物与当前聊天来源，供以后回想；保存不是代对方公开。"
    associated_types: list[str] = ["text"]

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """声明创建记忆的结构化参数。"""
        return {
            "type": "function",
            "function": {
                "name": f"action-{cls.name}",
                "description": cls.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "payload": {
                            "type": "object",
                            "properties": _PERSON_FIELDS,
                            "required": [
                                "content",
                                "memory_kind",
                                "primary_person_id",
                                "source_message_ids",
                            ],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["payload"],
                    "additionalProperties": False,
                },
            },
        }

    async def execute(self, payload: dict[str, object]) -> tuple[bool, str]:
        """按 payload 的正文、人物、类型与来源创建记忆并返回准确 ID。"""
        try:
            content = _text(payload.get("content"), "content")
            context, evidence = await _action_source(self, payload)
            subject, participants = await _people(self, payload)
            data = CreateMemoryInput(
                title=content.splitlines()[0][:72],
                content=content,
                memory_kind=MemoryKind(
                    _text(payload.get("memory_kind"), "memory_kind")
                ),
                subject=subject,
                participants=participants,
                observed_at=evidence.observed_at,
                evidence=(evidence,),
            )
            result = await _owner(self.plugin).tools.memory_write(data, context)
        except ValueError as error:
            return _action_result(error)
        return True, json.dumps(result, ensure_ascii=False)


class VNextMemoryReviseAction(BaseAction):
    """在当前版本上修订正文和人物关联，保留旧版本。"""

    name = "memory_revise"
    description = "基于当前 revision 修订同一记忆的正文和人物，可更正、澄清或补充依据；新经历仍应另建记忆。"
    associated_types: list[str] = ["text"]

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """声明线性修订的准确版本、正文、人物及来源。"""
        return {
            "type": "function",
            "function": {
                "name": f"action-{cls.name}",
                "description": cls.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "payload": {
                            "type": "object",
                            "properties": {
                                **_PERSON_FIELDS,
                                "memory_id": {"type": "string"},
                                "based_on_revision_id": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": [
                                "memory_id",
                                "based_on_revision_id",
                                "content",
                                "primary_person_id",
                                "source_message_ids",
                            ],
                            "additionalProperties": False,
                        }
                    },
                    "required": ["payload"],
                    "additionalProperties": False,
                },
            },
        }

    async def execute(self, payload: dict[str, object]) -> tuple[bool, str]:
        """按 payload 修订指定版本，来源时间由原始消息确定。"""
        try:
            memory_id = _text(payload.get("memory_id"), "memory_id")
            content = _text(payload.get("content"), "content")
            context, evidence = await _action_source(self, payload)
            subject, participants = await _people(self, payload)
            owner = _owner(self.plugin)
            current = await owner.repository.get_current_revision(memory_id)
            if current is None:
                raise ValueError("Memory 不存在或当前版本缺失")
            data = ReviseMemoryInput(
                memory_id=memory_id,
                based_on_revision_id=_text(
                    payload.get("based_on_revision_id"), "based_on_revision_id"
                ),
                title=content.splitlines()[0][:72],
                content=content,
                memory_kind=MemoryKind(
                    str(payload.get("memory_kind") or current.memory_kind.value)
                ),
                subject=subject,
                participants=participants,
                observed_at=evidence.observed_at,
                change_reason=RevisionChangeReason.CLARIFICATION,
                evidence=(evidence,),
            )
            result = await owner.tools.memory_revise(data, context)
        except ValueError as error:
            return _action_result(error)
        return True, json.dumps(result, ensure_ascii=False)


class VNextMemoryInvalidateAction(BaseAction):
    """将没有有效依据的正式记忆作废，并保留来源和历史。"""

    name = "memory_invalidate"
    description = "依据当前聊天来源作废错误或失效的记忆，不物理删除正文与历史。"
    associated_types: list[str] = ["text"]

    async def execute(
        self, memory_id: str, reason: str, source_message_ids: list[str]
    ) -> tuple[bool, str]:
        """使用准确 Memory ID、作废原因和来源消息记录撤回。"""
        try:
            memory_id = _text(memory_id, "memory_id")
            reason = _text(reason, "reason")
            context, evidence = await _action_source(
                self,
                {
                    "memory_id": memory_id,
                    "reason": reason,
                    "source_message_ids": source_message_ids,
                },
            )
            result = await _owner(self.plugin).tools.memory_invalidate(
                memory_id,
                reason,
                context,
                evidence=(evidence,),
            )
        except ValueError as error:
            return _action_result(error)
        return True, json.dumps(result, ensure_ascii=False)


class VNextPersonLookupTool(BaseTool):
    """查询准确人物身份、当前或历史印象和近期相关记忆。"""

    name = "person_lookup"
    description = (
        "按人物 ID 想起这个人的核心信息、当前印象与近期记忆，帮助你接着相处，不是要把他的情况介绍给旁人。"
        "群聊中跟某个人开始聊天或有人新参与时，先调用本工具读取他的当前印象，再开始回应。"
        "同一段对话已读过的印象可以继续使用；私聊直接使用自动注入的印象，不要求聊天前再调用本工具。"
        "ID 不能用昵称代替。"
        "view=current 读当前，history 列出历史目录，revision 配合 revision_no 读指定历史正文。"
        "历史只是当时的主观认识，不是当前事实或正式记忆依据。"
    )

    async def execute(
        self,
        person_id: str,
        view: str = "current",
        revision_no: int | None = None,
    ) -> tuple[bool, str | dict[str, object]]:
        """按 view 返回当前印象、历史目录或 revision_no 对应的历史正文。"""
        return True, await _owner(self.plugin).tools.person_lookup(
            person_id,
            _actor_context(self),
            view=view,
            revision_no=revision_no,
        )


class VNextMemoryService(BaseService):
    """向其他插件提供带调用身份的记忆查询。"""

    name = "memory_service"
    description = "Engram Memory 正式记忆查询服务。"

    async def search(
        self, query: str, context: ToolContext, limit: int | None = None
    ) -> tuple[dict[str, object], ...]:
        """执行带身份上下文的混合检索。"""
        return await _owner(self.plugin).tools.memory_search(
            query, context, limit=limit
        )

    async def read(
        self, memory_id: str, view: str, context: ToolContext
    ) -> dict[str, object]:
        """读取正式记忆及可选历史与来源。"""
        return await _owner(self.plugin).tools.memory_read(memory_id, view, context)


class VNextMemoryChangedEventHandler(BaseEventHandler):
    """将已提交的记忆变化合并进相关人物的更新队列。"""

    name = "memory_changed"
    description = "正式记忆变化后更新其前后关联人物的印象。"
    init_subscribe = ["engram_memory:memory_changed"]
    timeout = 1.0

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """仅登记人物更新，不在事件处理时等待模型。"""
        change = params.get("change")
        if not isinstance(change, MemoryChanged):
            raise ValueError("memory_changed 事件缺少有效的记忆变化")
        await _owner(self.plugin).persona_updater.enqueue(change)
        return EventDecision.SUCCESS, params


class VNextFlashbackEventHandler(BaseEventHandler):
    """预取相关记忆并在回复前刷新聊天流闪回。"""

    name = "vnext_flashback_injector"
    description = "预取与当前话题相关的正式记忆，刷新当前流的闪回。"
    init_subscribe = [EventType.ON_MESSAGE_RECEIVED, EventType.ON_PROMPT_BUILD]
    timeout = 2.0

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """收到消息时预取，生成回复前注入仍有效的记忆。"""
        owner = _owner(self.plugin)
        if event_name == EventType.ON_MESSAGE_RECEIVED:
            message = params.get("message")
            if message is not None:
                owner.observe_message(message)
            return EventDecision.SUCCESS, params
        if params.get("name") not in {
            "default_chatter_user_prompt",
            "neo_default_chatter_user_prompt",
            "kfc_user_prompt",
        }:
            return EventDecision.SUCCESS, params
        values = params.get("values")
        if not isinstance(values, dict):
            return EventDecision.SUCCESS, params
        stream_id = str(values.get("stream_id") or "").strip()
        if not stream_id:
            return EventDecision.SUCCESS, params
        candidates = await owner.consume_flashback_prefetch(stream_id)
        await self._reconcile_stream_reminders(owner, stream_id, candidates)
        return EventDecision.SUCCESS, params

    async def _reconcile_stream_reminders(
        self, owner: VNextRuntimeOwner, stream_id: str, candidates: tuple[object, ...]
    ) -> None:
        """刷新当前版本，并移除作废或删除的聊天流闪回。"""
        prefix = "engram_memory_flashback_"
        tracked_names = self.plugin._flashback_reminder_streams.setdefault(
            stream_id, set()
        )
        memory_ids = [name.removeprefix(prefix) for name in tracked_names]
        memory_ids.extend(
            str(getattr(item, "memory_id", "") or "") for item in candidates
        )
        normalized_ids = tuple(
            dict.fromkeys(memory_id for memory_id in memory_ids if memory_id)
        )
        if not normalized_ids:
            self.plugin._flashback_reminder_streams.pop(stream_id, None)
            return
        current = await owner.flashback.current_reminder_candidates(normalized_ids)
        for name in tuple(tracked_names):
            item = current.get(name.removeprefix(prefix))
            if item is None:
                prompt_api.delete_stream_reminder(stream_id, "actor", name)
                tracked_names.discard(name)
            else:
                self._upsert_stream_reminder(stream_id, name, item)
        for candidate in candidates:
            memory_id = str(getattr(candidate, "memory_id", "") or "").strip()
            item = current.get(memory_id)
            if item is not None:
                name = f"{prefix}{memory_id}"
                self._upsert_stream_reminder(stream_id, name, item)
                tracked_names.add(name)
        if not tracked_names:
            self.plugin._flashback_reminder_streams.pop(stream_id, None)

    def _upsert_stream_reminder(
        self, stream_id: str, name: str, candidate: Any
    ) -> None:
        """替换闪回正文时保留第一次想起的时间。"""
        rendered = prompt_api.get_stream_reminder(stream_id, "actor", names=[name])
        marker = f"[{name}]\n"
        previous = rendered[len(marker) :] if rendered.startswith(marker) else ""
        recalled_at, separator, previous_block = previous.partition("\n\n")
        current_block = (
            candidate.to_prompt_block()
            .replace("<system_reminder", "&lt;system_reminder")
            .replace("</system_reminder>", "&lt;/system_reminder&gt;")
        )
        if separator and previous_block == current_block:
            return
        if not separator:
            recalled_at = f"想起这段往事的时间：{datetime.now(UTC).isoformat()}"
        prompt_api.add_stream_reminder(
            stream_id=stream_id,
            bucket="actor",
            name=name,
            content=f"{recalled_at}\n\n{current_block}",
            insert_type=prompt_api.SystemReminderInsertType.FIXED,
            consume=prompt_api.SystemReminderConsumeType.FOREVER,
        )


class VNextDoctorRouter(BaseRouter):
    """提供正式记忆及派生索引的一致性检查。"""

    name = "vnext_doctor"
    description = "Engram Memory 记忆、来源与派生索引一致性检查。"
    custom_route_path = "/api/engram-vnext"

    def register_endpoints(self) -> None:
        """注册只读健康检查端点。"""

        @self.app.get("/check")
        async def check() -> dict[str, object]:
            """返回健康状态与可定位的问题目录。"""
            doctor = _owner(self.plugin).doctor
            if doctor is None:
                raise RuntimeError("Engram Doctor 尚未初始化")
            report = await doctor.check()
            return {
                "healthy": report.healthy,
                "issues": [
                    {
                        "code": issue.code,
                        "object_id": issue.object_id,
                        "repairable": issue.repairable,
                        "details": issue.details,
                    }
                    for issue in report.issues
                ],
            }


__all__ = [
    "VNextDoctorRouter",
    "VNextFlashbackEventHandler",
    "VNextMemoryChangedEventHandler",
    "VNextMemoryReadTool",
    "VNextMemoryReviseAction",
    "VNextMemorySearchTool",
    "VNextMemoryService",
    "VNextMemoryWriteAction",
    "VNextMemoryInvalidateAction",
    "VNextPersonLookupTool",
]
