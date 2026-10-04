"""聊天日记的群私聊开关、触发条件与后台处理配置。"""

from __future__ import annotations

from typing import Literal

from src.app.plugin_system.base import Field, SectionBase, config_section

TriggerMode = Literal["time", "messages", "either", "both"]


class DiaryPolicy(SectionBase):
    """一种聊天类型的日记采集与注入策略。"""

    enabled: bool = Field(default=True, description="启用此类聊天的日记整理与注入")
    trigger_mode: TriggerMode = Field(
        default="messages",
        description="time 仅时间；messages 仅消息数；either 任一满足；both 同时满足",
    )
    interval_seconds: int = Field(
        default=10800, ge=1, description="成功整理后的时间间隔（秒）"
    )
    message_threshold: int = Field(
        default=100, ge=1, description="尚未处理的实际聊天消息条数"
    )
    context_days: int = Field(
        default=7, ge=1, description="注入的最近自然日数，包含今天"
    )

    def is_due(self, *, message_count: int, elapsed_seconds: float) -> bool:
        """根据未处理消息数与时间判断是否整理，空批次始终跳过。"""
        if not self.enabled or message_count <= 0:
            return False
        time_due = elapsed_seconds >= self.interval_seconds
        messages_due = message_count >= self.message_threshold
        if self.trigger_mode == "time":
            return time_due
        if self.trigger_mode == "messages":
            return messages_due
        if self.trigger_mode == "either":
            return time_due or messages_due
        return time_due and messages_due


@config_section("group", title="群聊日记", tag="ai")
class GroupDiaryPolicy(DiaryPolicy):
    """群聊默认同时满足三小时与两百条消息后整理。"""

    trigger_mode: TriggerMode = Field(default="both", description="群聊日记触发方式")
    message_threshold: int = Field(
        default=200, ge=1, description="群聊未处理消息条数阈值"
    )


@config_section("private", title="私聊日记", tag="ai")
class PrivateDiaryPolicy(DiaryPolicy):
    """私聊默认按累计消息条数整理。"""


@config_section("diary", title="聊天日记", tag="ai")
class DiaryConfig(SectionBase):
    """独立于长期记忆的近期交流日记配置。"""

    group: GroupDiaryPolicy = Field(default_factory=GroupDiaryPolicy)
    private: PrivateDiaryPolicy = Field(default_factory=PrivateDiaryPolicy)
    database_path: str = Field(
        default="data/engram_memory/chat_diary.db",
        description="独立日记数据库路径",
    )
    timezone: str = Field(default="Asia/Shanghai", description="日记自然日所属时区")
    max_concurrency: int = Field(
        default=3, ge=1, description="不同聊天流的生成并发上限"
    )
    batch_messages: int = Field(default=100, ge=1, description="每批连续新消息条数上限")
    context_messages: int = Field(
        default=6, ge=0, description="用于理解指代的少量前文条数"
    )
    retry_limit: int = Field(
        default=2, ge=0, description="同一批次失败后的追加重试次数"
    )

    def policy_for(self, chat_type: str) -> DiaryPolicy:
        """取得群聊或私聊策略，不将未知聊天类型归入其他流。"""
        if chat_type == "group":
            return self.group
        if chat_type == "private":
            return self.private
        raise ValueError(f"不支持的日记聊天类型: {chat_type}")
