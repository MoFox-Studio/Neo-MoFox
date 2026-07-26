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


__all__ = ["OneBotEvent"]
