from __future__ import annotations

from enum import Enum


class ROLE(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    # 声明可用工具（函数）列表；不是模型对话角色。
    TOOL = "tool"

    # 工具执行结果回传给模型。
    TOOL_RESULT = "tool_result"
