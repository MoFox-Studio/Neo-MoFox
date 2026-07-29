"""Person API 模块。

为插件提供用户身份标识生成、用户记录管理、名称变更历史、
用户关联查询与印象/态度维护能力。

涉及数据库读写的操作均为**异步函数**，调用时需 ``await``；
身份标识生成函数为同步函数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

API_VERSION = "1.0.0"

if TYPE_CHECKING:
    from src.core.models.message import Message
    from src.core.models.sql_alchemy import ChatStreams, Messages, PersonInfo


def _get_user_query_helper() -> Any:
    """延迟获取 UserQueryHelper，避免循环依赖。

    Returns:
        用户查询辅助工具实例
    """
    from src.core.utils.user_query_helper import get_user_query_helper

    return get_user_query_helper()


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


def _validate_limit(value: int, name: str) -> None:
    """校验 limit / delta 等整数参数。

    Args:
        value: 整数值
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数")


# ────────────────────────────────────────────────────────────
# 身份标识生成（同步）
# ────────────────────────────────────────────────────────────


def generate_raw_person_id(platform: str, user_id: str) -> str:
    """生成原始格式的 ``person_id``（``platform:user_id``）。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID

    Returns:
        原始 person_id
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return _get_user_query_helper().generate_raw_person_id(platform, user_id)


def generate_person_id(platform: str, user_id: str) -> str:
    """生成哈希后的 ``person_id``，用于系统内部索引。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID

    Returns:
        哈希后的 person_id
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return _get_user_query_helper().generate_person_id(platform, user_id)


# ────────────────────────────────────────────────────────────
# 用户记录管理（异步）
# ────────────────────────────────────────────────────────────


async def get_or_create_person(
    platform: str,
    user_id: str,
    nickname: str | None = None,
    cardname: str | None = None,
) -> tuple["PersonInfo", bool]:
    """获取或创建用户记录。

    存在时刷新 ``last_interaction`` 与 ``interaction_count``；不存在时创建新记录。
    返回 ``(PersonInfo, is_new)``。此函数为**异步函数**。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID
        nickname: 用户昵称（创建时使用）
        cardname: 群名片（创建时使用）

    Returns:
        (用户信息实例, 是否为新创建)
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return await _get_user_query_helper().get_or_create_person(
        platform=platform,
        user_id=user_id,
        nickname=nickname,
        cardname=cardname,
    )


async def get_person(
    platform: str,
    user_id: str,
) -> "PersonInfo | None":
    """获取用户记录（只读，不创建、不更新交互时间）。

    与 :func:`get_or_create_person` 的区别：用户不存在时返回 ``None``，
    不会自动创建新记录，也不刷新 ``last_interaction`` / ``interaction_count``。
    适合仅查询场景。此函数为**异步函数**。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID

    Returns:
        用户信息实例，不存在时返回 None
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return await _get_user_query_helper().get_person(
        platform=platform,
        user_id=user_id,
    )


async def update_person_info(
    platform: str,
    user_id: str,
    nickname: str | None = None,
    cardname: str | None = None,
) -> bool:
    """更新用户信息（消息接收流程实际调用的入口）。

    - 刷新 ``last_interaction`` / ``interaction_count`` / ``updated_at``
    - 当传入的 ``nickname`` 或 ``cardname`` 与数据库现有值不同且都非空时，
      自动把旧值推入对应的 ``*_history`` 列表，再用新值替换当前字段
    - 用户不存在时自动创建

    此函数为**异步函数**。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID
        nickname: 用户昵称，None 表示不更新
        cardname: 群名片，None 表示不更新

    Returns:
        是否更新成功
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return await _get_user_query_helper().update_person_info(
        platform=platform,
        user_id=user_id,
        nickname=nickname,
        cardname=cardname,
    )


async def update_user_impression(
    platform: str,
    user_id: str,
    impression: str,
    short_impression: str | None = None,
) -> bool:
    """更新对用户的长期印象。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID
        impression: 长期印象
        short_impression: 简短印象摘要，可选

    Returns:
        是否更新成功
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    _validate_non_empty(impression, "impression")
    return await _get_user_query_helper().update_user_impression(
        platform=platform,
        user_id=user_id,
        impression=impression,
        short_impression=short_impression,
    )


