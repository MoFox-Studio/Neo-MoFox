from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from plugins.default_chatter.plugin import DefaultChatter
from plugins.default_chatter.service import DefaultChatterService
from plugins.default_chatter.session import (
    DefaultChatterSession,
    _build_actor_decision_panel,
    collect_used_tool_names,
)
from plugins.default_chatter.type_defs import (
    DefaultChatterSessionAdapters,
    DefaultChatterSessionOptions,
)
from src.app.plugin_system.api.llm_api import create_llm_request
from src.core.components.base import Stop, Success, Wait, WaitResumeEvent
from src.core.models.stream import ChatStream
from src.core.prompt.system_reminder import get_system_reminder_store, reset_system_reminder_store
from src.kernel.llm import ROLE
from src.kernel.llm.payload import LLMPayload, Text


@dataclass
class _FakePayload:
    role: str
    content: list[Any] | None = None


class _FakeResponse:
    def __init__(
        self,
        payload_roles: list[str],
        *,
        message: str = "ok",
        reasoning_content: str | None = None,
        model_set: list[dict[str, object]] | None = None,
    ) -> None:
        self.payloads: list[_FakePayload] = [_FakePayload(r, []) for r in payload_roles]
        self.message: str = message
        self.reasoning_content: str | None = reasoning_content
        self.call_list: list[Any] = []
        self.send_count: int = 0
        self.model_set: list[dict[str, object]] = model_set or []

    def add_payload(self, payload: Any) -> None:
        role = getattr(payload, "role", None)
        content = list(getattr(payload, "content", None) or [])
        if role == ROLE.SYSTEM:
            self.payloads.insert(0, _FakePayload(str(role), content))
            return
        self.payloads.append(_FakePayload(str(role), content))

    async def send(self, *, stream: bool = False) -> "_FakeResponse":
        _ = stream
        self.send_count += 1
        return self

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _done() -> "_FakeResponse":
            return self

        return _done().__await__()


class _FakeToolRegistry:
    def get_all(self) -> list[Any]:
        return []


