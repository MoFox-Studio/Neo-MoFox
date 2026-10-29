"""Exceptional integration paths in the shared Gemini attempt/response seam."""
import asyncio
from unittest.mock import patch

import pytest

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
        async def create(self,**kwargs):
            async def events():yield StreamEvent(text_delta='ok')
            return None,None,events(),None,None
    class Bus:
        def get_subscribers(self,name):return []
        async def publish(self,name,params):raise RuntimeError('observer failed')
    m=model();m['client_type']='openai'
    req=LLMRequest(model_set=[m],clients=ModelClientRegistry(openai=Provider()),policy=RoundRobinPolicy(),enable_metrics=False)
    req.context_manager=None;req.payloads=[LLMPayload(ROLE.USER,Text('hi'))]
    with patch('src.kernel.event.get_event_bus',return_value=Bus()):
        result=await req.send(stream=True)
        assert await result == 'ok'


async def test_managed_failed_tool_never_remains_executable() -> None:
    """A partial call is cleared even when collecting the complete response fails."""
    class Session:
        def next_after_error(self,error):return ModelStep(model=None)
    async def source():
        yield StreamEvent(tool_call_id='c',tool_name='danger',tool_args_delta='{')
        raise LLMAPIError('bad',status_code=400)
    async def terminal(*args):pass
    async def resume(context):raise AssertionError('no retry')
    stream=ManagedStream(source(),timeout=1,started_at=__import__('time').perf_counter(),context=AttemptContext(Session(),ModelStep(model={})),terminal=terminal,resume=resume)
    response=LLMResponse(_stream=stream,_upper=object(),_auto_append_response=False,payloads=[],model_set=[])
    with pytest.raises(LLMAPIError):await response
    assert response.call_list==[]
    assert response.provider_state is None
