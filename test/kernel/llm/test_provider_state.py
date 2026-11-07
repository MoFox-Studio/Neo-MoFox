"""Tests for provider-owned replay snapshots and their payload plumbing.

覆盖范围：
- ``src/kernel/llm/provider_state.py``：``ProviderReplayState`` 的脱敏 repr、
  非法 JSON/版本/target 拒绝、内容指纹稳定性与篡改检测；
- ``src/kernel/llm/payload/payload.py``：``LLMPayload`` 的旧两参数兼容与
  keyword-only ``provider_state``；
- ``src/kernel/llm/context_structure.py``：``append_payload`` 在存在
  ``provider_state`` 时不合并、无 ``provider_state`` 时保持旧的合并语义；
- ``src/kernel/llm/model_client/base.py``：``ModelClientResult`` 与
  ``StreamEvent`` 上的 ``provider_state`` 字段。
"""

from __future__ import annotations

import json

import pytest

from src.kernel.llm.context_structure import append_payload
from src.kernel.llm.model_client.base import ModelClientResult, StreamEvent
from src.kernel.llm.payload import LLMPayload, ReasoningText, Text, ToolCall
from src.kernel.llm.payload.payload import LLMPayload as PayloadFromModule
from src.kernel.llm.provider_state import ProviderReplayState, payload_fingerprint
from src.kernel.llm.roles import ROLE

# ============================================================================
# Helpers
# ============================================================================

PARTS_JSON = json.dumps([{"text": "hello", "thought": False}])
TARGET = ("gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-pro")


def make_state(
    content: list[object] | None = None,
    *,
    parts_json: str = PARTS_JSON,
    schema_version: int = 1,
    target: tuple[str, str, str] = TARGET,
) -> ProviderReplayState:
    """构造一个用于测试的 ``ProviderReplayState``。

    Args:
        content: 用于计算 ``content_fingerprint`` 的标准内容视图。
        parts_json: 供应商 part 的 JSON 快照。
        schema_version: 快照结构版本号。
        target: ``(api_provider, endpoint, model_identifier)`` 三元组。

    Returns:
        构造好的 ``ProviderReplayState`` 实例。
    """
    parts = content if content is not None else [Text(text="hello")]
    return ProviderReplayState(
        provider="gemini",
        target=target,
        parts_json=parts_json,
        content_fingerprint=payload_fingerprint(parts),
        schema_version=schema_version,
    )


# ============================================================================
# ProviderReplayState
# ============================================================================


class TestProviderReplayStateRepr:
    """``ProviderReplayState`` 的脱敏与不可变性。"""

    def test_repr_hides_parts_json(self) -> None:
        """repr 不得打印 parts 内容。"""
        state = make_state()
        rendered = repr(state)
        assert "parts_json" not in rendered
        assert "hello" not in rendered
        assert PARTS_JSON not in rendered

    def test_repr_keeps_safe_metadata(self) -> None:
        """repr 仍应暴露定位快照所需的安全元数据。"""
        state = make_state()
        rendered = repr(state)
        assert "gemini" in rendered
        assert state.content_fingerprint in rendered

    def test_parts_json_still_accessible(self) -> None:
        """脱敏只影响展示，不影响字段读取。"""
        state = make_state()
        assert state.parts_json == PARTS_JSON
        assert json.loads(state.parts_json) == [{"text": "hello", "thought": False}]

    def test_frozen_prevents_mutation(self) -> None:
        """快照为 frozen dataclass，不允许被就地改写。"""
        state = make_state()
        with pytest.raises(Exception):
            state.parts_json = "[]"  # type: ignore[misc]

    def test_call_ids_default_empty(self) -> None:
        """``call_ids`` 默认为空元组。"""
        state = make_state()
        assert state.call_ids == ()

    def test_call_ids_are_preserved(self) -> None:
        """``call_ids`` 按“内部 id -> 供应商 id”顺序保留。"""
        state = ProviderReplayState(
            provider="gemini",
            target=TARGET,
            parts_json=PARTS_JSON,
            content_fingerprint=payload_fingerprint([Text(text="hello")]),
            call_ids=(("call_1", "native_1"),),
        )
        assert state.call_ids == (("call_1", "native_1"),)


class TestProviderReplayStateValidation:
    """``ProviderReplayState`` 的非法输入拒绝行为。"""

    def test_invalid_json_rejected(self) -> None:
        """非法 JSON 的 parts 快照必须被拒绝。"""
        with pytest.raises(ValueError):
            make_state(parts_json="{not-json")

    def test_non_list_parts_rejected(self) -> None:
        """parts 快照必须是 JSON 数组。"""
        with pytest.raises(ValueError):
            make_state(parts_json='{"text": "hello"}')

    def test_unsupported_schema_version_rejected(self) -> None:
        """未知结构版本必须被拒绝。"""
        with pytest.raises(ValueError):
            make_state(schema_version=2)

    def test_invalid_target_length_rejected(self) -> None:
        """target 必须是三元组。"""
        with pytest.raises(ValueError):
            make_state(target=("gemini", "endpoint"))

    def test_error_message_does_not_leak_parts(self) -> None:
        """校验失败信息不得回显 parts 内容。"""
        secret = "sk-secret-parts-value"
        with pytest.raises(ValueError) as excinfo:
            make_state(parts_json=secret)
        assert secret not in str(excinfo.value)


