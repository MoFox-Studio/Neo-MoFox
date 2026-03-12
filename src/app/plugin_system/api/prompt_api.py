"""Prompt API 模块。

提供 PromptTemplate 的注册、检索与管理能力。

本模块是对 :class:`src.core.prompt.manager.PromptManager` 的薄封装，
用于在插件系统侧以稳定的 API 形式访问 prompt 管理器。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.prompt import PromptManager, PromptTemplate
    from src.core.prompt import SystemReminderBucket
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
) -> None:
    """添加（或覆盖）一条 system reminder。

    该功能仅提供存储能力，不会自动注入到 LLM context。
    调用方需要自行通过 :func:`get_system_reminder` 获取并注入。

    Args:
        bucket: bucket 名称（推荐使用 SystemReminderBucket 预设值，如 actor/sub_actor）
        name: reminder 名称
        content: reminder 内容

    Returns:
        None
    """

    # bucket 的空值校验在 store 内完成，这里保持与其它参数一致的提示
    _validate_non_empty(name, "name")
    _validate_non_empty(content, "content")
    _get_system_reminder_store().set(bucket=bucket, name=name, content=content)


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


__all__ = [
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
]
