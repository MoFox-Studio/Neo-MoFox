"""default_chatter.decision_agent 模块测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.default_chatter.decision_agent import (
    _fit_unreads_to_sub_agent_budget,
    decide_should_respond,
)


@pytest.mark.asyncio
async def test_fit_unreads_keeps_text_when_within_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当输入未超过预算时应保持原样。"""

    monkeypatch.setattr(
        "plugins.default_chatter.decision_agent._safe_count_tokens",
        lambda text, _model_identifier: len(text),
    )

    request = SimpleNamespace(
        model_set=[{"model_identifier": "demo-model", "max_context": 32768}]
    )
    text = "line-1\nline-2"

    fitted = _fit_unreads_to_sub_agent_budget(request, text)

    assert fitted == text


@pytest.mark.asyncio
async def test_fit_unreads_trims_old_prefix_when_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过预算时应裁剪前缀，优先保留最新未读内容。"""

    monkeypatch.setattr(
        "plugins.default_chatter.decision_agent._safe_count_tokens",
        lambda text, _model_identifier: len(text),
    )

    request = SimpleNamespace(
        model_set=[{"model_identifier": "demo-model", "max_context": 4096}]
    )
    long_text = "old-message\n" + ("x" * 1500) + "\nlatest-message"

    fitted = _fit_unreads_to_sub_agent_budget(request, long_text)

    assert "latest-message" in fitted
    assert len(fitted) <= 1024


@pytest.mark.asyncio
async def test_decide_should_respond_requests_sub_actor_reminder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理创建请求时应传入 sub_actor reminder bucket。"""

    captured: dict[str, Any] = {}

    class _FakeResponse:
        message = '{"should_respond": true, "reason": "ok"}'

        def add_payload(self, _payload: Any) -> None:
            return None

        async def send(self, stream: bool = False) -> "_FakeResponse":
            _ = stream
            return self

        def __await__(self):  # type: ignore[no-untyped-def]
            async def _done() -> "_FakeResponse":
                return self

            return _done().__await__()

    class _FakeChatter:
        def create_request(
            self,
            task: str = "actor",
            request_name: str = "",
            max_context: int | None = None,
            with_reminder: str | None = None,
        ) -> _FakeResponse:
            captured["task"] = task
            captured["request_name"] = request_name
            captured["max_context"] = max_context
            captured["with_reminder"] = with_reminder
            return _FakeResponse()

    monkeypatch.setattr(
        "plugins.default_chatter.decision_agent.get_core_config",
        lambda: SimpleNamespace(personality=SimpleNamespace(nickname="Neo")),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.decision_agent.get_prompt_manager",
        lambda: SimpleNamespace(get_template=lambda _name: None),
    )

    result = await decide_should_respond(
        chatter=_FakeChatter(),
        logger=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None, debug=lambda *_a, **_k: None, error=lambda *_a, **_k: None),
        unreads_text="hello",
        chat_stream=SimpleNamespace(stream_id="s1"),
        fallback_prompt="hello {nickname}",
    )

    assert result["should_respond"] is True
    assert captured == {
        "task": "sub_actor",
        "request_name": "sub_agent",
        "max_context": 5,
        "with_reminder": "sub_actor",
    }
