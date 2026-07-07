"""系统提醒存储模块

系统提醒是一种轻量级的、结构化的文本信息，供模型在生成过程中参考。
它们通常包含特定格式的内容块，帮助模型记住重要信息或指导其行为。

设计目标：
- 简单易用：提供直观的接口来设置和获取提醒。
- 灵活性：支持不同的提醒分类（bucket）和命名（name），以适应多样化的使用场景。
- 流隔离：通过 ``stream_id`` 参数支持按聊天流隔离 reminder，实现 per-stream 注入。
- 线程安全：在多线程环境下安全地访问和修改提醒。
- 轻量级：仅在内存中存储，不涉及持久化，以保持高性能和低复杂度。

使用示例:
    from src.core.prompt import get_system_reminder_store

    # 添加全局提醒（stream_id=None，所有聊天流共享）
    store = get_system_reminder_store()
    store.set(bucket="actor", name="goal", content="完成订单处理")
    store.set(bucket="actor", name="constraint", content="只能使用提供的API")

    # 添加流隔离提醒（仅对指定 stream_id 生效）
    store.set(
        bucket="actor",
        name="group_rule",
        content="这是技术群，优先解决技术问题。",
        stream_id="stream_abc123",
    )

    # 获取提醒
    print(store.get("actor"))
    # 输出:
    # [goal]
    # 完成订单处理
    #
    # [constraint]
    # 只能使用提供的API
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Sequence, TypeAlias


class SystemReminderBucket(str, Enum):
    """预定义的系统提醒分类（bucket）。可以根据实际需求扩展更多分类。"""

    ACTOR = "actor"
    SUB_ACTOR = "sub_actor"


class SystemReminderInsertType(str, Enum):
    """system reminder 的插入位置类型。"""

    FIXED = "fixed"
    DYNAMIC = "dynamic"


class SystemReminderConsumeType(str, Enum):
    """system reminder 对单个消费者的可见生命周期。"""

    FOREVER = "forever"
    ONCE = "once"


BucketLike: TypeAlias = str | SystemReminderBucket
InsertTypeLike: TypeAlias = str | SystemReminderInsertType
ConsumeTypeLike: TypeAlias = str | SystemReminderConsumeType


@dataclass(frozen=True, slots=True)
class SystemReminderItem:
    """单条 system reminder 记录。"""

    name: str
    content: str
    insert_type: SystemReminderInsertType
    consume_type: SystemReminderConsumeType = SystemReminderConsumeType.FOREVER

    def render(self) -> str:
        """渲染为注入 LLM 前使用的文本块。"""

        return f"[{self.name}]\n{self.content}"


def _normalize_bucket(bucket: BucketLike) -> str:
    """规范化 bucket 参数，确保其为非空字符串。"""

    if isinstance(bucket, SystemReminderBucket):
        bucket_value = bucket.value
    else:
        bucket_value = bucket

    if not isinstance(bucket_value, str) or not bucket_value.strip():
        raise ValueError("bucket 不能为空")

    return bucket_value.strip()


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def _normalize_insert_type(insert_type: InsertTypeLike) -> SystemReminderInsertType:
    """规范化 insert_type 参数。"""

    if isinstance(insert_type, SystemReminderInsertType):
        return insert_type

    if not isinstance(insert_type, str) or not insert_type.strip():
        raise ValueError("insert_type 不能为空")

    normalized = insert_type.strip().lower()
    try:
        return SystemReminderInsertType(normalized)
    except ValueError as exc:
        raise ValueError("insert_type 只能是 fixed 或 dynamic") from exc


def _normalize_consume_type(consume: ConsumeTypeLike) -> SystemReminderConsumeType:
    """规范化 consume 参数。"""

    if isinstance(consume, SystemReminderConsumeType):
        return consume

    if not isinstance(consume, str) or not consume.strip():
        raise ValueError("consume 不能为空")

    normalized = consume.strip().lower()
    try:
        return SystemReminderConsumeType(normalized)
    except ValueError as exc:
        raise ValueError("consume 只能是 forever 或 once") from exc


def _validate_insert_and_consume(
    *,
    insert_type: SystemReminderInsertType,
    consume_type: SystemReminderConsumeType,
) -> None:
    """校验插入策略与消费模式的组合是否合法。"""

    if (
        consume_type == SystemReminderConsumeType.ONCE
        and insert_type != SystemReminderInsertType.DYNAMIC
    ):
        raise ValueError("consume=once 只能与 insert_type=dynamic 组合使用")


class SystemReminderStore:
    """system reminder存储类，提供线程安全的接口来设置和获取reminder。

    设计说明:
    - 内部使用三层嵌套字典结构存储 reminder：
      第一层键为 bucket，第二层键为 stream_id（``None`` 表示全局），
      第三层键为 name。
    - 提供 set 和 get 方法来添加和检索 reminder，支持按 bucket、stream_id
      和 name 进行过滤。
    - ``stream_id=None`` 时为全局命名空间，所有聊天流共享。
    - ``stream_id`` 为具体值时为流私有命名空间，仅对该流可见。
    - 使用 RLock 确保在多线程环境下的安全访问。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        # bucket -> stream_id(None=全局) -> name -> item
        self._data: dict[str, dict[str | None, dict[str, SystemReminderItem]]] = {}

    def set(
        self,
        bucket: BucketLike,
        name: str,
        content: str,
        insert_type: InsertTypeLike = SystemReminderInsertType.FIXED,
        consume: ConsumeTypeLike = SystemReminderConsumeType.FOREVER,
        stream_id: str | None = None,
    ) -> None:
        """设置一个reminder。

        Args:
            bucket: reminder所属的 bucket。
            name: reminder的名称。
            content: reminder的内容文本。
            insert_type: reminder 的插入位置类型。
            consume: reminder 的消费模式。
            stream_id: 聊天流 ID。为 ``None`` 时写入全局命名空间（所有流共享）；
                为具体值时仅对该流可见。name 在同一 bucket + stream_id 范围内唯一。
        """

        bucket_key = _normalize_bucket(bucket)
        _validate_non_empty(name, "name")
        _validate_non_empty(content, "content")
        normalized_name = name.strip()
        normalized_insert_type = _normalize_insert_type(insert_type)
        normalized_consume_type = _normalize_consume_type(consume)
        _validate_insert_and_consume(
            insert_type=normalized_insert_type,
            consume_type=normalized_consume_type,
        )

        with self._lock:
            stream_map = self._data.setdefault(bucket_key, {})
            name_map = stream_map.setdefault(stream_id, {})
            name_map[normalized_name] = SystemReminderItem(
                name=normalized_name,
                content=content,
                insert_type=normalized_insert_type,
                consume_type=normalized_consume_type,
            )

    def get_items(
        self,
        bucket: BucketLike,
        names: Sequence[str] | None = None,
        stream_id: str | None = None,
    ) -> list[SystemReminderItem]:
        """获取 bucket 下的 reminder 记录列表。

        Args:
            bucket: reminder所属的 bucket。
            names: 可选的reminder名称列表。如果提供，则仅返回这些 name 的 reminder。
            stream_id: 聊天流 ID。为 ``None`` 时从全局命名空间读取；
                为具体值时从该流的私有命名空间读取。

        Returns:
            bucket + stream_id 范围内的 reminder 记录列表。
        """

        bucket_key = _normalize_bucket(bucket)

        with self._lock:
            stream_map = dict(self._data.get(bucket_key, {}))
            bucket_map = dict(stream_map.get(stream_id, {}))

        if names is None:
            return list(bucket_map.values())

        selected_items: list[SystemReminderItem] = []
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("names 不能为空或包含空字符串")
            normalized_name = name.strip()
            if normalized_name in bucket_map:
                selected_items.append(bucket_map[normalized_name])
        return selected_items

    def get(
        self,
        bucket: BucketLike,
        names: Sequence[str] | None = None,
        stream_id: str | None = None,
    ) -> str:
        """获取 bucket 下的reminder文本。

        Args:
            bucket: reminder所属的 bucket。
            names: 可选的reminder名称列表。如果提供，则仅返回这些 name 的 reminder，且按 names 顺序拼接；如果为 None，则返回 bucket 下所有 reminder（按插入顺序拼接）。
            stream_id: 聊天流 ID。为 ``None`` 时从全局命名空间读取；为具体值时从该流的私有命名空间读取。

        Returns:
            bucket 下的reminder文本，格式为 [name]\ncontent，多个reminder之间以 \n\n 分隔；如果没有找到任何reminder，则返回空字符串。
        """

        selected_items = self.get_items(bucket=bucket, names=names, stream_id=stream_id)
        if not selected_items:
            return ""

        return "\n\n".join(item.render() for item in selected_items)

    def clear_bucket(
        self,
        bucket: BucketLike,
        stream_id: str | None = None,
    ) -> None:
        """清空指定 bucket 下的 reminder。

        Args:
            bucket: reminder所属的 bucket。
            stream_id: 聊天流 ID。为 ``None`` 时清空全局命名空间；
                为具体值时仅清空该流的私有命名空间。
        """

        bucket_key = _normalize_bucket(bucket)
        with self._lock:
            stream_map = self._data.get(bucket_key)
            if stream_map is None:
                return
            stream_map.pop(stream_id, None)
            if not stream_map:
                self._data.pop(bucket_key, None)

    def delete(
        self,
        bucket: BucketLike,
        name: str,
        stream_id: str | None = None,
    ) -> bool:
        """删除指定 bucket 下的单条 reminder。

        Args:
            bucket: reminder 所属的 bucket。
            name: reminder 名称。
            stream_id: 聊天流 ID。为 ``None`` 时从全局命名空间删除；为具体值时从该流删除。

        Returns:
            bool: 删除成功返回 True；不存在时返回 False。
        """

        bucket_key = _normalize_bucket(bucket)
        _validate_non_empty(name, "name")
        normalized_name = name.strip()

        with self._lock:
            stream_map = self._data.get(bucket_key)
            if stream_map is None:
                return False
            name_map = stream_map.get(stream_id)
            if not name_map or normalized_name not in name_map:
                return False

            del name_map[normalized_name]
            if not name_map:
                stream_map.pop(stream_id, None)
            if not stream_map:
                self._data.pop(bucket_key, None)
            return True

    def clear_all(self) -> None:
        """清空所有 bucket 下的所有 reminder（包括全局和所有流私有）。"""

        with self._lock:
            self._data.clear()

    def clear_stream(self, stream_id: str) -> None:
        """清除指定聊天流在所有 bucket 下的私有 reminder。

        主要用于聊天流销毁或重置时清理资源，避免流私有 reminder 残留。

        Args:
            stream_id: 要清除的聊天流 ID。
        """

        if not stream_id:
            return

        with self._lock:
            empty_buckets: list[str] = []
            for bucket_key, stream_map in self._data.items():
                stream_map.pop(stream_id, None)
                if not stream_map:
                    empty_buckets.append(bucket_key)
            for bucket_key in empty_buckets:
                self._data.pop(bucket_key, None)


_global_store: SystemReminderStore | None = None


def get_system_reminder_store() -> SystemReminderStore:
    """获取全局单例 store。"""

    global _global_store
    if _global_store is None:
        _global_store = SystemReminderStore()
    return _global_store


def reset_system_reminder_store() -> None:
    """重置全局 store（主要用于测试）。"""

    global _global_store
    _global_store = None


__all__ = [
    "SystemReminderBucket",
    "SystemReminderInsertType",
    "SystemReminderConsumeType",
    "SystemReminderItem",
    "SystemReminderStore",
    "get_system_reminder_store",
    "reset_system_reminder_store",
]
