"""neo_default_chatter.tool_flow 模块测试。

重点覆盖控制流调用（pass/stop）与去重的交互：

- 「普通工具 + pass」同轮混用时应同时置上 ``should_wait`` 与
  ``has_pending_tool_results``（供 session.py 4.5 分支做等待优先决策）；
- pass/stop 作为幂等状态转换意图不参与去重、不登记 ``seen_signatures``，
  避免「上一轮 pass 意图被丢弃、签名残留 → 下一轮重复 pass 被跳过 →
  should_wait 永远为 False → 无限 FOLLOW_UP 空转」的死环；
- 普通工具调用的跨轮去重不受影响，仍按签名命中跳过。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from plugins.neo_default_chatter.utils import tool_flow as tool_flow_mod
from plugins.neo_default_chatter.utils.event_publisher import NdfcEvent

process_tool_calls = cast(Any, tool_flow_mod.process_tool_calls)
_build_call_dedupe_key = cast(Any, tool_flow_mod._build_call_dedupe_key)

_PASS = "action-pass_and_wait"
_STOP = "action-stop_conversation"


class _FakeResponse:
    """最小化响应对象。"""

    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def add_payload(self, payload: Any, position: object = None) -> None:
        """记录 payload。"""
        _ = position
        self.payloads.append(payload)


def _make_call(name: str, args: dict[str, Any] | None = None, call_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(name=name, args=args or {}, id=call_id)


def _install_fake_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], set[str]]:
    """用模拟默认 handler 行为的 fake 替换 ``publish_event``。

    - ``:dedupe_tool_call``：按 ``call_name:{json}`` 签名检查/登记共享集合，
      并把发布记录追加到 ``published``（用于断言控制流调用不触达去重）；
    - ``:format_tool_result``：返回 ``"[<kind>] <call_name>"``。

    Returns:
        (published, shared_seen)：发布记录列表 + 跨轮登记集合。
    """
    published: list[str] = []
    shared_seen: set[str] = set()

    async def _fake_publish(event, params=None):  # noqa: ANN001
        params = params or {}
        if event == NdfcEvent.DEDUPE_TOOL_CALL:
            published.append(str(event))
            call = params["call"]
            args = call.args if isinstance(call.args, dict) else {}
            dedupe_args = {k: v for k, v in args.items() if k != "reason"}
            key = _build_call_dedupe_key(call.name, dedupe_args)
            seen = params["seen_signatures"]
            if key in seen:
                params["is_duplicate"] = True
            else:
                seen.add(key)
                params["is_duplicate"] = False
        elif event == NdfcEvent.FORMAT_TOOL_RESULT:
            params["result_text"] = f"[{params['kind']}] {params['call_name']}"
        return {"decision": None, "params": params}

    monkeypatch.setattr(
        "plugins.neo_default_chatter.utils.event_publisher.publish_event",
        _fake_publish,
    )
    return published, shared_seen


async def _run(
    calls: list[Any],
    response: _FakeResponse,
    seen: set[str] | None = None,
) -> tuple[Any, list[str]]:
    """执行 process_tool_calls，返回 (outcome, called_names)。"""
    called_names: list[str] = []

    async def _run_tool_call(
        call_list: list[Any], _resp: Any, _usable: Any, _trigger: Any
    ) -> list[tuple[bool, bool]]:
        called_names.extend(call.name for call in call_list)
        return [(True, True) for _ in call_list]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name=_PASS,
        stop_call_name=_STOP,
        default_stop_minutes=5.0,
        logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None),
        seen_signatures=seen,
    )
    return outcome, called_names


@pytest.mark.asyncio
async def test_pass_after_regular_tool_sets_both_wait_and_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「普通工具 + action + pass」同轮混用：should_wait 与 pending 应同时成立。

    这正是 bug 现场第一轮的形状：``tool-change_outfit + action-send_text +
    action-pass_and_wait(seconds:0)``。session.py 4.5 依赖该结果做
    「等待优先于 FOLLOW_UP」的决策。
    """
    _install_fake_publish(monkeypatch)
    response = _FakeResponse()
    calls = [
        _make_call("tool-change_outfit", {"outfit": "A"}, call_id="t1"),
        _make_call("action-send_text", {"content": "你好"}, call_id="a1"),
        _make_call(_PASS, {"seconds": 0}, call_id="p1"),
    ]

    outcome, called_names = await _run(calls, response)

    # 普通工具先执行，再登记等待
    assert called_names == ["tool-change_outfit", "action-send_text"]
    assert outcome.should_wait is True
    assert outcome.wait_seconds == 0.0
    assert outcome.has_pending_tool_results is True
    assert outcome.should_stop is False


