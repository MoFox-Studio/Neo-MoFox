"""
调度器类型定义

提供触发类型、任务状态等枚举类型，以及相关的数据模型。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class TriggerType(Enum):
    """触发类型枚举"""

    TIME = "time"  # 时间触发（延迟、周期、指定时间）
    EVENT = "event"  # 事件触发（预留，未来集成 event 模块）
    CUSTOM = "custom"  # 自定义条件触发


class TaskStatus(Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 等待触发
    RUNNING = "running"  # 正在执行
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 执行失败
    CANCELLED = "cancelled"  # 已取消
    PAUSED = "paused"  # 已暂停
    TIMEOUT = "timeout"  # 执行超时


@dataclass
class TaskExecution:
    """任务执行记录"""

    execution_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: TaskStatus = TaskStatus.RUNNING
    error: Exception | None = None
    result: Any = None
    duration: float = 0.0

    def complete(self, result: Any = None) -> None:
        """标记执行完成"""
        self.ended_at = datetime.now()
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.duration = (self.ended_at - self.started_at).total_seconds()

    def fail(self, error: Exception) -> None:
        """标记执行失败"""
        self.ended_at = datetime.now()
        self.status = TaskStatus.FAILED
        self.error = error
        self.duration = (self.ended_at - self.started_at).total_seconds()

    def cancel(self) -> None:
        """标记执行取消"""
        self.ended_at = datetime.now()
        self.status = TaskStatus.CANCELLED
        self.duration = (self.ended_at - self.started_at).total_seconds()
