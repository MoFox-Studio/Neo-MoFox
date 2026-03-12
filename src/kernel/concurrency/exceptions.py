"""
Concurrency 模块自定义异常

提供任务管理相关的异常类。
"""


class ConcurrencyError(Exception):
    """并发模块基础异常"""

    pass


class TaskNotFoundError(ConcurrencyError):
    """任务未找到异常"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task '{task_id}' not found")


class TaskTimeoutError(ConcurrencyError):
    """任务超时异常"""

    def __init__(self, task_id: str, timeout: float) -> None:
        self.task_id = task_id
        self.timeout = timeout
        super().__init__(f"Task '{task_id}' timeout after {timeout} seconds")


class TaskGroupError(ConcurrencyError):
    """任务组异常"""

    pass


class TaskGroupAlreadyExists(TaskGroupError):
    """任务组已存在异常"""

    def __init__(self, group_name: str) -> None:
        self.group_name = group_name
        super().__init__(f"TaskGroup '{group_name}' already exists")


class TaskGroupNotFoundError(TaskGroupError):
    """任务组未找到异常"""

    def __init__(self, group_name: str) -> None:
        self.group_name = group_name
        super().__init__(f"TaskGroup '{group_name}' not found")


class WatchDogError(ConcurrencyError):
    """WatchDog 异常"""

    pass