class TestPayloadFingerprint:
    """``payload_fingerprint`` 的稳定性与覆盖范围。"""

    def test_stable_across_calls(self) -> None:
        """相同内容必须得到相同指纹。"""
        content = [Text(text="hello"), ToolCall(id="c1", name="tool", args={"a": 1})]
        assert payload_fingerprint(content) == payload_fingerprint(content)

    def test_stable_across_equal_content(self) -> None:
        """内容相等的不同对象必须得到相同指纹。"""
        first = [Text(text="hello")]
        second = [Text(text="hello")]
        assert payload_fingerprint(first) == payload_fingerprint(second)

    def test_detects_text_change(self) -> None:
        """文本变化必须改变指纹。"""
        before = payload_fingerprint([Text(text="hello")])
        after = payload_fingerprint([Text(text="hello!")])
        assert before != after

    def test_detects_tool_call_change(self) -> None:
        """工具调用变化必须改变指纹。"""
        before = payload_fingerprint([ToolCall(id="c1", name="tool", args={"a": 1})])
        after = payload_fingerprint([ToolCall(id="c1", name="tool", args={"a": 2})])
        assert before != after

    def test_signature_not_part_of_fingerprint(self) -> None:
        """推理签名本身不进入标准指纹。"""
        first = payload_fingerprint([ReasoningText(text="think", signature="sig-a")])
        second = payload_fingerprint([ReasoningText(text="think", signature="sig-b")])
        assert first == second

    def test_order_matters(self) -> None:
        """内容顺序变化必须改变指纹。"""
        first = payload_fingerprint([Text(text="a"), Text(text="b")])
        second = payload_fingerprint([Text(text="b"), Text(text="a")])
        assert first != second

    def test_falls_back_to_value_for_other_parts(self) -> None:
        """既非工具调用也非文本的 part 使用 value 参与指纹。"""
        from src.kernel.llm.payload import ToolResult

        first = payload_fingerprint([ToolResult(value={"a": 1}, call_id="c1")])
        second = payload_fingerprint([ToolResult(value={"a": 2}, call_id="c1")])
        assert first != second

    def test_empty_content_has_stable_fingerprint(self) -> None:
        """空内容也得到稳定的指纹。"""
        assert payload_fingerprint([]) == payload_fingerprint([])


class TestProviderReplayStateMatches:
    """``ProviderReplayState.matches`` 的内容校验。"""

    def test_matches_unchanged_content(self) -> None:
        """未改动的标准内容应当匹配。"""
        content = [Text(text="hello")]
        state = make_state(content)
        assert state.matches(content) is True

    def test_detects_tampered_text(self) -> None:
        """文本被篡改后不得匹配。"""
        state = make_state([Text(text="hello")])
        assert state.matches([Text(text="goodbye")]) is False

    def test_detects_tampered_tool_call(self) -> None:
        """工具调用被篡改后不得匹配。"""
        state = make_state([ToolCall(id="c1", name="tool", args={"a": 1})])
        tampered = [ToolCall(id="c1", name="tool", args={"a": 999})]
        assert state.matches(tampered) is False

    def test_detects_extra_content(self) -> None:
        """追加内容后不得匹配。"""
        state = make_state([Text(text="hello")])
        assert state.matches([Text(text="hello"), Text(text="extra")]) is False


# ============================================================================
# LLMPayload
# ============================================================================


class TestLLMPayloadProviderState:
    """``LLMPayload`` 与 ``provider_state`` 的兼容性。"""

    def test_legacy_two_positional_args(self) -> None:
        """旧写法（两个位置参数）必须继续可用。"""
        payload = LLMPayload(ROLE.USER, Text(text="hello"))
        assert payload.role == ROLE.USER
        assert payload.content == [Text(text="hello")]
        assert payload.provider_state is None

    def test_legacy_keyword_args(self) -> None:
        """旧写法（关键字参数）必须继续可用。"""
        payload = LLMPayload(role=ROLE.ASSISTANT, content=[Text(text="hello")])
        assert payload.role == ROLE.ASSISTANT
        assert payload.content == [Text(text="hello")]
        assert payload.provider_state is None

    def test_single_content_still_normalized(self) -> None:
        """单个 content 仍规范化为列表。"""
        payload = LLMPayload(ROLE.USER, Text(text="hello"))
        assert isinstance(payload.content, list)
        assert len(payload.content) == 1

    def test_provider_state_is_keyword_only(self) -> None:
        """``provider_state`` 只允许以关键字传入。"""
        state = make_state()
        with pytest.raises(TypeError):
            LLMPayload(ROLE.USER, Text(text="hello"), state)  # type: ignore[misc]

    def test_provider_state_accepted_by_keyword(self) -> None:
        """``provider_state`` 可通过关键字传入并被保留。"""
        state = make_state()
        payload = LLMPayload(ROLE.ASSISTANT, Text(text="hello"), provider_state=state)
        assert payload.provider_state is state

    def test_repr_hides_provider_state(self) -> None:
        """``LLMPayload`` 的 repr 不应打印供应商快照内容。"""
        state = make_state()
        payload = LLMPayload(ROLE.ASSISTANT, Text(text="hello"), provider_state=state)
        rendered = repr(payload)
        assert "provider_state" not in rendered
        assert PARTS_JSON not in rendered

    def test_class_exported_from_same_module(self) -> None:
        """``payload`` 包与子模块导出同一个类。"""
        assert PayloadFromModule is LLMPayload


