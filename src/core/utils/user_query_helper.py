"""用户查询辅助工具

提供统一的用户信息查询接口，处理 PersonInfo 与其他表的关联。
"""

import time
import hashlib
from typing import TYPE_CHECKING
from functools import lru_cache

from src.kernel.db import CRUDBase, QueryBuilder
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.models.sql_alchemy import PersonInfo, ChatStreams, Messages

logger = get_logger("user_query", display="UserQuery")


class UserQueryHelper:
    """用户查询辅助类"""

    def __init__(self) -> None:
        """初始化用户查询辅助类"""
        # 延迟导入避免循环依赖
        from src.core.models.sql_alchemy import PersonInfo, ChatStreams, Messages

        self.person_crud = CRUDBase[PersonInfo](PersonInfo)
        self.stream_crud = CRUDBase[ChatStreams](ChatStreams)
        self.messages_crud = CRUDBase[Messages](Messages)
        self._PersonInfo = PersonInfo
        self._ChatStreams = ChatStreams
        self._Messages = Messages

    @lru_cache(maxsize=10000)
    def generate_person_id(self, platform: str, user_id: str) -> str:
        """生成 person_id

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID

        Returns:
            全局唯一的 person_id
        """
        return hashlib.sha256(f"{platform}_{user_id}".encode()).hexdigest()

    async def get_or_create_person(
        self,
        platform: str,
        user_id: str,
        nickname: str | None = None,
        cardname: str | None = None,
    ) -> tuple["PersonInfo", bool]:
        """获取或创建用户信息

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID
            nickname: 用户昵称（创建时使用）
            cardname: 群名片（创建时使用）

        Returns:
            (用户信息, 是否为新创建)
        """
        person_id = self.generate_person_id(platform, user_id)

        # 1. 尝试获取
        existing = await self.person_crud.get_by(person_id=person_id)
        if existing:
            # 2. 更新最后交互时间
            await self.person_crud.update(
                existing.id,
                {
                    "last_interaction": time.time(),
                    "interaction_count": existing.interaction_count + 1,
                },
            )
            return existing, False

        # 3. 创建新用户
        now = time.time()
        person_data = {
            "person_id": person_id,
            "platform": platform,
            "user_id": user_id,
            "nickname": nickname,
            "cardname": cardname,
            "first_interaction": now,
            "last_interaction": now,
            "interaction_count": 1,
            "attitude": 50,
            "created_at": now,
            "updated_at": now,
        }

        person = await self.person_crud.create(person_data)
        logger.info(f"创建新用户：{person_id} ({nickname})")
        return person, True

    async def get_user_streams(
        self,
        platform: str,
        user_id: str,
    ) -> list["ChatStreams"]:
        """获取用户的所有聊天流

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID

        Returns:
            聊天流列表
        """
        person_id = self.generate_person_id(platform, user_id)

        streams = await QueryBuilder(self._ChatStreams).filter(
            person_id=person_id
        ).order_by("-last_active_time").all()

        return streams

    async def get_user_recent_messages(
        self,
        platform: str,
        user_id: str,
        limit: int = 50,
    ) -> list["Messages"]:
        """获取用户最近发送的消息

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID
            limit: 返回的最大消息数

        Returns:
            消息列表
        """
        person_id = self.generate_person_id(platform, user_id)

        messages = await QueryBuilder(self._Messages).filter(
            person_id=person_id
        ).order_by("-time").limit(limit).all()

        return messages

    async def enrich_message_with_person_info(
        self,
        message: "Messages",
    ) -> dict:
        """为消息补充用户信息

        Args:
            message: 消息对象

        Returns:
            包含用户信息的字典
        """
        if not message.person_id:
            return message.to_dict()

        person = await self.person_crud.get_by(person_id=message.person_id)
        if not person:
            return message.to_dict()

        msg_dict = message.to_dict()
        msg_dict.update(
            {
                "user_nickname": person.nickname,
                "user_cardname": person.cardname,
                "user_attitude": person.attitude,
                "user_interaction_count": person.interaction_count,
            }
        )

        return msg_dict

    async def update_user_impression(
        self,
        platform: str,
        user_id: str,
        impression: str,
        short_impression: str | None = None,
    ) -> bool:
        """更新对用户的印象

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID
            impression: 长期印象
            short_impression: 简短印象

        Returns:
            是否更新成功
        """
        person_id = self.generate_person_id(platform, user_id)

        person = await self.person_crud.get_by(person_id=person_id)
        if not person:
            logger.warning(f"用户不存在：{person_id}")
            return False

        update_data = {
            "impression": impression,
            "updated_at": time.time(),
        }

        if short_impression is not None:
            update_data["short_impression"] = short_impression

        await self.person_crud.update(person.id, update_data)
        logger.info(f"更新用户印象：{person_id}")
        return True

    async def update_user_attitude(
        self,
        platform: str,
        user_id: str,
        attitude_delta: int,
    ) -> int | None:
        """更新对用户的态度评分

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID
            attitude_delta: 态度变化量（可正可负）

        Returns:
            更新后的态度评分，失败返回 None
        """
        person_id = self.generate_person_id(platform, user_id)

        person = await self.person_crud.get_by(person_id=person_id)
        if not person:
            logger.warning(f"用户不存在：{person_id}")
            return None

        # 限制态度评分在 0-100 范围内
        new_attitude = max(0, min(100, person.attitude + attitude_delta))

        await self.person_crud.update(
            person.id,
            {
                "attitude": new_attitude,
                "updated_at": time.time(),
            },
        )

        logger.info(
            f"更新用户态度：{person_id} {person.attitude} -> {new_attitude}"
        )
        return new_attitude


# 全局单例
_user_query_helper: UserQueryHelper | None = None


def get_user_query_helper() -> UserQueryHelper:
    """获取用户查询辅助工具单例

    Returns:
        UserQueryHelper: 用户查询辅助工具实例
    """
    global _user_query_helper
    if _user_query_helper is None:
        _user_query_helper = UserQueryHelper()
    return _user_query_helper


__all__ = [
    "UserQueryHelper",
    "get_user_query_helper",
]
