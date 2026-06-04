"""OneBot 适配器事件类型定义"""


class OneBotEvent:
    """OneBot 适配器事件类型"""

    class ON_RECEIVED:
        """接收事件"""

        FRIEND_INPUT = "onebot.on_received.friend_input"  # 好友正在输入
        EMOJI_LIEK = "onebot.on_received.emoji_like"  # 表情回复（注意：保持原来的拼写）
        POKE = "onebot.on_received.poke"  # 戳一戳
        GROUP_UPLOAD = "onebot.on_received.group_upload"  # 群文件上传
        GROUP_BAN = "onebot.on_received.group_ban"  # 群禁言
        GROUP_LIFT_BAN = "onebot.on_received.group_lift_ban"  # 群解禁
        FRIEND_RECALL = "onebot.on_received.friend_recall"  # 好友消息撤回
        GROUP_RECALL = "onebot.on_received.group_recall"  # 群消息撤回

    class MESSAGE:
        """消息相关事件"""

        GET_MSG = "onebot.message.get_msg"  # 获取消息

    class GROUP:
        """群组相关事件"""

        SET_GROUP_BAN = "onebot.group.set_group_ban"  # 设置群禁言
        SET_GROUP_WHOLE_BAN = "onebot.group.set_group_whole_ban"  # 设置全员禁言
        SET_GROUP_KICK = "onebot.group.set_group_kick"  # 踢出群聊

    class FRIEND:
        """好友相关事件"""

        SEND_LIKE = "onebot.friend.send_like"  # 发送点赞


__all__ = ["OneBotEvent"]