@pytest.mark.asyncio
async def test_pass_not_registered_in_cross_round_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨轮重复 pass 不应被去重：两次都应置上 should_wait。

    修复前：第一轮 pass 签名登记进 ``seen_signatures``，第二轮相同 pass
    命中去重被跳过 → should_wait 永远为 False → 无限 FOLLOW_UP 空转。
    """
    published, shared_seen = _install_fake_publish(monkeypatch)

    # 第一轮
    response_1 = _FakeResponse()
    outcome_1, _ = await _run(
        [_make_call(_PASS, {"seconds": 0})], response_1, seen=shared_seen
    )
    assert outcome_1.should_wait is True

    # 第二轮（同一跨轮窗口内，seen_signatures 未被清除）
    response_2 = _FakeResponse()
    outcome_2, _ = await _run(
        [_make_call(_PASS, {"seconds": 0})], response_2, seen=shared_seen
    )
    assert outcome_2.should_wait is True

    # 控制流调用从不触达 :dedupe_tool_call，跨轮集合保持为空
    assert published == []
    assert shared_seen == set()


@pytest.mark.asyncio
async def test_stop_not_registered_in_cross_round_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop_conversation 同样不参与跨轮去重，重复 stop 每次都被执行。"""
    published, shared_seen = _install_fake_publish(monkeypatch)

    for _ in range(2):
        response = _FakeResponse()
        outcome, _ = await _run(
            [_make_call(_STOP, {"minutes": 3})], response, seen=shared_seen
        )
        assert outcome.should_stop is True
        assert outcome.stop_minutes == 3.0

    assert published == []
    assert shared_seen == set()


@pytest.mark.asyncio
async def test_repeated_pass_in_same_output_runs_each_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一轮内重复 pass 不应被本轮去重跳过，也不写入「重复已跳过」结果。"""
    _install_fake_publish(monkeypatch)
    response = _FakeResponse()

    outcome, _ = await _run(
        [
            _make_call(_PASS, {"seconds": 0}, call_id="p1"),
            _make_call(_PASS, {"seconds": 0}, call_id="p2"),
        ],
        response,
    )

    assert outcome.should_wait is True
    # 两条 pass 各自写回了登记等待的结果文本（而非去重跳过文本）
    assert len(response.payloads) == 2
    for payload in response.payloads:
        text = payload.content[0].value
        assert text.startswith("[pass]")
        assert "duplicate" not in text


@pytest.mark.asyncio
async def test_regular_tool_cross_round_dedupe_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通工具的跨轮去重语义保持不变：同签名调用第二轮回合并被跳过。"""
    published, shared_seen = _install_fake_publish(monkeypatch)
    tool_call = _make_call("tool-weather", {"city": "上海"}, call_id="w1")

    # 第一轮：执行
    response_1 = _FakeResponse()
    outcome_1, called_1 = await _run([tool_call], response_1, seen=shared_seen)
    assert called_1 == ["tool-weather"]
    assert outcome_1.has_pending_tool_results is True

    # 第二轮：同签名命中跨轮去重，不再执行，只写回「重复已跳过」结果
    response_2 = _FakeResponse()
    outcome_2, called_2 = await _run([tool_call], response_2, seen=shared_seen)
    assert called_2 == []
    assert outcome_2.has_pending_tool_results is False
    assert len(response_2.payloads) == 1
    assert response_2.payloads[0].content[0].value == "[duplicate] tool-weather"

    # 普通工具仍然触达跨轮去重事件
    assert published == [str(NdfcEvent.DEDUPE_TOOL_CALL)] * 2