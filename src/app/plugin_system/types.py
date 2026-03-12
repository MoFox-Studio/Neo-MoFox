"""插件系统公共类型模块。

为插件作者提供一层相对稳定的类型导出，避免在编写插件时
频繁直接下钻到 core 或 kernel 的更底层模块中寻找常用类型。
"""

from __future__ import annotations

from enum import Enum

from src.core.components.types import (
    ChatType,
    ComponentMeta,
    ComponentSignature,
    ComponentState,
    ComponentType,
    EventType,
    PermissionLevel,
)
from src.core.models import ChatStream, Message, MessageType, StreamContext
from src.core.prompt import PromptTemplate, SystemReminderBucket
from src.core.prompt.policies import RenderPolicy
from src.kernel.llm import (
    Audio,
    Content,
    Image,
    LLMPayload,
    LLMUsable,
    ModelEntry,
    ModelSet,
    RequestType,
    ROLE,
    Text,
    ToolCall,
    ToolRegistry,
    ToolResult,
)


class TaskType(Enum):
    """插件系统面向任务语义的模型分类。"""

    UTILS = "utils"
    UTILS_SMALL = "utils_small"
    ACTOR = "actor"
    SUB_ACTOR = "sub_actor"
    VLM = "vlm"
    VOICE = "voice"
    VIDEO = "video"
    TOOL_USE = "tool_use"


__all__ = [
    "Audio",
    "ChatType",
    "ComponentMeta",
    "ComponentSignature",
    "ComponentState",
    "ComponentType",
    "Content",
    "EventType",
    "Image",
    "LLMPayload",
    "LLMUsable",
    "Message",
    "MessageType",
    "ModelEntry",
    "ModelSet",
    "PermissionLevel",
    "PromptTemplate",
    "RenderPolicy",
    "RequestType",
    "ROLE",
    "ChatStream",
    "StreamContext",
    "SystemReminderBucket",
    "TaskType",
    "Text",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
]