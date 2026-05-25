"""Default Chatter private type definitions."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, TypeAlias

from src.core.components.base import Failure, Stop, Success, Wait, WaitResumeEvent
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.kernel.llm import LLMPayload, LLMRequest, ToolCall, ToolRegistry
from src.kernel.logger import Logger


class SubAgentDecision(TypedDict):
    """Sub-agent 返回的决策结果。"""

    reason: str
    should_respond: bool


class LLMResponseLike(Protocol):
    """LLM 响应的最小协议，支持继续发送和添加 payload。"""

    payloads: list[LLMPayload]
    message: str | None
    reasoning_content: str | None
    call_list: list[ToolCall] | None

    async def send(
        self,
        auto_append_response: bool = True,
        *,
        stream: bool = True,
    ) -> "LLMResponseLike":
        """使用当前对话状态继续请求。"""
        ...

    def add_payload(
        self,
        payload: LLMPayload,
        position: object = None,
    ) -> object:
        """将 payload 添加到对话状态中。"""
        ...

    def __await__(self) -> Generator[object, None, str]:
        """允许等待完整的响应。"""
        ...


LLMConversationState: TypeAlias = LLMRequest | LLMResponseLike


class SupportsRequestCreation(Protocol):
    """提供创建 LLM 请求的能力，允许会话核心在需要时构建请求对象。"""

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        with_reminder: str | None = None,
    ) -> LLMRequest:
        """创建一个 LLM 请求。"""
        ...


class PromptAdapter(Protocol):
    """Prompt 相关的钩子，用于会话核心。"""

    async def _build_system_prompt(self, chat_stream: ChatStream) -> str:
        ...

    def _build_enhanced_history_text(self, chat_stream: ChatStream) -> str:
        ...

    async def _build_user_prompt(
        self,
        chat_stream: ChatStream,
        history_text: str,
        unread_lines: str,
        extra: str = "",
    ) -> str:
        ...

    def _build_negative_behaviors_extra(self) -> str:
        ...


class UnreadAdapter(Protocol):
    """未读消息相关的钩子，用于会话核心处理未读消息的获取、格式化和状态更新。"""

    async def fetch_unreads(
        self,
        time_format: str = "%H:%M",
    ) -> tuple[str, list[Message]]:
        ...

    def format_message_line(
        self,
        msg: Message,
        time_format: str = "%H:%M",
    ) -> str:
        ...

    def _upsert_pending_unread_payload(
        self,
        response: LLMConversationState,
        formatted_text: str,
        unread_msgs: list[Message] | None = None,
        native_multimodal: bool = False,
        logger_override: Logger | None = None,
    ) -> None:
        ...

    async def flush_unreads(self, unread_messages: list[Message]) -> int:
        ...


class UsableAdapter(Protocol):
    """Tool registry 注入钩子，用于会话核心。"""

    async def inject_usables(self, request: LLMRequest) -> ToolRegistry:
        ...


class ToolExecutionAdapter(Protocol):
    """工具调用执行钩子，用于会话核心处理工具调用的执行逻辑。"""

    async def run_tool_call(
        self,
        calls: list[ToolCall],
        response: LLMResponseLike,
        usable_map: ToolRegistry,
        trigger_msg: Message | None,
    ) -> list[tuple[bool, bool]]:
        ...


class SubAgentAdapter(Protocol):
    """Sub-agent gate 钩子，用于会话核心处理子代理的决策逻辑。"""

    async def sub_agent(
        self,
        unreads_text: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
    ) -> SubAgentDecision:
        ...


class LoggerAdapter(Protocol):
    """日志适配器，提供日志记录和面板展示的能力，供会话核心使用。"""

    def info(self, *args: Any, **kwargs: Any) -> None:
        ...

    def warning(self, *args: Any, **kwargs: Any) -> None:
        ...

    def error(self, *args: Any, **kwargs: Any) -> None:
        ...

    def debug(self, *args: Any, **kwargs: Any) -> None:
        ...

    def print_panel(
        self,
        message: str,
        title: str | None = None,
        border_style: str | None = None,
    ) -> None:
        ...


@dataclass(slots=True)
class DefaultChatterSessionAdapters:
    """可重用聊天核心公开的显式接口。"""

    request_adapter: SupportsRequestCreation
    prompt_adapter: PromptAdapter
    unread_adapter: UnreadAdapter
    usable_adapter: UsableAdapter
    tool_execution_adapter: ToolExecutionAdapter
    sub_agent_adapter: SubAgentAdapter
    logger_adapter: LoggerAdapter

@dataclass(slots=True)
class DefaultChatterSessionOptions:
    """可重用聊天核心公开的配置选项。"""

    actor_task_name: str = "actor"
    sub_actor_task_name: str = "actor"
    enable_cooldown: bool = False
    enable_action_suspend: bool = True
    enable_programmatic_controller: bool = True
    enable_sub_agent_collaboration: bool = False
    enable_stop_direct_message_wake: bool = False
    stop_direct_message_wake_probability: float = 0.0
    native_multimodal: bool = False
    theme_guide: dict[str, str] = field(default_factory=dict)
    negative_behavior_reinforcement: bool = True


DefaultChatterResult: TypeAlias = Wait | Success | Failure | Stop
DefaultChatterResumeEvent: TypeAlias = WaitResumeEvent | None