class _FakeLogger:
    def __init__(self) -> None:
        self.panels: list[tuple[str, str | None, str | None]] = []

    def info(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def warning(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def error(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def debug(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def print_panel(
        self,
        message: str,
        title: str | None = None,
        border_style: str | None = None,
    ) -> None:
        self.panels.append((message, title, border_style))


class _FakeRuntime:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.create_request_calls: list[tuple[str, str | None]] = []
        self.stream_id = "s1"

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        with_reminder: str | None = None,
    ) -> _FakeResponse:
        _ = request_name
        self.create_request_calls.append((task, with_reminder))
        return self._response

    async def _build_system_prompt(self, _chat_stream: Any) -> str:
        return "sys"

    def _build_enhanced_history_text(self, _chat_stream: Any) -> str:
        return "hist"

    async def inject_usables(self, _request: Any) -> _FakeToolRegistry:
        return _FakeToolRegistry()

    async def fetch_unreads(self, time_format: str = "%H:%M") -> tuple[str, list[Any]]:
        _ = time_format
        return "", [SimpleNamespace(message_id="m1")]

    def format_message_line(self, _msg: Any, _time_format: str = "%H:%M") -> str:
        return "line"

    async def _build_user_prompt(
        self,
        _chat_stream: Any,
        history_text: str,
        unread_lines: str,
        extra: str = "",
    ) -> str:
        _ = history_text, unread_lines, extra
        return "user"

    def _build_negative_behaviors_extra(self) -> str:
        return ""

    async def sub_agent(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"reason": "", "should_respond": True}

    async def run_tool_call(
        self,
        calls: list[Any],
        *_args: Any,
        **_kwargs: Any,
    ) -> list[tuple[bool, bool]]:
        return [(True, True) for _call in calls]

    def _upsert_pending_unread_payload(
        self,
        response: Any,
        formatted_text: str,
        unread_msgs: list[Any] | None = None,
        native_multimodal: bool = False,
        logger_override: Any = None,
    ) -> None:
        _ = formatted_text, unread_msgs, native_multimodal, logger_override
        raise AssertionError("This branch should not inject a USER payload in this test.")

    async def flush_unreads(self, _unread_messages: list[Any]) -> int:
        raise AssertionError("This branch should not flush unread messages in this test.")


class _FakeRuntimeAllowUser(_FakeRuntime):
    def _upsert_pending_unread_payload(
        self,
        response: Any,
        formatted_text: str,
        unread_msgs: list[Any] | None = None,
        native_multimodal: bool = False,
        logger_override: Any = None,
    ) -> None:
        _ = unread_msgs, native_multimodal, logger_override
        response.add_payload(
            SimpleNamespace(role=ROLE.USER, content=[Text(formatted_text)])
        )

    async def flush_unreads(self, _unread_messages: list[Any]) -> int:
        return 0


class _FakeRuntimeWithUnreadSequence(_FakeRuntimeAllowUser):
    def __init__(self, response: _FakeResponse, unread_batches: list[list[Any]]) -> None:
        super().__init__(response)
        self._unread_batches = unread_batches
        self._fetch_index = 0

    async def fetch_unreads(self, time_format: str = "%H:%M") -> tuple[str, list[Any]]:
        _ = time_format
        if self._fetch_index >= len(self._unread_batches):
            return "", []
        batch = self._unread_batches[self._fetch_index]
        self._fetch_index += 1
        return "", batch


def _build_session(
    runtime: Any,
    *,
    logger: _FakeLogger | None = None,
    options: DefaultChatterSessionOptions | None = None,
) -> DefaultChatterSession:
    fake_logger = logger or _FakeLogger()
    return DefaultChatterSession(
        stream_id=runtime.stream_id,
        options=options or DefaultChatterSessionOptions(),
        adapters=DefaultChatterSessionAdapters(
            request_adapter=runtime,
            prompt_adapter=runtime,
            unread_adapter=runtime,
            usable_adapter=runtime,
            tool_execution_adapter=runtime,
            sub_agent_adapter=runtime,
            logger_adapter=fake_logger,
            plain_text_adapter=(
                runtime if hasattr(runtime, "handle_plain_text_response") else None
            ),
        ),
    )


def _make_chat_stream(
    *,
    stream_id: str = "s1",
    stream_name: str = "test",
    platform: str = "test-platform",
    chat_type: str = "private",
) -> ChatStream:
    return ChatStream(
        stream_id=stream_id,
        stream_name=stream_name,
        platform=platform,
        chat_type=chat_type,
    )


def test_collect_used_tool_names_dedups_and_filters_empty_names() -> None:
    calls = [SimpleNamespace(name="search_web"), SimpleNamespace(name="memory_command")]
    assert collect_used_tool_names(calls) == {"search_web", "memory_command"}
    assert collect_used_tool_names(
        [
            SimpleNamespace(name="search_web"),
            SimpleNamespace(name="search_web"),
            SimpleNamespace(name=""),
        ]
    ) == {"search_web"}


def test_upsert_pending_unread_payload_keeps_fixed_reminder_on_first_user_only() -> None:
    reset_system_reminder_store()
    store = get_system_reminder_store()
    store.set("actor", "booku_memory", "Booku Memory command manual")

    request = create_llm_request(
        model_set=[
            {
                "api_provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "model_identifier": "test-model",
                "api_key": "sk-test-key",
                "client_type": "openai",
                "max_retry": 1,
                "timeout": 5.0,
                "retry_interval": 0.0,
                "price_in": 0.0,
                "cache_hit_price_in": 0.0,
                "price_out": 0.0,
                "temperature": 0.0,
                "max_tokens": 1024,
                "max_context": 8192,
                "tool_call_compat": False,
                "extra_params": {},
            }
        ],
        request_name="chat_test",
        with_reminder="actor",
    )

    DefaultChatter._upsert_pending_unread_payload(request, "first")
    request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("ack 1")))
    DefaultChatter._upsert_pending_unread_payload(request, "second")
    request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("ack 2")))
    DefaultChatter._upsert_pending_unread_payload(request, "third")

    user_payloads = [payload for payload in request.payloads if payload.role == ROLE.USER]
    assert len(user_payloads) == 3
    assert isinstance(user_payloads[0].content[0], Text)
    assert user_payloads[0].content[0].text == (
        "<system_reminder>\n[booku_memory]\nBooku Memory command manual\n</system_reminder>"
    )

    for payload in user_payloads[1:]:
        assert all(
            not isinstance(part, Text)
            or part.text
            != "<system_reminder>\n[booku_memory]\nBooku Memory command manual\n</system_reminder>"
            for part in payload.content
        )

    reset_system_reminder_store()


