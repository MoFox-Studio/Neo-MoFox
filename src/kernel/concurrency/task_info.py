"""
任务信息数据类

封装异步任务的元数据信息。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Coroutine
from uuid import uuid4


@dataclass
class TaskInfo:
    """异步任务信息

    存储任务的元数据和运行状态信息。

    Attributes:
        task_id: 任务唯一标识符
        name: 任务名称（可选，用于调试）
        coro: 任务协程对象
        task: asyncio.Task 对象
        daemon: 是否为守护任务（守护任务不会被 WatchDog 超时检查）
        timeout: 任务超时时间（秒），None 表示不超时
        created_at: 任务创建时间
        group_name: 所属任务组名称，None 表示不属于任何组
        metadata: 额外的元数据字典
    """

    task_id: str = field(default_factory=lambda: str(uuid4()))
    name: str | None = None
    coro: Coroutine[Any, Any, Any] | None = None
    task: asyncio.Task[Any] | None = None
    daemon: bool = False
    timeout: float | None = None
    created_at: datetime = field(default_factory=datetime.now)
    group_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_done(self) -> bool:
        """检查任务是否已完成"""
        return self.task is not None and self.task.done()

    def is_cancelled(self) -> bool:
        """检查任务是否被取消"""
        return self.task is not None and self.task.cancelled()

    def is_failed(self) -> bool:
        """检查任务是否失败"""
        if not self.is_done():
            return False
        if self.task is None:
            return False
        if self.task.exception() is not None:
            return True
        return False

    def get_exception(self) -> BaseException | None:
        """获取任务异常"""
        if self.task is None:
            return None
        return self.task.exception()

    def get_result(self) -> Any:
        """获取任务结果"""
        if self.task is None:
            return None
        return self.task.result()

    def cancel(self) -> bool:
        """取消任务"""
        if self.task is None:
            return False
        return self.task.cancel()

    def __repr__(self) -> str:
        """任务信息字符串表示"""
        status = "running"
        if self.is_done():
            if self.is_cancelled():
                status = "cancelled"
            elif self.is_failed():
                status = "failed"
            else:
                status = "completed"

        daemon_str = " [daemon]" if self.daemon else ""
        group_str = f" @{self.group_name}" if self.group_name else ""

        return (
            f"TaskInfo(id={self.task_id[:8]}, name={self.name}, "
            f"status={status}{daemon_str}{group_str})"
        )
