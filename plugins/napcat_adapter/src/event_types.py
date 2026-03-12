"""Napcat 适配器事件类型定义"""


class NapcatEvent:
    """Napcat 适配器事件类型"""

    class ON_RECEIVED:
        """接收事件"""

        FRIEND_INPUT = "napcat.on_received.friend_input"  # 好友正在输入
        EMOJI_LIEK = "napcat.on_received.emoji_like"  # 表情回复（注意：保持原来的拼写）
        POKE = "napcat.on_received.poke"  # 戳一戳
        GROUP_UPLOAD = "napcat.on_received.group_upload"  # 群文件上传
        GROUP_BAN = "napcat.on_received.group_ban"  # 群禁言
        GROUP_LIFT_BAN = "napcat.on_received.group_lift_ban"  # 群解禁
        FRIEND_RECALL = "napcat.on_received.friend_recall"  # 好友消息撤回
        GROUP_RECALL = "napcat.on_received.group_recall"  # 群消息撤回

    class MESSAGE:
        """消息相关事件"""

        GET_MSG = "napcat.message.get_msg"  # 获取消息

    class GROUP:
        """群组相关事件"""

        SET_GROUP_BAN = "napcat.group.set_group_ban"  # 设置群禁言
        SET_GROUP_WHOLE_BAN = "napcat.group.set_group_whole_ban"  # 设置全员禁言
        SET_GROUP_KICK = "napcat.group.set_group_kick"  # 踢出群聊

    class FRIEND:
        """好友相关事件"""

        SEND_LIKE = "napcat.friend.send_like"  # 发送点赞


__all__ = ["NapcatEvent"]
