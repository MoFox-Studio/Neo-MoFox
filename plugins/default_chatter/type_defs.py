"""Default Chatter 私有类型定义。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Protocol, TypedDict, TypeAlias

from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.kernel.llm import LLMPayload, LLMRequest, ToolCall, ToolRegistry


class SubAgentDecision(TypedDict):
    """子代理是否响应的判定结果。"""

    reason: str
    should_respond: bool


class LLMResponseLike(Protocol):
    """具备响应阶段行为的最小结构类型。"""

    payloads: list[LLMPayload]
    message: str | None
    call_list: list[ToolCall] | None

    async def send(
        self,
        auto_append_response: bool = True,
        *,
        stream: bool = True,
    ) -> "LLMResponseLike":
        """继续基于当前上下文发起请求。"""
        ...

    def add_payload(
        self,
        payload: LLMPayload,
        position: object = None,
    ) -> object:
        """向上下文追加 payload。"""
        ...

    def __await__(self) -> Generator[object, None, str]:
        """支持 await 收集完整响应。"""
        ...


LLMConversationState: TypeAlias = LLMRequest | LLMResponseLike


class DefaultChatterRuntime(Protocol):
    """default_chatter 运行流程依赖的最小 chatter 能力集合。"""

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        max_context: int | None = None,
        with_reminder: str | None = None,
    ) -> LLMRequest:
        """创建 LLM 请求。"""
        ...

    async def _build_system_prompt(self, chat_stream: ChatStream) -> str:
        """构建系统提示词。"""
        ...

    def _build_enhanced_history_text(self, chat_stream: ChatStream) -> str:
        """构建 enhanced 模式历史文本。"""
        ...

    async def inject_usables(self, request: LLMRequest) -> ToolRegistry:
        """向请求注入可用工具。"""
        ...

    async def fetch_unreads(
        self,
        time_format: str = "%H:%M",
    ) -> tuple[str, list[Message]]:
        """读取当前未读消息。"""
        ...

    def format_message_line(
        self,
        msg: Message,
        time_format: str = "%H:%M",
    ) -> str:
        """格式化单条消息。"""
        ...

    async def _build_user_prompt(
        self,
        chat_stream: ChatStream,
        history_text: str,
        unread_lines: str,
        extra: str = "",
    ) -> str:
        """构建增强模式用户提示词。"""
        ...

    def _build_negative_behaviors_extra(self) -> str:
        """构建附加负面行为约束。"""
        ...

    async def sub_agent(
        self,
        unreads_text: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
    ) -> SubAgentDecision:
        """执行子代理判定。"""
        ...

    def _upsert_pending_unread_payload(
        self,
        response: LLMConversationState,
        formatted_text: str,
    ) -> None:
        """将未读消息写入待发送上下文。"""
        ...

    async def flush_unreads(self, unread_messages: list[Message]) -> int:
        """清空已处理未读消息。"""
        ...

    async def run_tool_call(
        self,
        call: ToolCall,
        response: LLMResponseLike,
        usable_map: ToolRegistry,
        trigger_msg: Message | None,
    ) -> tuple[bool, bool]:
        """执行单个工具调用。"""
        ...

    async def _build_classical_user_text(
        self,
        chat_stream: ChatStream,
        unread_msgs: list[Message],
    ) -> str:
        """构建 classical 模式用户提示词。"""
        ...


class SupportsRequestCreation(Protocol):
    """支持创建 LLM 请求的最小能力集合。"""

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        max_context: int | None = None,
        with_reminder: str | None = None,
    ) -> LLMRequest:
        """创建 LLM 请求。"""
        ...