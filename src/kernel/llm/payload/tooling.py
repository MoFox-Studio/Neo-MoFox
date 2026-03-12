"""
定义了与工具调用相关的内容类型、工具注册表和工具执行器。

主要组件：
- ToolCall：表示工具调用的信息，包括工具名称、参数等。
- ToolResult：表示工具执行的结果，包含结果值、调用 ID 和工具名称等信息。
- ToolRegistry：一个工具注册表，支持动态注册和发现工具。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .content import Content


@runtime_checkable
class LLMUsable(Protocol):
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """将组件描述为可被 LLM 调用的 schema。"""
        ...


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