# ============================================================================
# append_payload
# ============================================================================


class TestAppendPayloadProviderState:
    """``append_payload`` 的合并语义。"""

    def test_legacy_merge_without_provider_state(self) -> None:
        """无 provider_state 时保持旧的同 role 合并行为。"""
        payloads = append_payload(
            [],
            LLMPayload(ROLE.USER, Text(text="hello")),
        )
        payloads = append_payload(
            payloads,
            LLMPayload(ROLE.USER, Text(text="world")),
        )
        assert len(payloads) == 1
        assert payloads[0].content == [Text(text="hello"), Text(text="world")]

    def test_no_merge_when_last_has_provider_state(self) -> None:
        """上一条带 provider_state 时不得合并内容。"""
        state = make_state()
        payloads = append_payload(
            [],
            LLMPayload(ROLE.USER, Text(text="hello"), provider_state=state),
        )
        payloads = append_payload(
            payloads,
            LLMPayload(ROLE.USER, Text(text="world")),
        )
        assert len(payloads) == 2
        assert payloads[0].content == [Text(text="hello")]
        assert payloads[1].content == [Text(text="world")]

    def test_no_merge_when_new_has_provider_state(self) -> None:
        """新条目带 provider_state 时不得合并内容。"""
        state = make_state()
        payloads = append_payload(
            [],
            LLMPayload(ROLE.USER, Text(text="hello")),
        )
        payloads = append_payload(
            payloads,
            LLMPayload(ROLE.USER, Text(text="world"), provider_state=state),
        )
        assert len(payloads) == 2
        assert payloads[0].content == [Text(text="hello")]
        assert payloads[1].content == [Text(text="world")]
        assert payloads[1].provider_state is state

    def test_no_merge_when_both_have_provider_state(self) -> None:
        """两条都带 provider_state 时保持独立条目。"""
        state = make_state()
        payloads = append_payload(
            [],
            LLMPayload(ROLE.ASSISTANT, Text(text="a"), provider_state=state),
        )
        payloads = append_payload(
            payloads,
            LLMPayload(ROLE.ASSISTANT, Text(text="b"), provider_state=state),
        )
        assert len(payloads) == 2

    def test_different_role_still_appends(self) -> None:
        """不同 role 依旧直接追加。"""
        payloads = append_payload(
            [],
            LLMPayload(ROLE.USER, Text(text="hello")),
        )
        payloads = append_payload(
            payloads,
            LLMPayload(ROLE.ASSISTANT, Text(text="hi")),
        )
        assert len(payloads) == 2
        assert payloads[1].role == ROLE.ASSISTANT

    def test_explicit_position_bypasses_merge(self) -> None:
        """指定 position 时直接插入，不受合并规则影响。"""
        payloads = append_payload(
            [],
            LLMPayload(ROLE.USER, Text(text="hello")),
        )
        payloads = append_payload(
            payloads,
            LLMPayload(ROLE.USER, Text(text="world"), provider_state=make_state()),
            position=0,
        )
        assert len(payloads) == 2
        assert payloads[0].content == [Text(text="world")]


# ============================================================================
# Model client base
# ============================================================================


class TestModelClientProviderState:
    """``ModelClientResult`` 与 ``StreamEvent`` 上的 ``provider_state``。"""

    def test_model_client_result_defaults(self) -> None:
        """``ModelClientResult`` 各字段默认为 None。"""
        result = ModelClientResult()
        assert result.message is None
        assert result.tool_calls is None
        assert result.stream_iter is None
        assert result.reasoning_content is None
        assert result.usage is None
        assert result.stop_reason is None
        assert result.provider_state is None

    def test_model_client_result_carries_provider_state(self) -> None:
        """``ModelClientResult`` 可以携带并保留 provider_state。"""
        state = make_state()
        result = ModelClientResult(message="hello", provider_state=state)
        assert result.message == "hello"
        assert result.provider_state is state
        assert PARTS_JSON not in repr(result)

    def test_stream_event_defaults(self) -> None:
        """``StreamEvent`` 保持原有字段默认值。"""
        event = StreamEvent()
        assert event.text_delta is None
        assert event.tool_call_id is None
        assert event.usage is None
        assert event.provider_state is None

    def test_stream_event_carries_provider_state(self) -> None:
        """``StreamEvent`` 可以携带 provider_state 且 repr 不打印它。"""
        state = make_state()
        event = StreamEvent(text_delta="hi", provider_state=state)
        assert event.text_delta == "hi"
        assert event.provider_state is state
        assert "provider_state" not in repr(event)
        assert PARTS_JSON not in repr(event)
