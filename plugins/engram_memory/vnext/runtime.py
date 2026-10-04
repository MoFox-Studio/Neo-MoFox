"""消息来源快照转换、Chroma 派生向量接口与向量投递后台任务。"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from src.app.plugin_system.api import llm_api, person_api
from src.app.plugin_system.types import Message, ModelSet

from .domain import VectorUpsert
from .framework_bridge import (
    ManagedTaskHandle,
    VectorDatabase,
    cancel_managed_task,
    create_managed_task,
    get_vector_database,
)
from .schema import VNextSchema
from .vector_service import VectorIndexService, VectorSink

DEFAULT_EMBEDDING_MODEL_TASK: str = "embedding"
DEFAULT_VECTOR_COLLECTION: str = "engram_vnext_retrieval"
DEFAULT_EMBEDDING_REQUEST_NAME: str = "engram_vnext_embedding"
DEFAULT_VECTOR_DB_PATH: str = "data/chroma_db"

MessageLike = Message | Mapping[str, object]


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    """携带消息身份、时间、正文、发言者与来源字段的只读快照。"""

    message_id: str
    stream_id: str
    time: datetime
    text: str
    speaker: str | None
    snapshot: Mapping[str, object]


class VectorSinkError(RuntimeError):
    """向量生成、格式校验或派生索引写入失败。"""


def _normalize_datetime(value: object) -> datetime:
    """将消息时间转换为带时区的 UTC 时间。"""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("message.time 不是有效的时间值") from error
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        raise ValueError("message.time 必须是 datetime、Unix timestamp 或 ISO 文本")

    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise ValueError("message.time 必须是有限时间戳")
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _normalize_text(value: object) -> str:
    """将消息字段转换为去除首尾空白的文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _snapshot_value(value: object) -> object:
    """将公开消息字段转换为可序列化的快照值。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return _snapshot_value(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _snapshot_value(item)
            for key, item in value.items()
            if isinstance(key, (str, int, float, bool))
        }
    if isinstance(value, (list, tuple)):
        return [_snapshot_value(item) for item in value]
    return str(value)


def _message_value(message: MessageLike, field: str, default: object = None) -> object:
    """读取公开消息对象或消息映射的动态字段。"""
    if isinstance(message, Mapping):
        return message.get(field, default)
    return getattr(message, field, default)


def message_to_snapshot(message: MessageLike) -> MessageSnapshot:
    """转换消息为来源快照，校验身份、聊天流、时间与有效正文。

    正文优先使用处理后的纯文本，发言者优先使用名称、群名片和账号。
    人物身份取自消息或附加字段；缺少身份时通过平台与账号生成核心人物 ID。
    """
    if message is None:
        raise ValueError("message 不能为空")

    message_id = _normalize_text(
        _message_value(message, "message_id") or _message_value(message, "id")
    )
    stream_id = _normalize_text(_message_value(message, "stream_id"))
    text = _normalize_text(
        _message_value(message, "processed_plain_text")
    ) or _normalize_text(_message_value(message, "content"))
    if not message_id:
        raise ValueError("message.message_id 不能为空")
    if not stream_id:
        raise ValueError("message.stream_id 不能为空")
    if not text:
        raise ValueError("message 必须包含有效文本")

    speaker = (
        _normalize_text(_message_value(message, "sender_name"))
        or _normalize_text(_message_value(message, "sender_cardname"))
        or _normalize_text(_message_value(message, "sender_id"))
        or None
    )
    message_time = _normalize_datetime(_message_value(message, "time"))
    person_id = _normalize_text(_message_value(message, "person_id"))
    if not person_id:
        extra = _message_value(message, "extra")
        if isinstance(extra, Mapping):
            person_id = _normalize_text(extra.get("person_id"))
    sender_id = _normalize_text(_message_value(message, "sender_id"))
    platform = _normalize_text(_message_value(message, "platform"))
    sender_role = _normalize_text(_message_value(message, "sender_role")).casefold()
    if sender_role == "bot" or sender_id == "bot":
        person_id = "bot"
    elif not person_id and platform and sender_id and sender_id != "system":
        person_id = person_api.generate_person_id(platform, sender_id)
    snapshot = {
        "message_id": message_id,
        "stream_id": stream_id,
        "time": message_time.isoformat(),
        "person_id": person_id or None,
        "speaker_is_bot": person_id.casefold() == "bot",
        "sender_role": sender_role or None,
        "sender_id": _snapshot_value(_message_value(message, "sender_id")),
        "sender_name": _snapshot_value(_message_value(message, "sender_name")),
        "sender_cardname": _snapshot_value(_message_value(message, "sender_cardname")),
        "platform": _snapshot_value(_message_value(message, "platform")),
        "message_type": _snapshot_value(_message_value(message, "message_type")),
        "reply_to": _snapshot_value(_message_value(message, "reply_to")),
        "content": _snapshot_value(_message_value(message, "content")),
        "processed_plain_text": _snapshot_value(
            _message_value(message, "processed_plain_text") or text
        ),
    }
    return MessageSnapshot(
        message_id=message_id,
        stream_id=stream_id,
        time=message_time,
        text=text,
        speaker=speaker,
        snapshot=snapshot,
    )


def _validate_vector_item(item: VectorUpsert) -> None:
    """校验派生向量入口所需的正式记忆字段。"""
    if not isinstance(item, VectorUpsert):
        raise TypeError("vector sink 只接受 VectorUpsert")
    for field_name, value in (
        ("entry_id", item.entry_id),
        ("memory_id", item.memory_id),
        ("text", item.text),
        ("content_hash", item.content_hash),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"VectorUpsert.{field_name} 不能为空")
    if item.revision_id is not None and not isinstance(item.revision_id, str):
        raise ValueError("VectorUpsert.revision_id 必须是字符串或 None")


class ChromaVectorSink(VectorSink):
    """通过公开向量接口写入由正式记忆检索入口派生的文档与元数据。

    更新先删除相同 ID 再添加文档；写入失败由向量投递服务处理。
    """

    def __init__(
        self,
        db_path: str = DEFAULT_VECTOR_DB_PATH,
        *,
        collection_name: str = DEFAULT_VECTOR_COLLECTION,
        embedding_task: str = DEFAULT_EMBEDDING_MODEL_TASK,
        request_name: str = DEFAULT_EMBEDDING_REQUEST_NAME,
        model_set: ModelSet | None = None,
        vector_db: VectorDatabase | None = None,
    ) -> None:
        """绑定集合、模型任务与可选向量数据库，延迟建立外部连接。"""
        if not isinstance(db_path, str) or not db_path.strip():
            raise ValueError("db_path 不能为空")
        if not isinstance(collection_name, str) or not collection_name.strip():
            raise ValueError("collection_name 不能为空")
        if not isinstance(embedding_task, str) or not embedding_task.strip():
            raise ValueError("embedding_task 不能为空")
        if not isinstance(request_name, str) or not request_name.strip():
            raise ValueError("request_name 不能为空")
        self._db_path = db_path.strip()
        self._collection_name = collection_name.strip()
        self._embedding_task = embedding_task.strip()
        self._request_name = request_name.strip()
        self._model_set = model_set
        self._vector_db = vector_db

    @property
    def collection_name(self) -> str:
        """返回派生索引的物理集合名。"""
        return self._collection_name

    @property
    def supports_batch_upsert(self) -> bool:
        """返回 Chroma 写入端是否支持一次请求写入多条入口。"""
        return True

    @staticmethod
    def collection_name_for(
        index_id: str,
        embedding_model_id: str,
        embedding_dimension: int,
    ) -> str:
        """由索引身份、模型身份和向量维度生成长度受限的集合名。"""
        import hashlib

        if (
            not index_id.strip()
            or not embedding_model_id.strip()
            or embedding_dimension <= 0
        ):
            raise ValueError("索引物理参数无效")
        model_token = hashlib.sha256(embedding_model_id.encode("utf-8")).hexdigest()[
            :12
        ]
        index_token = hashlib.sha256(index_id.encode("utf-8")).hexdigest()[:12]
        return f"engram_vnext_{index_token}_{model_token}_{embedding_dimension}"

    def for_index(
        self,
        index_id: str,
        embedding_model_id: str,
        embedding_dimension: int,
    ) -> ChromaVectorSink:
        """构造使用指定索引物理集合的向量接口。"""
        return ChromaVectorSink(
            db_path=self._db_path,
            collection_name=self.collection_name_for(
                index_id, embedding_model_id, embedding_dimension
            ),
            embedding_task=self._embedding_task,
            request_name=self._request_name,
            model_set=self._model_set,
            vector_db=self._vector_db,
        )

    async def entry_ids(self) -> frozenset[str] | None:
        """读取物理集合中的全部入口 ID，集合不可读时返回未知状态。"""
        try:
            result = await self._get_vector_db().get(
                collection_name=self._collection_name,
                include=["metadatas"],
            )
        except Exception:
            return None
        ids = result.get("ids") if isinstance(result, Mapping) else None
        if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes, bytearray)):
            return None
        return frozenset(str(item) for item in ids if str(item).strip())

    @staticmethod
    def derived_metadata(item: VectorUpsert) -> dict[str, str]:
        """由正式检索入口的身份与内容摘要构造标量元数据。"""
        _validate_vector_item(item)
        metadata: dict[str, str] = {
            "entry_id": item.entry_id,
            "memory_id": item.memory_id,
            "content_hash": item.content_hash,
        }
        if item.revision_id:
            metadata["revision_id"] = item.revision_id
        return metadata

    def _get_vector_db(self) -> VectorDatabase:
        """取得注入或缓存的公开向量数据库接口。"""
        if self._vector_db is None:
            self._vector_db = get_vector_database(self._db_path)
        return self._vector_db

    def embedding_model_identity(self) -> str:
        """从当前模型任务解析唯一 Embedding 模型标识。"""
        model_set = self._model_set or llm_api.get_model_set_by_task(
            self._embedding_task
        )
        if len(model_set) != 1 or not isinstance(model_set[0], Mapping):
            raise VectorSinkError("Embedding task 必须恰好配置一个模型")
        identity = model_set[0].get("model_identifier")
        if not isinstance(identity, str) or not identity.strip():
            raise VectorSinkError("Embedding task 模型缺少 model_identifier")
        return identity.strip()

    async def inspect_embedding_settings(self) -> tuple[str, int]:
        """返回模型任务标识与一次真实响应的向量维度。"""
        identity = self.embedding_model_identity()
        embedding = await self.embed_text("engram-vnext-runtime-preflight")
        return identity, len(embedding)

    async def embed_text(self, text: str) -> list[float]:
        """通过公开模型接口生成一条文本向量。"""
        return (await self.embed_texts((text,)))[0]

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """通过一次公开 LLM 请求生成一批按输入顺序排列的向量。"""
        if not texts or any(
            not isinstance(text, str) or not text.strip() for text in texts
        ):
            raise ValueError("embedding texts 只能包含非空文本")
        try:
            model_set = self._model_set or llm_api.get_model_set_by_task(
                self._embedding_task
            )
            request = llm_api.create_embedding_request(
                model_set,
                request_name=self._request_name,
                inputs=list(texts),
            )
            send_result = request.send()
            response: object = (
                await send_result if inspect.isawaitable(send_result) else send_result
            )
            embeddings = getattr(response, "embeddings", None)
        except Exception as error:  # noqa: BLE001
            raise VectorSinkError("embedding 请求失败") from error

        if (
            not isinstance(embeddings, Sequence)
            or isinstance(embeddings, (str, bytes, bytearray))
            or not embeddings
        ):
            raise VectorSinkError("embedding 请求返回为空")
        if len(embeddings) != len(texts):
            raise VectorSinkError("embedding 返回数量与输入数量不一致")
        vectors: list[list[float]] = []
        for embedding in embeddings:
            if (
                not isinstance(embedding, Sequence)
                or isinstance(embedding, (str, bytes, bytearray))
                or not embedding
            ):
                raise VectorSinkError("embedding 返回的向量格式无效")
            vector: list[float] = []
            for value in embedding:
                if isinstance(value, bool):
                    raise VectorSinkError("embedding 向量包含非法数值")
                try:
                    numeric = float(value)
                except (TypeError, ValueError) as error:
                    raise VectorSinkError("embedding 向量包含非法数值") from error
                if not math.isfinite(numeric):
                    raise VectorSinkError("embedding 向量包含非有限数值")
                vector.append(numeric)
            vectors.append(vector)
        return vectors

    async def query_entries(self, text: str, top_k: int) -> tuple[str, ...]:
        """查询派生向量入口 ID，索引不可用时返回空结果。"""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("query text 不能为空")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k 必须是正整数")
        try:
            embedding = await self.embed_text(text)
            result = await self._get_vector_db().query(
                collection_name=self._collection_name,
                query_embeddings=[embedding],
                n_results=top_k,
                include=["metadatas"],
            )
        except Exception:
            return ()
        rows = result.get("ids") if isinstance(result, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
            return ()
        return tuple(str(entry_id) for entry_id in rows[0] if str(entry_id).strip())

    async def query_scored_entries(
        self, text: str, top_k: int
    ) -> tuple[tuple[str, float], ...]:
        """用现有入口向量计算余弦相似度，不依赖集合的距离度量。"""
        from .retrieval_service import EmbeddingVectorBackend

        if not isinstance(text, str) or not text.strip():
            raise ValueError("query text 不能为空")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k 必须是正整数")
        embedding = await self.embed_text(text)
        result = await self._get_vector_db().query(
            collection_name=self._collection_name,
            query_embeddings=[embedding],
            n_results=top_k,
            include=["embeddings"],
        )
        rows = result.get("ids")
        vectors = result.get("embeddings")
        if not rows or vectors is None or len(vectors) == 0 or vectors[0] is None:
            return ()
        scores = tuple(
            (str(entry_id), EmbeddingVectorBackend._cosine(embedding, vector))
            for entry_id, vector in zip(rows[0], vectors[0], strict=True)
            if str(entry_id).strip()
        )
        return tuple(sorted(scores, key=lambda item: (-item[1], item[0])))

    async def upsert(self, item: VectorUpsert) -> None:
        """从正式检索入口生成并替换一条向量文档。"""
        await self.upsert_many((item,))

    async def upsert_many(self, items: Sequence[VectorUpsert]) -> None:
        """通过一次 embedding 请求批量替换同一物理集合中的入口。"""
        if not items:
            return
        for item in items:
            _validate_vector_item(item)
        embeddings = await self.embed_texts(tuple(item.text for item in items))
        vector_db = self._get_vector_db()
        try:
            await vector_db.delete(
                collection_name=self._collection_name,
                ids=[item.entry_id for item in items],
            )
            await vector_db.add(
                collection_name=self._collection_name,
                embeddings=embeddings,
                documents=[item.text for item in items],
                metadatas=[self.derived_metadata(item) for item in items],
                ids=[item.entry_id for item in items],
            )
        except Exception as error:  # noqa: BLE001
            raise VectorSinkError("向量派生 upsert 失败") from error

    async def delete(self, entry_id: str) -> None:
        """删除指定派生向量入口，不修改正式记忆。"""
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ValueError("entry_id 不能为空")
        try:
            await self._get_vector_db().delete(
                collection_name=self._collection_name,
                ids=[entry_id.strip()],
            )
        except Exception as error:  # noqa: BLE001
            raise VectorSinkError("向量派生 delete 失败") from error


@runtime_checkable
class VectorIndexServiceProtocol(Protocol):
    """向量投递后台任务所需的服务协议。"""

    async def process_pending_outbox(self, limit: int = 20) -> tuple[str, ...]:
        """处理待投递入口并返回成功写入的 ID。"""
        ...

    async def retry_failed_outbox(self, limit: int = 20) -> tuple[str, ...]:
        """显式重试失败的向量投递。"""
        ...


class VectorOutboxWorker:
    """以托管后台任务轮询正式记忆的派生向量投递。"""

    def __init__(
        self,
        service_or_schema: VectorIndexServiceProtocol | VNextSchema,
        sink: VectorSink | None = None,
        *,
        batch_size: int = 20,
        poll_interval_seconds: float = 60.0,
        retry_failed: bool = False,
        task_name: str = "engram_vnext_vector_outbox_worker",
    ) -> None:
        """绑定向量服务或数据库与向量接口，自动轮询仅处理待投递入口。

        失败入口仅由显式修复操作重试，后台任务遵循服务的尝试次数限制。
        """
        if isinstance(service_or_schema, VNextSchema):
            if sink is None:
                raise ValueError("使用 VNextSchema 时必须提供 sink")
            service: VectorIndexServiceProtocol = VectorIndexService(
                service_or_schema,
                sink,
            )
        elif isinstance(service_or_schema, VectorIndexServiceProtocol):
            if sink is not None:
                raise ValueError("使用 VectorIndexService 时不能再次传入 sink")
            service = service_or_schema
        else:
            raise TypeError(
                "service_or_schema 必须实现 VectorIndexServiceProtocol 或是 VNextSchema"
            )
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or batch_size <= 0
        ):
            raise ValueError("batch_size 必须是大于 0 的整数")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds 必须大于 0")
        if not isinstance(task_name, str) or not task_name.strip():
            raise ValueError("task_name 不能为空")
        self._service: VectorIndexServiceProtocol = service
        self._batch_size = batch_size
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._task_name = task_name.strip()
        self._stop_event: asyncio.Event | None = None
        self._task_info: ManagedTaskHandle | None = None
        self._last_error: Exception | None = None

    @property
    def task_info(self) -> ManagedTaskHandle | None:
        """返回当前托管后台任务的句柄。"""
        return self._task_info

    @property
    def last_error(self) -> Exception | None:
        """返回最近一次轮询的异常。"""
        return self._last_error

    async def run_once(self) -> tuple[str, ...]:
        """执行一次投递并返回成功写入的入口 ID。"""
        return await self._service.process_pending_outbox(limit=self._batch_size)

    async def run_forever(self) -> None:
        """轮询投递直至收到停止信号，并保留最近一次异常。"""
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        stop_event = self._stop_event
        while not stop_event.is_set():
            try:
                await self.run_once()
                self._last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                self._last_error = error
            if stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    def start(self) -> ManagedTaskHandle:
        """幂等启动托管后台投递任务。"""
        if self._task_info is not None and self._task_info.task is not None:
            if not self._task_info.task.done():
                return self._task_info
        self._stop_event = asyncio.Event()
        self._task_info = create_managed_task(
            self.run_forever(),
            name=self._task_name,
            daemon=True,
        )
        return self._task_info

    async def stop(self) -> None:
        """停止并等待后台投递任务，清除托管句柄。"""
        if self._stop_event is not None:
            self._stop_event.set()
        task_info = self._task_info
        if task_info is None or task_info.task is None:
            return
        if not task_info.task.done():
            cancel_managed_task(task_info.task_id)
        await asyncio.gather(task_info.task, return_exceptions=True)
        self._task_info = None


__all__: list[str] = [
    "DEFAULT_EMBEDDING_MODEL_TASK",
    "DEFAULT_VECTOR_COLLECTION",
    "DEFAULT_EMBEDDING_REQUEST_NAME",
    "DEFAULT_VECTOR_DB_PATH",
    "MessageSnapshot",
    "VectorSinkError",
    "message_to_snapshot",
    "ChromaVectorSink",
    "VectorOutboxWorker",
    "VectorIndexServiceProtocol",
]
