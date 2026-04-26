"""
定义了与工具调用相关的内容类型、工具注册表和工具执行器。

主要组件：
- ToolCall：表示工具调用的信息，包括工具名称、参数等。
- ToolResult：表示工具执行的结果，包含结果值、调用 ID 和工具名称等信息。
- ToolRegistry：一个工具注册表，支持动态注册和发现工具。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from .content import Content

LLMUsableExecutionStatus = Literal["_WORKING", "_READY", "_DONE"]


@runtime_checkable
class LLMUsable(Protocol):
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """将组件描述为可被 LLM 调用的 schema。"""
        ...


class LLMUsableExecution:
    """包装一次 LLMUsable.execute 调用，并向调度器暴露执行状态。

    coroutine 会直接运行到返回值并进入 ``"_DONE"`` 状态；异步生成器或手写
    async iterator 会先运行到下一次 ``yield``，此时进入 ``"_READY"`` 状态，
    等待统一调度器决定何时继续。生成器的最后一次非空 ``yield`` 值会作为
    最终返回结果，空 ``yield`` 仅表示“准备完成，暂停等待调度”。

    Args:
        execution: ``execute`` 返回的 coroutine、异步生成器、async iterator，
            或已经同步得到的结果对象。

    Attributes:
        _status: 调度状态。``"_WORKING"`` 表示仍在运行，``"_READY"`` 表示
            已暂停并等待继续，``"_DONE"`` 表示已经完成。
        result: 执行完成后的标准化前结果。
        exception: 执行过程中捕获到的异常；调度器会在汇总阶段重新处理。
    """

    def __init__(self, execution: Any) -> None:
        self._status: LLMUsableExecutionStatus = "_WORKING"
        self.result: Any = None
        self.exception: BaseException | None = None
        self._last_non_empty_yield: Any = None
        self._aiter: Any | None = None
        self._task: asyncio.Task[None] | None = None

        if hasattr(execution, "__aiter__") and hasattr(execution, "__anext__"):
            self._aiter = execution.__aiter__()
            self._task = asyncio.create_task(self._advance_iterator())
        elif inspect.isawaitable(execution):
            self._task = asyncio.create_task(self._await_result(execution))
        else:
            self.result = execution
            self._status = "_DONE"

    @property
    def task(self) -> asyncio.Task[None] | None:
        """返回当前后台推进任务；没有正在运行的任务时返回 None。"""
        return self._task

    async def _await_result(self, execution: Any) -> None:
        """等待 coroutine 完成，并记录返回值或异常。"""
        try:
            self.result = await execution
        except BaseException as exc:
            self.exception = exc
            raise
        finally:
            self._status = "_DONE"

    async def _advance_iterator(self) -> None:
        """推进 async iterator 到下一次 yield 或完成。"""
        if self._aiter is None:
            self._status = "_DONE"
            return

        try:
            item = await anext(self._aiter)
        except StopAsyncIteration:
            self.result = self._last_non_empty_yield
            self._status = "_DONE"
        except BaseException as exc:
            self.exception = exc
            self._status = "_DONE"
            raise
        else:
            if item is not None:
                self._last_non_empty_yield = item
            self._status = "_READY"

    def resume(self) -> None:
        """继续一个处于 ``"_READY"`` 状态的异步迭代执行。"""
        if self._status != "_READY" or self._aiter is None:
            return
        self._status = "_WORKING"
        self._task = asyncio.create_task(self._advance_iterator())

    async def wait_done(self) -> Any:
        """持续推进直到完成，并返回最终结果。

        Returns:
            Any: coroutine 的返回值，或异步迭代器最后一次非空 ``yield`` 的值。

        Raises:
            BaseException: 重新抛出执行过程中捕获到的异常。
        """
        while self._status != "_DONE":
            if self._status == "_READY":
                self.resume()
            task = self._task
            if task is not None:
                await task
            else:
                await asyncio.sleep(0)

        if self._task is not None:
            await self._task
        if self.exception is not None:
            raise self.exception
        return self.result


@dataclass(frozen=True, slots=True)
class ToolCall(Content):
    id: str | None
    name: str
    args: dict[str, Any] | str

@dataclass(frozen=True, slots=True)
class ToolResult(Content):
    """工具执行结果。

    value：建议为 dict/str；若为 dict，会默认 JSON 序列化。
    call_id：用于 OpenAI tool message 的 tool_call_id。
    name：可选，便于调试；OpenAI tool message 不需要。
    """

    value: Any
    call_id: str | None = None
    name: str | None = None

    def to_text(self) -> str:
        if isinstance(self.value, str):
            return self.value
        try:
            return json.dumps(self.value, ensure_ascii=False)
        except Exception:
            return str(self.value)


class ToolRegistry:
    """工具注册表，支持动态注册和发现工具。

    使用示例：
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        registry.register(SearchTool)

        # 获取所有工具的 schema
        schemas = registry.list_all()

        # 根据名称获取工具
        tool_cls = registry.get("get_time")
    """

    def __init__(self) -> None:
        self._tools: dict[str, type[LLMUsable]] = {}

    def register(self, tool: type[LLMUsable], name: str | None = None) -> None:
        """注册工具。

        Args:
            tool: 工具类（需实现 LLMUsable 协议）。
            name: 工具名称，若不提供则从 schema 中提取。
        """
        if name is None:
            schema = tool.to_schema()
            # 尝试从 schema 中获取名称
            if "function" in schema:
                name = schema["function"].get("name")
            else:
                name = schema.get("name")

        if not name:
            raise ValueError(f"无法确定工具名称，请显式提供 name 参数：{tool}")

        self._tools[name] = tool

    def get(self, name: str) -> type[LLMUsable] | None:
        """根据名称获取工具类。"""
        return self._tools.get(name)

    def get_all(self) -> list[type[LLMUsable]]:
        """获取所有注册的工具类"""
        return list(self._tools.values())
    
    def list_all(self) -> list[dict[str, Any]]:
        """获取所有已注册工具的 schema 列表。"""
        return [tool.to_schema() for tool in self._tools.values()]

    def get_all_names(self) -> list[str]:
        """获取所有已注册工具的名称。"""
        return list(self._tools.keys())
