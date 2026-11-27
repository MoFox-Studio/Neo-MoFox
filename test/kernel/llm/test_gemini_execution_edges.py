"""Exceptional integration paths in the shared Gemini attempt/response seam."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Never
from unittest.mock import patch

import pytest

from src.kernel.event.core import EventHandlerCallable, EventHandlerResult, EventParams
from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
from src.kernel.llm.exceptions import LLMContextError, LLMAPIError
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream
from src.kernel.llm.policy.base import ModelStep
from src.kernel.llm.response import LLMResponse
from test.kernel.llm.test_gemini_client import model


async def test_locked_round_without_configured_target_rejects() -> None:
    """A removed source is a context error, never a fallback to another model."""
    req=LLMRequest(model_set=[model()],enable_metrics=False,_locked_target=('missing','https://missing.invalid/v1beta','test'))
    with pytest.raises(LLMContextError,match='no matching configured target'):
        await req.send(stream=False)


async def test_terminal_observer_failure_does_not_break_generation() -> None:
    """A failed terminal event subscriber cannot alter successful HTTP output."""
    class Provider:
        """返回固定文本事件的供应商桩，保留旧五元组结果契约。"""
        async def create(self, **kwargs: object) -> tuple[None, None, AsyncIterator[StreamEvent], None, None]:
            """接受模型请求关键字参数，并返回仅含事件流的结果。"""
            async def events() -> AsyncIterator[StreamEvent]:
                """产出一个成功文本片段。"""
                yield StreamEvent(text_delta='ok')
            return None,None,events(),None,None
    class Bus:
        """发布时失败的事件总线桩，用于验证观察者错误隔离。"""
        def get_subscribers(self, name: str) -> list[EventHandlerCallable]:
            """返回指定事件的空订阅者列表。"""
            return []
        async def publish(self, name: str, params: EventParams) -> EventHandlerResult:
            """模拟发布指定事件及参数时发生观察者异常。"""
            raise RuntimeError('observer failed')
    m = model()
    m['client_type'] = 'openai'
    req=LLMRequest(model_set=[m],clients=ModelClientRegistry(openai=Provider()),policy=RoundRobinPolicy(),enable_metrics=False)
    req.context_manager = None
    req.payloads = [LLMPayload(ROLE.USER, Text('hi'))]
    with patch('src.kernel.event.get_event_bus',return_value=Bus()):
        result=await req.send(stream=True)
        assert await result == 'ok'


async def test_managed_failed_tool_never_remains_executable() -> None:
    """A partial call is cleared even when collecting the complete response fails."""
    class Session:
        """遇到错误即耗尽的策略会话桩。"""
        def next_after_error(self, error: BaseException) -> ModelStep:
            """对传入错误返回无可用模型的终止步骤。"""
            return ModelStep(model=None)
    async def source() -> AsyncIterator[StreamEvent]:
        """先产出半截工具参数，再抛出不可恢复的供应商错误。"""
        yield StreamEvent(tool_call_id='c',tool_name='danger',tool_args_delta='{')
        raise LLMAPIError('bad',status_code=400)
    async def terminal(*args: BaseException | dict[str, Any] | float | str | None) -> None:
        """接收错误、用量、耗时和阶段等终态参数，不添加副作用。"""
        pass
    async def resume(context: AttemptContext) -> Never:
        """若耗尽的尝试上下文仍请求恢复，立即使测试失败。"""
        raise AssertionError('no retry')
    stream=ManagedStream(source(),timeout=1,started_at=__import__('time').perf_counter(),context=AttemptContext(Session(),ModelStep(model={})),terminal=terminal,resume=resume)
    response=LLMResponse(_stream=stream,_upper=object(),_auto_append_response=False,payloads=[],model_set=[])
    with pytest.raises(LLMAPIError):
        await response
    assert response.call_list==[]
    assert response.provider_state is None
