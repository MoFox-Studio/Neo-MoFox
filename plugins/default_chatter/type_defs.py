"""Default Chatter private type definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Generator
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Literal,
    NotRequired,
    Protocol,
    TypedDict,
    TypeAlias,
    runtime_checkable,
)

from src.app.plugin_system.api.llm_api import LLMRequest
from src.app.plugin_system.api.log_api import Logger
from src.app.plugin_system.base import Failure, Stop, Success, Wait, WaitResumeEvent
from src.app.plugin_system.types import ChatStream, LLMPayload, Message, ToolCall, ToolRegistry
from src.kernel.llm import StreamEvent


class SubAgentDecision(TypedDict):
    """Sub-agent 返回的决策结果。"""

    reason: str
    should_respond: bool
    source: NotRequired[str]


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

    async def stream_events_with_callback(
        self,
        on_event: Callable[[StreamEvent], Awaitable[None]],
    ) -> str:
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
    """定义会话创建 LLM 请求所需的接口。"""

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        with_reminder: str | None = None,
    ) -> LLMRequest:
        """创建一个 LLM 请求。"""
        ...


class PromptAdapter(Protocol):
    """定义会话提示词构建接口。"""

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
    """定义未读消息读取、格式化、合并和提交接口。"""

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
    """定义可调用组件注入接口。"""

    async def inject_usables(self, request: LLMRequest) -> ToolRegistry:
        ...


class ToolExecutionAdapter(Protocol):
    """定义工具调用批量执行接口。"""

    async def run_tool_call(
        self,
        calls: list[ToolCall],
        response: LLMResponseLike,
        usable_map: ToolRegistry,
        trigger_msg: Message | None,
    ) -> list[tuple[bool, bool]]:
        ...


class SubAgentAdapter(Protocol):
    """定义未读消息响应决策接口。"""

    async def sub_agent(
        self,
        unreads_text: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
        history_text: str = "",
        decision_history: list[SubAgentDecision] | None = None,
    ) -> SubAgentDecision:
        ...


class LoggerAdapter(Protocol):
    """定义会话日志和决策面板输出接口。"""

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


class PlainTextResponseHandling(TypedDict):
    """模型错误输出纯文本时的补救策略。"""

    action: Literal["retry", "wait", "stop"]
    reminder_text: str


@runtime_checkable
class PlainTextResponseAdapter(Protocol):
    """定义模型直接输出纯文本时的处理接口。"""

    def handle_plain_text_response(
        self,
        *,
        message: str,
        retry_count: int,
        response: LLMResponseLike,
    ) -> PlainTextResponseHandling:
        ...


@runtime_checkable
class DefaultChatterRuntimeAdapter(
    SupportsRequestCreation,
    PromptAdapter,
    UnreadAdapter,
    UsableAdapter,
    ToolExecutionAdapter,
    SubAgentAdapter,
    Protocol,
):
    """定义默认 Chatter 运行时必须同时提供的会话能力。"""


@dataclass(slots=True)
class DefaultChatterSessionAdapters:
    """会话依赖的运行时适配器集合。"""

    request_adapter: SupportsRequestCreation
    prompt_adapter: PromptAdapter
    unread_adapter: UnreadAdapter
    usable_adapter: UsableAdapter
    tool_execution_adapter: ToolExecutionAdapter
    sub_agent_adapter: SubAgentAdapter
    logger_adapter: LoggerAdapter
    plain_text_adapter: PlainTextResponseAdapter | None = None
    stream_event_observer: Callable[..., Awaitable[None]] | None = None


@dataclass(slots=True)
class DefaultChatterSessionOptions:
    """会话执行选项。"""

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
    enable_llm_stream: bool = False
    enable_sub_agent: bool = True
    enable_interest_filter: bool = False
    enable_sub_agent_context: bool = True
    sub_agent_context_history_limit: int = 10
    sub_agent_decision_history_limit: int = 3


DefaultChatterResult: TypeAlias = Wait | Success | Failure | Stop
DefaultChatterResumeEvent: TypeAlias = WaitResumeEvent | None
