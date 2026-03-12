"""系统提醒存储模块

系统提醒是一种轻量级的、结构化的文本信息，供模型在生成过程中参考。
它们通常包含特定格式的内容块，帮助模型记住重要信息或指导其行为。

设计目标：
- 简单易用：提供直观的接口来设置和获取提醒。
- 灵活性：支持不同的提醒分类（bucket）和命名（name），以适应多样化的使用场景。
- 线程安全：在多线程环境下安全地访问和修改提醒。
- 轻量级：仅在内存中存储，不涉及持久化，以保持高性能和低复杂度。

使用示例:
    from src.core.prompt import get_system_reminder_store

    # 添加提醒
    store = get_system_reminder_store()
    store.set(bucket="actor", name="goal", content="完成订单处理")
    store.set(bucket="actor", name="constraint", content="只能使用提供的API")

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

from enum import Enum
from threading import RLock
from typing import Sequence, TypeAlias


class SystemReminderBucket(str, Enum):
    """预定义的系统提醒分类（bucket）。可以根据实际需求扩展更多分类。"""

    ACTOR = "actor"
    SUB_ACTOR = "sub_actor"


BucketLike: TypeAlias = str | SystemReminderBucket


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


class SystemReminderStore:
    """system reminder存储类，提供线程安全的接口来设置和获取reminder。

    设计说明:
    - 内部使用嵌套字典结构存储reminder，第一层键为 bucket，第二层键为 name。
    - 提供 set 和 get 方法来添加和检索reminder，支持按 bucket 和 name 进行过滤。
    - 使用 RLock 确保在多线程环境下的安全访问。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._data: dict[str, dict[str, str]] = {}

    def set(self, bucket: BucketLike, name: str, content: str) -> None:
        """设置一个reminder。

        Args:
            bucket: reminder所属的 bucket。
            name: reminder的名称，在 bucket 内唯一。
            content: reminder的内容文本。
        """

        bucket_key = _normalize_bucket(bucket)
        _validate_non_empty(name, "name")
        _validate_non_empty(content, "content")

        with self._lock:
            self._data.setdefault(bucket_key, {})[name.strip()] = content

    def get(self, bucket: BucketLike, names: Sequence[str] | None = None) -> str:
        """获取 bucket 下的reminder文本。

        Args:
            bucket: reminder所属的 bucket。
            names: 可选的reminder名称列表。如果提供，则仅返回这些 name 的 reminder，且按 names 顺序拼接；如果为 None，则返回 bucket 下所有 reminder（按插入顺序拼接）。

        Returns:
            bucket 下的reminder文本，格式为 [name]\ncontent，多个reminder之间以 \n\n 分隔；如果没有找到任何reminder，则返回空字符串。
        """

        bucket_key = _normalize_bucket(bucket)

        with self._lock:
            bucket_map = dict(self._data.get(bucket_key, {}))

        selected_items: list[tuple[str, str]]
        if names is None:
            selected_items = list(bucket_map.items())
        else:
            selected_items = []
            for n in names:
                if not isinstance(n, str) or not n.strip():
                    raise ValueError("names 中包含空 name")
                key = n.strip()
                if key in bucket_map:
                    selected_items.append((key, bucket_map[key]))

        if not selected_items:
            return ""

        # 输出保持可预测：
        # - names 为 None：按该 bucket 内的插入顺序拼接
        # - names 非 None：按 names 的给定顺序拼接
        blocks: list[str] = []
        for name, content in selected_items:
            # 组合格式尽量简洁；调用方如需额外包装，可自行处理。
            blocks.append(f"[{name}]\n{content}")

        return "\n\n".join(blocks)

    def clear_bucket(self, bucket: BucketLike) -> None:
        """清空指定 bucket 下的所有 reminder。"""

        bucket_key = _normalize_bucket(bucket)
        with self._lock:
            self._data.pop(bucket_key, None)

    def delete(self, bucket: BucketLike, name: str) -> bool:
        """删除指定 bucket 下的单条 reminder。

        Args:
            bucket: reminder 所属的 bucket。
            name: reminder 名称。

        Returns:
            bool: 删除成功返回 True；不存在时返回 False。
        """

        bucket_key = _normalize_bucket(bucket)
        _validate_non_empty(name, "name")
        normalized_name = name.strip()

        with self._lock:
            bucket_map = self._data.get(bucket_key)
            if not bucket_map or normalized_name not in bucket_map:
                return False

            del bucket_map[normalized_name]
            if not bucket_map:
                self._data.pop(bucket_key, None)
            return True

    def clear_all(self) -> None:
        """清空所有 bucket 下的所有 reminder。"""

        with self._lock:
            self._data.clear()


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
    "SystemReminderStore",
    "get_system_reminder_store",
    "reset_system_reminder_store",
]