@pytest.mark.asyncio
async def test_session_execute_with_stream_consumes_sub_agent_results_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CaptureRuntime(_FakeRuntimeAllowUser):
        def __init__(self, response: _FakeResponse) -> None:
            super().__init__(response)
            self.upsert_texts: list[str] = []

        def _upsert_pending_unread_payload(
            self,
            response: Any,
            formatted_text: str,
            unread_msgs: list[Any] | None = None,
            native_multimodal: bool = False,
            logger_override: Any = None,
        ) -> None:
            _ = unread_msgs, native_multimodal, logger_override
            self.upsert_texts.append(formatted_text)
            response.add_payload(SimpleNamespace(role=ROLE.USER))

    response = _FakeResponse(payload_roles=[ROLE.USER], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        response.call_list = [SimpleNamespace(name="action-pass_and_wait", args={}, id="1")]
        response.message = ""
        return response

    response.send = _send  # type: ignore[method-assign]
    runtime = _CaptureRuntime(response)
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()

    class _FakeManager:
        def __init__(self) -> None:
            self.calls = 0

        def drain_completed_events(self, stream_id: str) -> list[dict[str, Any]]:
            assert stream_id == "s1"
            self.calls += 1
            if self.calls == 1:
                return [{"name": "worker", "status": "completed", "content": "task done"}]
            return []

    fake_manager = _FakeManager()
    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.get_sub_agent_collaboration_manager",
        lambda: fake_manager,
    )

    gen = session.execute_with_stream(chat_stream, apply_stop_wake_config=False)

    first = await anext(gen)
    assert isinstance(first, Wait)

    second = await gen.asend(WaitResumeEvent(source="sub_agent"))
    assert isinstance(second, Wait)
    assert [text for text in runtime.upsert_texts if "task done" in text] == [
        "以下是子代理刚刚返回的结果，请基于这些结果继续处理：\n[worker] completed\ntask done"
    ]
    assert fake_manager.calls == 1


@pytest.mark.asyncio
async def test_session_execute_with_stream_merges_unread_during_tool_followup() -> None:
    class _CaptureRuntime(_FakeRuntimeWithUnreadSequence):
        def __init__(self, response: _FakeResponse, unread_batches: list[list[Any]]) -> None:
            super().__init__(response, unread_batches)
            self.upsert_texts: list[str] = []
            self.flushed_batches: list[list[Any]] = []

        def _upsert_pending_unread_payload(
            self,
            response: Any,
            formatted_text: str,
            unread_msgs: list[Any] | None = None,
            native_multimodal: bool = False,
            logger_override: Any = None,
        ) -> None:
            _ = unread_msgs, native_multimodal, logger_override
            self.upsert_texts.append(formatted_text)
            response.add_payload(SimpleNamespace(role=ROLE.USER, content=[Text(formatted_text)]))

        async def flush_unreads(self, unread_messages: list[Any]) -> int:
            self.flushed_batches.append(list(unread_messages))
            return len(unread_messages)

    unread_msg = SimpleNamespace(message_id="m1")
    response = _FakeResponse(
        payload_roles=[ROLE.USER, ROLE.ASSISTANT, ROLE.TOOL_RESULT],
        message="finish",
    )
    runtime = _CaptureRuntime(response, unread_batches=[[unread_msg]])
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()
    logger = cast(_FakeLogger, session.logger)

    result = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(result, Stop)
    assert runtime.create_request_calls == [("actor", "actor")]
    assert runtime.upsert_texts == ["user"]
    assert runtime.flushed_batches == [[unread_msg]]
    assert [str(payload.role) for payload in response.payloads[-2:]] == [
        str(ROLE.TOOL_RESULT),
        str(ROLE.USER),
    ]
    assert logger.panels == []


