"""Prompt API 模块。

提供 PromptTemplate 的注册、检索与管理能力。

本模块是对 :class:`src.core.prompt.manager.PromptManager` 的薄封装，
用于在插件系统侧以稳定的 API 形式访问 prompt 管理器。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.prompt import (
    STREAM_BUCKET_PREFIX,
    SystemReminderConsumeType,
    SystemReminderInsertType,
)

API_VERSION = "1.0.0"

if TYPE_CHECKING:
    from src.core.prompt import PromptManager, PromptTemplate, SystemReminderBucket
    from src.core.prompt.system_reminder import SystemReminderStore


def _get_prompt_manager() -> "PromptManager":
    """延迟获取 PromptManager，避免循环依赖。

    Returns:
        Prompt 管理器实例
    """

    from src.core.prompt import get_prompt_manager

    return get_prompt_manager()


def _get_system_reminder_store() -> "SystemReminderStore":
    """延迟获取 SystemReminderStore，避免循环依赖。

    Returns:
        system reminder 存储实例
    """

    from src.core.prompt import get_system_reminder_store

    return get_system_reminder_store()


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def register_template(template: "PromptTemplate") -> None:
    """注册一个 PromptTemplate。

    Args:
        template: PromptTemplate 实例

    Returns:
        None
    """

    if template is None:
        raise ValueError("template 不能为空")
    _get_prompt_manager().register_template(template)


def unregister_template(name: str) -> bool:
    """注销一个 PromptTemplate。

    Args:
        name: 模板名称

    Returns:
        如果模板存在并删除成功返回 True，否则返回 False
    """

    _validate_non_empty(name, "name")
    return _get_prompt_manager().unregister_template(name)


def get_template(name: str) -> "PromptTemplate | None":
    """获取模板副本。

    Args:
        name: 模板名称

    Returns:
        模板副本；未找到返回 None
    """

    _validate_non_empty(name, "name")
    return _get_prompt_manager().get_template(name)


def get_or_create(
    name: str,
    template: str,
    policies: dict[str, Any] | None = None,
) -> "PromptTemplate":
    """获取或创建模板。

    Args:
        name: 模板名称
        template: 模板字符串
        policies: 可选渲染策略映射

    Returns:
        模板副本
    """

    _validate_non_empty(name, "name")
    _validate_non_empty(template, "template")
    return _get_prompt_manager().get_or_create(name=name, template=template, policies=policies)


def has_template(name: str) -> bool:
    """检查模板是否存在。

    Args:
        name: 模板名称

    Returns:
        是否存在
    """

    _validate_non_empty(name, "name")
    return _get_prompt_manager().has_template(name)


def list_templates() -> list[str]:
    """列出所有已注册模板名称。

    Returns:
        模板名称列表
    """

    return _get_prompt_manager().list_templates()


def clear_templates() -> None:
    """清空所有已注册模板。"""

    _get_prompt_manager().clear()


def count_templates() -> int:
    """获取已注册模板数量。"""

    return _get_prompt_manager().count()


def add_system_reminder(
    bucket: str | SystemReminderBucket,
    name: str,
    content: str,
    insert_type: str | SystemReminderInsertType = SystemReminderInsertType.FIXED,
    consume: str | SystemReminderConsumeType = SystemReminderConsumeType.FOREVER,
) -> None:
    """添加（或覆盖）一条 system reminder。

    该功能仅提供存储能力，不会自动注入到 LLM context。
    调用方需要自行通过 :func:`get_system_reminder` 获取并注入。

    Args:
        bucket: bucket 名称（推荐使用 SystemReminderBucket 预设值，如 actor/sub_actor）
        name: reminder 名称
        content: reminder 内容
        insert_type: reminder 插入位置类型，支持 fixed 和 dynamic

    Returns:
        None
    """

    # bucket 的空值校验在 store 内完成，这里保持与其它参数一致的提示
    _validate_non_empty(name, "name")
    _validate_non_empty(content, "content")
    _get_system_reminder_store().set(
        bucket=bucket,
        name=name,
        content=content,
        insert_type=insert_type,
        consume=consume,
    )


def get_system_reminder(
    bucket: str | SystemReminderBucket,
    names: list[str] | None = None,
) -> str:
    """获取指定 bucket 的 system reminder 内容。

    Args:
        bucket: bucket 名称
        names: 可选的 name 列表；传入时仅返回这些 name 对应的 reminder（按 names 顺序拼接）。

    Returns:
        拼接后的 reminder 字符串；若 bucket 为空或无内容则返回空字符串。
    """

    return _get_system_reminder_store().get(bucket=bucket, names=names)


# ── 流隔离 reminder API ──────────────────────────────────────────────
#
# 以下函数在 system reminder store 上以 ``stream:{stream_id}:{bucket}``
# 作为 bucket key 实现按聊天流隔离的 reminder 读写。
# chatter 通过 ``create_request(with_reminder=...)`` 调用时自动同时拾取
# 全局 bucket 和当前流私有 bucket，无需插件感知底层命名约定。


def _stream_bucket(stream_id: str, bucket: str) -> str:
    """构造流私有 bucket 名称。

    Args:
        stream_id: 聊天流 ID。
        bucket: bucket 名称（如 ``"actor"``），必须是纯字符串。
            若调用方持有 :class:`SystemReminderBucket` 枚举值，
            应在上层取 ``.value`` 后再传入。

    Returns:
        形如 ``stream:{stream_id}:{bucket}`` 的流私有 bucket key。
    """

    _validate_non_empty(stream_id, "stream_id")
    # strip 防止上游传入带空格的 stream_id 导致 bucket key 不一致
    normalized_stream_id = stream_id.strip()
    return f"{STREAM_BUCKET_PREFIX}{normalized_stream_id}:{bucket}"


def add_stream_reminder(
    stream_id: str,
    bucket: str,
    name: str,
    content: str,
    insert_type: str | SystemReminderInsertType = SystemReminderInsertType.FIXED,
    consume: str | SystemReminderConsumeType = SystemReminderConsumeType.FOREVER,
) -> None:
    """向指定聊天流的私有 bucket 写入（覆盖）一条 system reminder。

    与 :func:`add_system_reminder` 的区分：

    - :func:`add_system_reminder` 写入全局 bucket，所有聊天流的 chatter
      通过 ``with_reminder`` 都会拾取到同一条 reminder。
    - 本函数写入 ``stream:{stream_id}:{bucket}`` 的私有 bucket，仅对
      指定聊天流可见，其他流不受影响。

    chatter 通过 ``create_request(with_reminder=...)`` 调用时，
    会自动同时拾取全局 bucket 和当前流私有 bucket，插件无需关心拾取逻辑。

    Args:
        stream_id: 聊天流 ID。必填，用于定位流私有 bucket。
        bucket: bucket 名称（推荐使用 :class:`SystemReminderBucket` 预设值，
            如 ``actor`` / ``sub_agent``）。
        name: reminder 名称，在同一个流私有 bucket 内唯一。
        content: reminder 内容文本。
        insert_type: reminder 插入位置类型（``fixed`` / ``dynamic``）。
        consume: reminder 消费模式（``forever`` / ``once``）。

    Returns:
        None

    Raises:
        ValueError: 当 ``stream_id`` 为空，或 ``name`` / ``content`` 为空时。
    """

    _validate_non_empty(name, "name")
    _validate_non_empty(content, "content")
    _get_system_reminder_store().set(
        bucket=_stream_bucket(stream_id, bucket),
        name=name,
        content=content,
        insert_type=insert_type,
        consume=consume,
    )


def get_stream_reminder(
    stream_id: str,
    bucket: str,
    names: list[str] | None = None,
) -> str:
    """从指定聊天流的私有 bucket 读取 reminder 文本。

    Args:
        stream_id: 聊天流 ID。
        bucket: bucket 名称。
        names: 可选的 name 列表；传入时仅返回这些 name 对应的 reminder
            （按 names 顺序拼接）。为 ``None`` 时返回 bucket 下所有 reminder。

    Returns:
        拼接后的 reminder 字符串；若 bucket 为空或无内容则返回空字符串。
    """

    return _get_system_reminder_store().get(
        bucket=_stream_bucket(stream_id, bucket),
        names=names,
    )


def delete_stream_reminder(
    stream_id: str,
    bucket: str,
    name: str,
) -> bool:
    """从指定聊天流的私有 bucket 删除单条 reminder。

    Args:
        stream_id: 聊天流 ID。
        bucket: bucket 名称。
        name: 要删除的 reminder 名称。

    Returns:
        bool: 删除成功返回 ``True``；不存在时返回 ``False``。
    """

    return _get_system_reminder_store().delete(
        bucket=_stream_bucket(stream_id, bucket),
        name=name,
    )


def clear_stream_reminders(stream_id: str) -> None:
    """清除指定聊天流在所有私有 bucket 下的 reminder。

    主要用于聊天流销毁或重置时清理资源，避免流私有 reminder 残留。
    本函数会扫描所有 bucket key，清除以 ``stream:{stream_id}:`` 开头的项，
    对全局 bucket 无影响。

    Args:
        stream_id: 要清除的聊天流 ID。
    """

    _validate_non_empty(stream_id, "stream_id")
    _get_system_reminder_store().clear_by_prefix(
        f"{STREAM_BUCKET_PREFIX}{stream_id.strip()}:"
    )


__all__ = [
    "API_VERSION",
    "register_template",
    "unregister_template",
    "get_template",
    "get_or_create",
    "has_template",
    "list_templates",
    "clear_templates",
    "count_templates",
    "add_system_reminder",
    "get_system_reminder",
    "add_stream_reminder",
    "get_stream_reminder",
    "delete_stream_reminder",
    "clear_stream_reminders",
]
