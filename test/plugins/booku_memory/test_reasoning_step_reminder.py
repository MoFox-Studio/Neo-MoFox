"""Tests for booku_memory agent reasoning-step reminders.

These tests ensure that when the internal tool-loop reaches the configured
reasoning step limit, the agent injects a strong reminder that forces the
model to call ``finish_task`` immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.kernel.llm import LLMPayload, ROLE, Text, ToolCall
from src.kernel.llm.context import LLMContextManager


@dataclass
class _DummyPlugin:
    """Minimal plugin stub for agent construction."""

    config: Any = None


class _FakeResponse:
    """Minimal awaitable response object used to drive the agent loop."""

    def __init__(self, *, payloads: list[LLMPayload], context_manager: LLMContextManager, call_list: list[ToolCall]):
        self.payloads = payloads
        self.context_manager = context_manager
        self.call_list = call_list
        self._send_impl: Any = None
        self._appended_assistant: bool = False

    def __await__(self):
        if not self._appended_assistant:
            self._appended_assistant = True
            assistant_parts: list[Any] = []
            if self.call_list:
                assistant_parts.extend(self.call_list)
            if assistant_parts:
                self.payloads = self.context_manager.add_payload(
                    self.payloads,
                    LLMPayload(ROLE.ASSISTANT, assistant_parts),  # type: ignore[arg-type]
                )
        if False:  # pragma: no cover
            yield None
        return None

    def add_payload(self, payload: LLMPayload, position=None):
        if self.context_manager is not None:
            self.payloads = self.context_manager.add_payload(
                self.payloads,
                payload,
                position=int(position) if position is not None else None,
            )
            return self
        if position is not None:
            self.payloads.insert(int(position), payload)
        else:
            self.payloads.append(payload)
        return self

    async def send(self, stream: bool = False):
        del stream
        if self._send_impl is None:
            raise RuntimeError("_send_impl not set")
        return await self._send_impl(self)


class _FakeRequest:
    """Minimal request object used by the agent; only what execute() needs."""

    def __init__(self):
        self.context_manager = LLMContextManager()
        self.payloads: list[LLMPayload] = []
        self._next_index = 0
        self.saw_force_finish = False
        self.seen_system_texts: list[str] = []

    def add_payload(self, payload: LLMPayload, position=None):
        self.payloads = self.context_manager.add_payload(
            self.payloads,
            payload,
            position=int(position) if position is not None else None,
        )
        return self

    async def send(self, stream: bool = False):
        del stream

        async def _send_impl(resp: _FakeResponse):
            # On the last follow-up send, the agent should have injected a strong
            # SYSTEM reminder mentioning finish_task.
            for payload in resp.payloads:
                if payload.role != ROLE.SYSTEM:
                    continue
                for part in payload.content:
                    if not isinstance(part, Text):
                        continue
                    self.seen_system_texts.append(part.text)
                    if "你已到达最后一轮" in part.text and "finish_task" in part.text:
                        self.saw_force_finish = True

            self._next_index += 1
            if self._next_index == 1:
                # First follow-up response: still asks for tools.
                nxt = _FakeResponse(
                    payloads=resp.payloads,
                    context_manager=resp.context_manager,
                    call_list=[ToolCall(id="call_2", name="tool-status", args={})],
                )
                nxt._send_impl = _send_impl
                return nxt
            # After the last follow-up, end with no calls.
            nxt = _FakeResponse(
                payloads=resp.payloads,
                context_manager=resp.context_manager,
                call_list=[],
            )
            nxt._send_impl = _send_impl
            return nxt

        # Initial response always contains a tool call.
        resp = _FakeResponse(
            payloads=list(self.payloads),
            context_manager=self.context_manager,
            call_list=[ToolCall(id="call_1", name="tool-retrieve", args={})],
        )
        resp._send_impl = _send_impl
        return resp


@pytest.mark.asyncio
async def test_read_agent_injects_force_finish_reminder_on_last_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """The read agent must force-finish on the last allowed follow-up round."""

    from plugins.booku_memory.agent.read_agent import BookuMemoryReadAgent

    # Avoid accessing real model config.
    monkeypatch.setattr(
        "plugins.booku_memory.agent.read_agent.get_model_set_by_task",
        lambda _task: [{"extra_params": {}}],
    )

    agent = BookuMemoryReadAgent(stream_id="s", plugin=_DummyPlugin())
    monkeypatch.setattr(agent, "_max_reasoning_steps", lambda: 2)

    fake_request = _FakeRequest()
    monkeypatch.setattr(agent, "create_llm_request", lambda **_kwargs: fake_request)

    async def _exec_local_usable(*_a: Any, **_k: Any) -> tuple[bool, Any]:
        return True, {"ok": True}

    monkeypatch.setattr(agent, "execute_local_usable", _exec_local_usable)

    ok, _result = await agent.execute(
        intent_text="x",
        core_tags=[],
        diffusion_tags=[],
        opposing_tags=[],
        context="",
        include_archived=False,
    )

    assert ok is False
    assert fake_request.saw_force_finish is True, fake_request.seen_system_texts