@pytest.mark.asyncio
async def test_session_execute_with_stream_does_not_yield_wait_when_pending_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER, ROLE.ASSISTANT, ROLE.TOOL_RESULT], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        if response.send_count == 1:
            response.call_list = [SimpleNamespace(name="tool-x", args={}, id="1")]
            response.message = ""
        else:
            response.call_list = []
            response.message = "finish"
        return response

    response.send = _send  # type: ignore[method-assign]
    runtime = _FakeRuntimeAllowUser(response)
    logger = _FakeLogger()
    session = _build_session(runtime, logger=logger)
    chat_stream = _make_chat_stream()
    expected_panels: list[str] = []

    async def _fake_process_tool_calls(**kwargs: Any) -> Any:
        expected_panels.append(
            _build_actor_decision_panel(chat_stream, cast(Any, kwargs["response"]))
        )
        return SimpleNamespace(
            should_wait=True,
            should_stop=False,
            stop_minutes=0.0,
            has_pending_tool_results=True,
        )

    monkeypatch.setattr(
        "plugins.default_chatter.session.process_tool_calls",
        _fake_process_tool_calls,
    )

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Stop)
    assert response.send_count == 2
    assert runtime.create_request_calls == [("actor", "actor")]
    assert logger.panels == [(expected_panels[0], "Actor 决策", "cyan")]


@pytest.mark.asyncio
async def test_session_execute_with_stream_prints_actor_decision_panel_before_processing_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(
        payload_roles=[ROLE.USER],
        message="reply first, then call tools",
        reasoning_content="think first",
    )
    response.call_list = [
        SimpleNamespace(name="tool-x", args={"reason": "test", "foo": "bar"}, id="1"),
        SimpleNamespace(name="tool-y", args={"count": 2}, id="2"),
    ]

    async def _fake_process_tool_calls(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            should_wait=True,
            should_stop=False,
            stop_minutes=0.0,
            has_pending_tool_results=False,
        )

    monkeypatch.setattr(
        "plugins.default_chatter.session.process_tool_calls",
        _fake_process_tool_calls,
    )

    runtime = _FakeRuntimeAllowUser(response)
    logger = _FakeLogger()
    session = _build_session(runtime, logger=logger)
    chat_stream = _make_chat_stream()

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Wait)
    assert logger.panels == [
        (_build_actor_decision_panel(chat_stream, cast(Any, response)), "Actor 决策", "cyan")
    ]


@pytest.mark.asyncio
async def test_session_execute_with_stream_waits_after_anthropic_action_only_suspend() -> None:
    response = _FakeResponse(
        payload_roles=[ROLE.USER],
        message="",
        model_set=[{"client_type": "anthropic"}],
    )
    response.call_list = [SimpleNamespace(name="action-send_text", args={}, id="1")]
    response.reasoning_content = "think"

    runtime = _FakeRuntimeAllowUser(response)
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Wait)
    assert response.send_count == 1


@pytest.mark.asyncio
async def test_session_execute_with_stream_action_only_follows_up_when_suspend_disabled() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        if response.send_count == 1:
            response.call_list = [SimpleNamespace(name="action-send_text", args={}, id="1")]
            response.message = ""
        else:
            response.call_list = []
            response.message = "finish"
        return response

    response.send = _send  # type: ignore[method-assign]
    runtime = _FakeRuntimeAllowUser(response)
    session = _build_session(
        runtime,
        options=DefaultChatterSessionOptions(enable_action_suspend=False),
    )
    chat_stream = _make_chat_stream()

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Stop)
    assert response.send_count == 2


@pytest.mark.asyncio
async def test_session_execute_with_stream_retries_plain_text_with_adapter_hook() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="spoken text")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        if response.send_count == 1:
            response.call_list = []
            response.message = "spoken text"
        else:
            response.call_list = [SimpleNamespace(name="action-pass_and_wait", args={}, id="1")]
            response.message = ""
        return response

    response.send = _send  # type: ignore[method-assign]

    class _RetryRuntime(_FakeRuntimeAllowUser):
        def handle_plain_text_response(
            self,
            *,
            message: str,
            retry_count: int,
            response: Any,
        ) -> dict[str, str]:
            _ = message, response
            if retry_count == 0:
                return {"action": "retry", "reminder_text": "use tools"}
            return {"action": "wait", "reminder_text": ""}

    runtime = _RetryRuntime(response)
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Wait)
    assert response.send_count == 2
    assert any(str(payload.role) == str(ROLE.TOOL_RESULT) for payload in response.payloads)
    assert any(
        str(payload.role) == str(ROLE.USER)
        and any(getattr(part, "text", None) == "use tools" for part in (payload.content or []))
        for payload in response.payloads
    )


