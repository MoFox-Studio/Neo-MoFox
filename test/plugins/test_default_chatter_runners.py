"""default_chatter.runners 模块测试。

聚焦 enhanced 模式在 strict 校验下的真实运行场景：
当上下文以 TOOL_RESULT 结尾时（工具链未闭合），即使收到新未读消息，
也必须优先完成工具续轮，避免出现 tool_result -> user 的非法序列。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from plugins.default_chatter.runners import run_enhanced
from src.core.components.base import Stop
from src.kernel.llm import ROLE


@dataclass
class _FakePayload:
    """最小 payload。"""

    role: str


class _FakeResponse:
    """最小 response/request 对象。

    - 具备 payloads
    - 具备 send/await 行为
    - 具备 message/call_list 供 runner 分支判断
    """

    def __init__(self, payload_roles: list[str], *, message: str = "ok") -> None:
        self.payloads: list[_FakePayload] = [_FakePayload(r) for r in payload_roles]
        self.message: str = message
        self.call_list: list[Any] = []
        self.send_count: int = 0

    def add_payload(self, payload: Any) -> None:
        role = getattr(payload, "role", None)
        if role == ROLE.SYSTEM:
            self.payloads.insert(0, _FakePayload(str(role)))
            return
        self.payloads.append(_FakePayload(str(role)))

    async def send(self, *, stream: bool = False) -> "_FakeResponse":
        _ = stream
        self.send_count += 1
        return self

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _done() -> "_FakeResponse":
            return self

        return _done().__await__()


class _FakeToolRegistry:
    """最小 ToolRegistry 替身。"""

    def get_all(self) -> list[Any]:
        return []


class _FakeChatter:
    """为 run_enhanced 提供所需接口的最小 chatter 替身。"""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.create_request_calls: list[tuple[str, str | None]] = []

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        max_context: int | None = None,
        with_reminder: str | None = None,
    ) -> _FakeResponse:
        _ = (request_name, max_context)
        self.create_request_calls.append((task, with_reminder))
        return self._response

    async def _build_system_prompt(self, _chat_stream: Any) -> str:
        return "sys"

    def _build_enhanced_history_text(self, _chat_stream: Any) -> str:
        return "hist"

    async def inject_usables(self, _request: Any) -> _FakeToolRegistry:
        return _FakeToolRegistry()

    async def fetch_unreads(self) -> tuple[str, list[Any]]:
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
        _ = (history_text, unread_lines, extra)
        return "user"

    def _build_negative_behaviors_extra(self) -> str:
        return ""

    async def _build_classical_user_text(
        self,
        _chat_stream: Any,
        _unread_msgs: list[Any],
    ) -> str:
        return "user"

    async def sub_agent(self, *_args: Any, **_kwargs: Any) -> dict:
        return {"reason": "", "should_respond": True}

    async def run_tool_call(self, *_args: Any, **_kwargs: Any) -> tuple[bool, bool]:
        return True, True

    @staticmethod
    def _upsert_pending_unread_payload(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "当 payload 尾部为 TOOL_RESULT 时，不应注入 USER（应先续轮闭合工具链）"
        )

    async def flush_unreads(self, _unread_messages: list[Any]) -> int:
        raise AssertionError("工具续轮阶段不应 flush 新未读")


@pytest.mark.asyncio
async def test_run_enhanced_prioritizes_tool_followup_when_tool_result_tail() -> None:
    """当上下文尾部是 TOOL_RESULT 时，应优先续轮，不注入 USER。

    该测试模拟：上一轮工具调用完成并写回 TOOL_RESULT，但尚未发送 follow-up
    承接工具结果；此时又来了新未读消息。

    期望：runner 不会调用 _upsert_pending_unread_payload，且不会 flush 新未读，
    而是直接发送一次 follow-up 并结束（由 FakeResponse 行为触发 Stop）。
    """

    fake_response = _FakeResponse(
        payload_roles=[ROLE.USER, ROLE.ASSISTANT, ROLE.TOOL_RESULT],
        message="finish",
    )
    chatter = _FakeChatter(fake_response)

    chat_stream = cast(Any, SimpleNamespace(stream_id="s1"))
    fake_logger = cast(
        Any,
        SimpleNamespace(
            info=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
            error=lambda *_a, **_k: None,
        ),
    )

    gen = run_enhanced(
        chatter=cast(Any, chatter),
        chat_stream=chat_stream,
        logger=fake_logger,
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        suspend_text="__SUSPEND__",
    )

    result = await anext(gen)
    assert isinstance(result, Stop)
    assert chatter.create_request_calls == [("actor", "actor")]


@pytest.mark.asyncio
async def test_run_enhanced_does_not_yield_wait_when_pending_tool_results(monkeypatch: Any) -> None:
    """当 should_wait 与 pending_tool_results 并存时，必须先 follow-up。

    该场景模拟模型给出了矛盾控制流：既要求等待，又发起了需要后续推理的工具调用。
    我们的约束是：先闭合 tool_result → assistant（follow-up），再进入等待。
    """

    # 1) 让 response 第一次 send 有 call_list，第二次 send 变为纯文本以结束。
    resp = _FakeResponse(payload_roles=[ROLE.USER, ROLE.ASSISTANT, ROLE.TOOL_RESULT], message="")

    async def _send(*, stream: bool = False) -> _FakeResponse:
        _ = stream
        resp.send_count += 1
        if resp.send_count == 1:
            resp.call_list = [SimpleNamespace(name="tool-x", args={}, id="1")]
            resp.message = ""
        else:
            resp.call_list = []
            resp.message = "finish"
        return resp

    resp.send = _send  # type: ignore[method-assign]

    # 2) monkeypatch process_tool_calls：返回 should_wait=True 且 has_pending_tool_results=True
    from plugins.default_chatter import runners as runners_mod

    async def _fake_process_tool_calls(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            should_wait=True,
            should_stop=False,
            stop_minutes=0.0,
            sent_once=False,
            has_pending_tool_results=True,
        )

    monkeypatch.setattr(runners_mod, "process_tool_calls", _fake_process_tool_calls)

    # 3) chatter 依然需要提供接口，但不应注入 USER/flush。
    chatter = _FakeChatter(resp)
    chat_stream = cast(Any, SimpleNamespace(stream_id="s1"))
    fake_logger = cast(
        Any,
        SimpleNamespace(
            info=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
            error=lambda *_a, **_k: None,
        ),
    )

    gen = run_enhanced(
        chatter=cast(Any, chatter),
        chat_stream=chat_stream,
        logger=fake_logger,
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        suspend_text="__SUSPEND__",
    )

    first = await anext(gen)
    assert isinstance(first, Stop)
    assert resp.send_count == 2
    assert chatter.create_request_calls == [("actor", "actor")]