async def update_user_attitude(
    platform: str,
    user_id: str,
    attitude_delta: int,
) -> int | None:
    """更新对用户的态度评分（增减量）。

    评分会被限制在 ``0-100`` 范围内。返回更新后的分数，失败返回 ``None``。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID
        attitude_delta: 态度变化量（可正可负）

    Returns:
        更新后的态度评分，用户不存在时返回 None
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    if not isinstance(attitude_delta, int):
        raise ValueError("attitude_delta 必须是 int")
    return await _get_user_query_helper().update_user_attitude(
        platform=platform,
        user_id=user_id,
        attitude_delta=attitude_delta,
    )


# ────────────────────────────────────────────────────────────
# 名称变更历史（异步）
# ────────────────────────────────────────────────────────────


async def get_nickname_history(
    platform: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """获取用户昵称变更历史。

    列表按 ``retired_at`` 升序排列，每项形如
    ``{"name": str, "retired_at": float | None}``。
    用户不存在或无历史时返回空列表。此函数为**异步函数**。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID

    Returns:
        历史昵称列表
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return await _get_user_query_helper().get_name_history(
        platform=platform,
        user_id=user_id,
        field="nickname",
    )


async def get_cardname_history(
    platform: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """获取用户群名片变更历史。

    列表按 ``retired_at`` 升序排列，每项形如
    ``{"name": str, "retired_at": float | None}``。
    用户不存在或无历史时返回空列表。此函数为**异步函数**。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID

    Returns:
        历史群名片列表
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return await _get_user_query_helper().get_name_history(
        platform=platform,
        user_id=user_id,
        field="cardname",
    )


# ────────────────────────────────────────────────────────────
# 用户关联查询（异步）
# ────────────────────────────────────────────────────────────


async def get_user_streams(
    platform: str,
    user_id: str,
) -> list["ChatStreams"]:
    """获取用户的所有聊天流。

    结果按 ``last_active_time`` 降序排列。此函数为**异步函数**。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID

    Returns:
        聊天流记录列表
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return await _get_user_query_helper().get_user_streams(
        platform=platform,
        user_id=user_id,
    )


async def get_user_recent_messages(
    platform: str,
    user_id: str,
    limit: int = 50,
) -> list["Messages"]:
    """获取用户最近发送的消息。

    Args:
        platform: 平台标识
        user_id: 平台内部用户ID
        limit: 最大返回条数，默认 50

    Returns:
        消息记录列表
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    _validate_limit(limit, "limit")
    return await _get_user_query_helper().get_user_recent_messages(
        platform=platform,
        user_id=user_id,
        limit=limit,
    )


async def resolve_user_id(
    platform: str,
    keyword: str,
) -> str | None:
    """根据关键词（昵称/群名片/纯数字ID）解析平台 user_id。

    解析规则：
    1. 纯数字：直接视为 user_id
    2. 同平台按昵称/群名片精确匹配
    3. 失败则尝试包含匹配；仅在唯一命中时返回

    Args:
        platform: 平台标识
        keyword: 关键词

    Returns:
        解析出的 user_id；无法定位或命中不唯一时返回 None
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(keyword, "keyword")
    return await _get_user_query_helper().resolve_user_id(
        platform=platform,
        keyword=keyword,
    )


async def enrich_message_with_person_info(
    message: "Message",
) -> dict[str, Any]:
    """为消息补充用户信息（昵称、群名片、态度、交互次数等）。

    Args:
        message: 消息对象

    Returns:
        包含原始消息字段与用户字段的字典
    """
    if message is None:
        raise ValueError("message 不能为空")
    return await _get_user_query_helper().enrich_message_with_person_info(message)


__all__ = [
    "API_VERSION",
    # 身份标识
    "generate_raw_person_id",
    "generate_person_id",
    # 用户记录管理
    "get_or_create_person",
    "get_person",
    "update_person_info",
    "update_user_impression",
    "update_user_attitude",
    # 名称变更历史
    "get_nickname_history",
    "get_cardname_history",
    # 用户关联查询
    "get_user_streams",
    "get_user_recent_messages",
    "resolve_user_id",
    "enrich_message_with_person_info",
]