@pytest.mark.asyncio
async def test_session_execute_with_stream_pass_and_wait_still_waits_when_suspend_disabled() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")
    response.call_list = [
        SimpleNamespace(name="action-pass_and_wait", args={"seconds": 5}, id="1")
    ]

    runtime = _FakeRuntimeAllowUser(response)
    session = _build_session(
        runtime,
        options=DefaultChatterSessionOptions(enable_action_suspend=False),
    )
    chat_stream = _make_chat_stream()

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Wait)
    assert getattr(first, "time", None) == 5.0
    assert response.send_count == 1


@pytest.mark.asyncio
async def test_session_execute_with_stream_pass_and_wait_does_not_follow_up_immediately() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")
    response.call_list = [
        SimpleNamespace(name="action-pass_and_wait", args={"seconds": 5}, id="1")
    ]

    runtime = _FakeRuntimeWithUnreadSequence(
        response,
        unread_batches=[[SimpleNamespace(message_id="m1")], []],
    )
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()
    gen = session.execute_with_stream(chat_stream, apply_stop_wake_config=False)

    first = await anext(gen)
    assert isinstance(first, Wait)
    assert getattr(first, "time", None) == 5.0
    assert response.send_count == 1

    second = await gen.asend(None)
    assert isinstance(second, Wait)
    assert response.send_count == 1


@pytest.mark.asyncio
async def test_session_execute_with_stream_proactively_resumes_after_timed_wait() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        if response.send_count == 1:
            response.call_list = [
                SimpleNamespace(name="action-pass_and_wait", args={"seconds": 5}, id="1")
            ]
            response.message = ""
        else:
            response.call_list = []
            response.message = "finish"
        return response

    response.send = _send  # type: ignore[method-assign]
    runtime = _FakeRuntimeWithUnreadSequence(
        response,
        unread_batches=[[SimpleNamespace(message_id="m1")], []],
    )
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()
    gen = session.execute_with_stream(chat_stream, apply_stop_wake_config=False)

    first = await anext(gen)
    assert isinstance(first, Wait)
    assert first.time == 5.0

    second = await gen.asend(WaitResumeEvent(source="timer", wait_time=5.0))
    assert isinstance(second, Stop)
    assert response.send_count == 2


@pytest.mark.asyncio
async def test_session_execute_with_stream_uses_synthetic_trigger_message_for_timer_resume_tool_calls() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        response.call_list = [
            SimpleNamespace(name="action-send_text", args={"content": "continue"}, id="1")
        ]
        response.message = ""
        return response

    response.send = _send  # type: ignore[method-assign]

    class _CaptureRuntime(_FakeRuntimeWithUnreadSequence):
        def __init__(self, response: _FakeResponse) -> None:
            super().__init__(response, unread_batches=[[], []])
            self.trigger_messages: list[Any] = []

        async def run_tool_call(
            self,
            calls: list[Any],
            response: Any,
            usable_map: Any,
            trigger_msg: Any,
        ) -> list[tuple[bool, bool]]:
            _ = calls, response, usable_map
            self.trigger_messages.append(trigger_msg)
            return [(True, True)]

    runtime = _CaptureRuntime(response)
    session = _build_session(runtime)
    chat_stream = _make_chat_stream(platform="qq", chat_type="group")
    gen = session.execute_with_stream(chat_stream, apply_stop_wake_config=False)

    first = await anext(gen)
    assert isinstance(first, Wait)

    second = await gen.asend(WaitResumeEvent(source="timer", wait_time=5.0))
    assert isinstance(second, Wait)
    assert len(runtime.trigger_messages) == 1
    trigger_msg = runtime.trigger_messages[0]
    assert getattr(trigger_msg, "stream_id") == "s1"
    assert getattr(trigger_msg, "platform") == "qq"
    assert getattr(trigger_msg, "chat_type") == "group"
    assert "等待 5.0 秒已经结束" in str(getattr(trigger_msg, "processed_plain_text"))


@pytest.mark.asyncio
async def test_session_execute_with_stream_prefers_real_stream_message_for_resume_tool_calls() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        response.call_list = [
            SimpleNamespace(name="action-send_text", args={"content": "continue"}, id="1")
        ]
        response.message = ""
        return response

    response.send = _send  # type: ignore[method-assign]

    class _CaptureRuntime(_FakeRuntimeWithUnreadSequence):
        def __init__(self, response: _FakeResponse) -> None:
            super().__init__(response, unread_batches=[[], []])
            self.trigger_messages: list[Any] = []

        async def run_tool_call(
            self,
            calls: list[Any],
            response: Any,
            usable_map: Any,
            trigger_msg: Any,
        ) -> list[tuple[bool, bool]]:
            _ = calls, response, usable_map
            self.trigger_messages.append(trigger_msg)
            return [(True, True)]

    runtime = _CaptureRuntime(response)
    session = _build_session(runtime)
    real_message = SimpleNamespace(
        message_id="m-real",
        processed_plain_text="original message",
        content="original message",
        platform="qq",
        chat_type="group",
        stream_id="s1",
        extra={"group_id": "123"},
    )
    chat_stream = _make_chat_stream(platform="qq", chat_type="group")
    chat_stream.context.current_message = None
    chat_stream.context.unread_messages = []
    chat_stream.context.history_messages = [real_message]
    gen = session.execute_with_stream(chat_stream, apply_stop_wake_config=False)

    first = await anext(gen)
    assert isinstance(first, Wait)

    second = await gen.asend(WaitResumeEvent(source="sub_agent"))
    assert isinstance(second, Wait)
    assert runtime.trigger_messages == [real_message]


@pytest.mark.asyncio
async def test_session_execute_with_stream_supports_action_then_timed_wait() -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")
    response.call_list = [
        SimpleNamespace(name="action-send_text", args={"content": "back soon"}, id="1"),
        SimpleNamespace(name="action-pass_and_wait", args={"seconds": 6}, id="2"),
    ]

    runtime = _FakeRuntimeAllowUser(response)
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()

    first = await anext(session.execute_with_stream(chat_stream, apply_stop_wake_config=False))
    assert isinstance(first, Wait)
    assert getattr(first, "time", None) == 6.0
    assert getattr(first, "step_data", None) == {
        "step_scope": "actor_round",
        "used_tools": ["action-pass_and_wait", "action-send_text"],
    }
    assert response.send_count == 1


@pytest.mark.asyncio
async def test_session_execute_forwards_timer_resume_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(payload_roles=[ROLE.USER], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        response.send_count += 1
        if response.send_count == 1:
            response.call_list = [
                SimpleNamespace(name="action-pass_and_wait", args={"seconds": 5}, id="1")
            ]
            response.message = ""
        else:
            response.call_list = []
            response.message = "finish"
        return response

    response.send = _send  # type: ignore[method-assign]
    runtime = _FakeRuntimeWithUnreadSequence(
        response,
        unread_batches=[[SimpleNamespace(message_id="m1")], []],
    )
    session = _build_session(runtime)
    chat_stream = _make_chat_stream()
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: SimpleNamespace(activate_stream=AsyncMock(return_value=chat_stream)),
    )

    gen = session.execute()
    first = await anext(gen)
    assert isinstance(first, Wait)

    second = await gen.asend(WaitResumeEvent(source="timer", wait_time=5.0))
    assert isinstance(second, Stop)
    assert response.send_count == 2


@pytest.mark.asyncio
async def test_default_chatter_execute_delegates_to_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_events: list[WaitResumeEvent | None] = []

    class _FakeSession:
        async def execute(self) -> Any:
            resume_event = yield Wait(time=0.0)
            received_events.append(resume_event)
            yield Success(message="ok")

    monkeypatch.setattr(
        DefaultChatterService,
        "create_default_session",
        lambda self, **_kwargs: _FakeSession(),
    )

    chatter = DefaultChatter(
        stream_id="s1",
        plugin=cast(Any, SimpleNamespace(config=None)),
    )
    gen = chatter.execute()
    first = await anext(gen)
    assert isinstance(first, Wait)

    event = WaitResumeEvent(source="timer", wait_time=0.0)
    second = await gen.asend(event)

    assert isinstance(second, Success)
    assert received_events == [event]
