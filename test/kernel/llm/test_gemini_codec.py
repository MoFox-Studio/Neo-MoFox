"""Gemini 官方 SDK 编解码层（gemini_codec）单元测试。

测试覆盖：
1. ``normalize_endpoint`` / ``target_identity``：端点归一化与非法版本拒绝；
2. ``decode_response``：SDK 结果的终态映射（STOP / MAX_TOKENS / SAFETY 等）、
   usage（含 thought / cache 计数）、工具调用的原生 id 与内部 id；
3. ``encode_payloads``：system / user / tool schema、工具结果回传、
   不支持的内容类型拒绝；
4. 签名原样 roundtrip：``thought_signature`` 字节精确还原，
   且内容被篡改或来源目标不匹配时拒绝回放。

本文件只做纯函数级验证，不发起任何真实网络请求，也不依赖真实凭据。
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from google.genai import types

from src.kernel.llm.exceptions import (
    LLMAPIError,
    LLMConfigurationError,
    LLMContentFilterError,
    LLMContextError,
)
from src.kernel.llm.model_client.gemini_codec import (
    decode_response,
    encode_payloads,
    extract_usage,
    normalize_endpoint,
    target_identity,
)
from src.kernel.llm.payload import (
    LLMPayload,
    Image,
    ReasoningText,
    Text,
    ToolCall,
    ToolResult,
)
from src.kernel.llm.roles import ROLE

# ============================================================================
# Helpers
# ============================================================================

TARGET: tuple[str, str, str] = (
    "gemini",
    "https://generativelanguage.googleapis.com/v1beta",
    "gemini-2.5-pro",
)

# 1x1 透明 PNG，用于图片 MIME 校验。
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


def build_response(
    parts: list[types.Part],
    *,
    finish_reason: types.FinishReason | None = types.FinishReason.STOP,
    usage: types.GenerateContentResponseUsageMetadata | None = None,
    role: str = "model",
) -> types.GenerateContentResponse:
    """构造一个 SDK 响应对象。

    Args:
        parts: 候选内容包含的 Part 列表。
        finish_reason: 候选的结束原因；``None`` 表示不设置。
        usage: 可选的 usage 元数据。
        role: 候选内容的角色。

    Returns:
        构造好的 ``types.GenerateContentResponse``。
    """
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                index=0,
                content=types.Content(role=role, parts=parts),
                finish_reason=finish_reason,
            )
        ],
        usage_metadata=usage,
    )


class WeatherTool:
    """最小的 MoFox 工具声明，用于 schema 转换测试。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """返回 OpenAI 风格的嵌套 schema。"""
        return {
            "function": {
                "name": "get_weather",
                "description": "查询天气",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        }


class FlatTool:
    """返回扁平 schema 的工具声明。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """返回不带 ``function`` 包装的 schema。"""
        return {
            "name": "flat_tool",
            "description": "扁平 schema",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "number"}},
            },
        }


class ConflictingTool:
    """与 :class:`WeatherTool` 同名但描述不同的工具声明。"""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """返回冲突的同名 schema。"""
        return {
            "function": {
                "name": "get_weather",
                "description": "另一个描述",
                "parameters": {"type": "object", "properties": {}},
            }
        }


# ============================================================================
# normalize_endpoint / target_identity
# ============================================================================


class TestNormalizeEndpoint:
    """``normalize_endpoint`` 的端点归一化与拒绝规则。"""

    def test_default_endpoint(self) -> None:
        """未配置 base_url 时使用官方服务根与 v1beta。"""
        root, version = normalize_endpoint(None)
        assert root == "https://generativelanguage.googleapis.com"
        assert version == "v1beta"

    @pytest.mark.parametrize("version", ["v1beta", "v1alpha", "v1"])
    def test_version_suffix_is_stripped(self, version: str) -> None:
        """base_url 末尾的版本段被剥离并回填版本号。"""
        root, resolved = normalize_endpoint(f"https://gateway.test/{version}")
        assert root == "https://gateway.test"
        assert resolved == version

    def test_trailing_slash_is_ignored(self) -> None:
        """末尾斜杠不应产生空路径段。"""
        root, version = normalize_endpoint("https://gateway.test/")
        assert root == "https://gateway.test"
        assert version == "v1beta"

    def test_matching_version_suffix_is_accepted(self) -> None:
        """base_url 版本与请求版本一致时不应冲突。"""
        root, version = normalize_endpoint("https://gateway.test/v1beta", "v1beta")
        assert root == "https://gateway.test"
        assert version == "v1beta"

    def test_conflicting_version_is_rejected(self) -> None:
        """base_url 版本与请求版本冲突时必须报错。"""
        with pytest.raises(LLMConfigurationError):
            normalize_endpoint("https://gateway.test/v1beta", "v1")

    def test_unsupported_version_is_rejected(self) -> None:
        """未知版本号必须报错。"""
        with pytest.raises(LLMConfigurationError):
            normalize_endpoint("https://gateway.test", "v9")

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://gateway.test",
            "https://user:pass@gateway.test",
            "https://gateway.test?key=1",
            "https://gateway.test#frag",
            "ftp://gateway.test",
        ],
    )
    def test_invalid_base_url_is_rejected(self, base_url: str) -> None:
        """非 HTTPS、含凭据、含 query/fragment 的 base_url 必须报错。"""
        with pytest.raises(LLMConfigurationError):
            normalize_endpoint(base_url)


class TestTargetIdentity:
    """``target_identity`` 的签名来源标识。"""

    def test_identity_uses_provider_endpoint_and_model(self) -> None:
        """标识由 provider、endpoint 与模型名组成。"""
        identity = target_identity(
            {
                "api_provider": "gemini",
                "base_url": "https://gateway.test/v1beta",
                "model_identifier": "gemini-2.5-pro",
                "extra_params": {},
            }
        )
        assert identity == ("gemini", "https://gateway.test/v1beta", "gemini-2.5-pro")

    def test_models_prefix_is_removed(self) -> None:
        """``models/`` 前缀不进入模型标识。"""
        identity = target_identity(
            {
                "api_provider": "gemini",
                "base_url": "https://gateway.test/v1beta",
                "model_identifier": "models/gemini-2.5-pro",
                "extra_params": {},
            }
        )
        assert identity[2] == "gemini-2.5-pro"

    def test_api_version_option_is_applied(self) -> None:
        """``extra_params.gemini.api_version`` 决定版本段。"""
        identity = target_identity(
            {
                "api_provider": "gemini",
                "base_url": None,
                "model_identifier": "gemini-2.5-pro",
                "extra_params": {"gemini": {"api_version": "v1"}},
            }
        )
        assert identity[1] == "https://generativelanguage.googleapis.com/v1"


# ============================================================================
# decode_response：终态映射
# ============================================================================


class TestDecodeResponseFinishReasons:
    """``decode_response`` 对 SDK 终态的映射。"""

    def test_stop_is_accepted(self) -> None:
        """STOP 终态返回消息内容。"""
        result = decode_response(
            build_response([types.Part(text="hello")]), TARGET
        )
        assert result.message == "hello"
        assert result.stop_reason == "STOP"
        assert result.tool_calls is None

    def test_max_tokens_is_accepted(self) -> None:
        """MAX_TOKENS 表示被截断但仍可返回部分内容。"""
        result = decode_response(
            build_response(
                [types.Part(text="partial")],
                finish_reason=types.FinishReason.MAX_TOKENS,
            ),
            TARGET,
        )
        assert result.message == "partial"
        assert result.stop_reason == "MAX_TOKENS"

    def test_text_parts_are_joined(self) -> None:
        """多个文本 Part 拼接为一条消息。"""
        result = decode_response(
            build_response([types.Part(text="a"), types.Part(text="b")]), TARGET
        )
        assert result.message == "ab"

    def test_thought_part_is_separated(self) -> None:
        """思考内容进入 reasoning，不混入正文消息。"""
        result = decode_response(
            build_response(
                [
                    types.Part(text="thinking", thought=True),
                    types.Part(text="answer"),
                ]
            ),
            TARGET,
        )
        assert result.message == "answer"
        assert result.reasoning_content is not None
        assert [part.text for part in result.reasoning_content] == ["thinking"]

    def test_no_candidates_is_rejected(self) -> None:
        """没有候选结果属于协议错误。"""
        with pytest.raises(LLMAPIError):
            decode_response(types.GenerateContentResponse(candidates=[]), TARGET)

    @pytest.mark.parametrize(
        "finish_reason",
        [
            types.FinishReason.SAFETY,
            types.FinishReason.BLOCKLIST,
            types.FinishReason.PROHIBITED_CONTENT,
            types.FinishReason.SPII,
            types.FinishReason.IMAGE_SAFETY,
            types.FinishReason.RECITATION,
        ],
    )
    def test_blocked_finish_reason_raises_content_filter(
        self, finish_reason: types.FinishReason
    ) -> None:
        """被安全策略拦截的终态必须转成内容过滤错误。"""
        with pytest.raises(LLMContentFilterError) as excinfo:
            decode_response(
                build_response([types.Part(text="x")], finish_reason=finish_reason),
                TARGET,
            )
        assert excinfo.value.filter_type == finish_reason.value

    @pytest.mark.parametrize(
        "finish_reason",
        [
            types.FinishReason.LANGUAGE,
            types.FinishReason.MALFORMED_FUNCTION_CALL,
            types.FinishReason.FINISH_REASON_UNSPECIFIED,
        ],
    )
    def test_unsupported_finish_reason_is_rejected(
        self, finish_reason: types.FinishReason
    ) -> None:
        """非白名单终态属于协议错误，不得当作成功返回。"""
        with pytest.raises(LLMAPIError):
            decode_response(
                build_response([types.Part(text="x")], finish_reason=finish_reason),
                TARGET,
            )

    def test_missing_finish_reason_is_rejected_by_default(self) -> None:
        """默认要求终态存在，缺失时必须报错。"""
        with pytest.raises(LLMAPIError):
            decode_response(
                build_response([types.Part(text="x")], finish_reason=None), TARGET
            )

    def test_missing_finish_reason_allowed_when_not_required(self) -> None:
        """``require_finish=False`` 时允许流式中间态。"""
        result = decode_response(
            build_response([types.Part(text="x")], finish_reason=None),
            TARGET,
            require_finish=False,
        )
        assert result.message == "x"
        assert result.stop_reason is None

    def test_prompt_feedback_block_raises_content_filter(self) -> None:
        """prompt 被拦截时必须转成内容过滤错误。"""
        response = types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(
                block_reason=types.BlockedReason.SAFETY
            ),
            candidates=[],
        )
        with pytest.raises(LLMContentFilterError):
            decode_response(response, TARGET)


# ============================================================================
# decode_response：usage
# ============================================================================


class TestExtractUsage:
    """``extract_usage`` 的 usage 映射与 thought/cache 计数。"""

    def test_none_usage_returns_empty_mapping(self) -> None:
        """没有 usage 元数据时返回空字典。"""
        assert extract_usage(None) == {}

    def test_basic_counters_are_mapped(self) -> None:
        """基础计数映射到统一 usage 字段。"""
        usage = extract_usage(
            types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100,
                candidates_token_count=20,
                total_token_count=120,
            )
        )
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 20
        assert usage["total_tokens"] == 120

    def test_thought_tokens_are_not_double_charged(self) -> None:
        """思考 token 单独记账，且不重复计入 completion。"""
        usage = extract_usage(
            types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100,
                candidates_token_count=20,
                thoughts_token_count=30,
                total_token_count=150,
            )
        )
        assert usage["reasoning_tokens"] == 30
        assert usage["completion_includes_reasoning"] is False
        assert usage["completion_tokens"] == 20

    def test_cache_hit_and_miss_are_split(self) -> None:
        """缓存命中与未命中计数按 prompt 拆分。"""
        usage = extract_usage(
            types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100,
                candidates_token_count=20,
                cached_content_token_count=40,
                total_token_count=120,
            )
        )
        assert usage["cache_hit_tokens"] == 40
        assert usage["cache_miss_tokens"] == 60
        assert usage["cache_write_tokens"] == 0

    def test_cache_miss_never_goes_negative(self) -> None:
        """缓存命中超过 prompt 时，未命中数不得为负。"""
        usage = extract_usage(
            types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10,
                candidates_token_count=5,
                cached_content_token_count=20,
                total_token_count=15,
            )
        )
        assert usage["cache_miss_tokens"] == 0

    @pytest.mark.parametrize("bad_value", [True, "10", -1, 1.5])
    def test_invalid_counter_is_rejected(self, bad_value: Any) -> None:
        """非整数、负数或布尔计数必须报协议错误。"""
        usage = types.GenerateContentResponseUsageMetadata.model_construct(
            prompt_token_count=bad_value
        )
        with pytest.raises(LLMAPIError):
            extract_usage(usage)


# ============================================================================
# decode_response：工具调用 id
# ============================================================================


class TestDecodeResponseToolCalls:
    """``decode_response`` 的工具调用 id 处理。"""

    def test_native_id_is_preserved(self) -> None:
        """SDK 返回原生 id 时，内部 id 与原生 id 一致。"""
        result = decode_response(
            build_response(
                [
                    types.Part(
                        function_call=types.FunctionCall(
                            id="native-1", name="t", args={"a": 1}
                        )
                    )
                ]
            ),
            TARGET,
        )
        assert result.tool_calls == [
            {"id": "native-1", "name": "t", "args": {"a": 1}}
        ]
        assert result.provider_state.call_ids == (("native-1", "native-1"),)

    def test_missing_native_id_gets_internal_id(self) -> None:
        """SDK 未返回 id 时生成稳定的内部 id。"""
        result = decode_response(
            build_response(
                [
                    types.Part(function_call=types.FunctionCall(name="t", args={"a": 1}))
                ]
            ),
            TARGET,
        )
        assert result.tool_calls is not None
        assert result.tool_calls[0]["id"] == "gemini_call_0"
        assert result.provider_state.call_ids == (("gemini_call_0", None),)

    def test_multiple_calls_get_distinct_internal_ids(self) -> None:
        """多个无 id 的调用必须得到互不相同的内部 id。"""
        result = decode_response(
            build_response(
                [
                    types.Part(function_call=types.FunctionCall(name="t1", args={"a": 1})),
                    types.Part(function_call=types.FunctionCall(name="t2", args={"b": 2})),
                ]
            ),
            TARGET,
        )
        assert result.tool_calls is not None
        ids = [call["id"] for call in result.tool_calls]
        assert ids == ["gemini_call_0", "gemini_call_1"]
        assert len(set(ids)) == 2

    def test_function_call_without_name_is_rejected(self) -> None:
        """缺少函数名的调用属于协议错误。"""
        response = build_response(
            [types.Part(function_call=types.FunctionCall(args={"a": 1}))]
        )
        with pytest.raises(LLMAPIError):
            decode_response(response, TARGET)

    def test_function_call_without_args_is_rejected(self) -> None:
        """参数不是对象的调用属于协议错误。"""
        response = build_response(
            [
                types.Part(
                    function_call=types.FunctionCall.model_construct(
                        name="t", args=None
                    )
                )
            ]
        )
        with pytest.raises(LLMAPIError):
            decode_response(response, TARGET)

    def test_partial_args_are_rejected(self) -> None:
        """增量参数片段不得当成完整参数使用。"""
        response = build_response(
            [
                types.Part(
                    function_call=types.FunctionCall(
                        name="t",
                        args={"a": 1},
                        partial_args=[types.PartialArg()],
                    )
                )
            ]
        )
        with pytest.raises(LLMAPIError):
            decode_response(response, TARGET)

    def test_function_call_under_max_tokens_is_rejected(self) -> None:
        """被截断的函数调用参数可能不完整，必须拒绝。"""
        response = build_response(
            [types.Part(function_call=types.FunctionCall(name="t", args={"a": 1}))],
            finish_reason=types.FinishReason.MAX_TOKENS,
        )
        with pytest.raises(LLMAPIError):
            decode_response(response, TARGET)


# ============================================================================
# 签名 roundtrip 与篡改拒绝
# ============================================================================


class TestSignatureRoundTrip:
    """``thought_signature`` 的原样保存与回放。"""

    def test_signature_bytes_are_preserved(self) -> None:
        """签名在快照中按 base64 精确保留。"""
        raw = bytes([0x00, 0xFF, 0x10, 0x80, 0x7F])
        result = decode_response(
            build_response([types.Part(text="x", thought_signature=raw)]), TARGET
        )
        parts = json.loads(result.provider_state.parts_json)
        assert parts[0]["thought_signature"] == base64.b64encode(raw).decode("ascii")

    def test_signature_round_trips_through_replay(self) -> None:
        """回放后签名字节与原始完全一致。"""
        raw = b"\x00\xff\x10\x80\x7f"
        result = decode_response(
            build_response([types.Part(text="x", thought_signature=raw)]), TARGET
        )
        rebuilt = types.Part.model_validate(
            json.loads(result.provider_state.parts_json)[0]
        )
        assert rebuilt.thought_signature == raw

    def test_signature_only_part_is_preserved(self) -> None:
        """只有签名没有文本的 Part 也必须原样保留。"""
        result = decode_response(
            build_response([types.Part(thought_signature=b"only-sig")]), TARGET
        )
        assert result.message is None
        rebuilt = types.Part.model_validate(
            json.loads(result.provider_state.parts_json)[0]
        )
        assert rebuilt.thought_signature == b"only-sig"

    def test_replay_restores_every_part_in_order(self) -> None:
        """多 Part 的顺序与签名在回放后保持不变。"""
        parts = [
            types.Part(text="thinking", thought=True, thought_signature=b"sig1"),
            types.Part(text="hello", thought_signature=b"sig2"),
        ]
        result = decode_response(build_response(parts), TARGET)
        rebuilt = [
            types.Part.model_validate(item)
            for item in json.loads(result.provider_state.parts_json)
        ]
        assert [part.thought_signature for part in rebuilt] == [b"sig1", b"sig2"]
        assert [part.text for part in rebuilt] == ["thinking", "hello"]

    def test_replay_keeps_tool_call_and_signature(self) -> None:
        """函数调用 Part 上的签名同样原样保留。"""
        result = decode_response(
            build_response(
                [
                    types.Part(
                        function_call=types.FunctionCall(
                            id="native-1", name="t", args={"a": 1}
                        ),
                        thought_signature=b"sig3",
                    )
                ]
            ),
            TARGET,
        )
        rebuilt = types.Part.model_validate(
            json.loads(result.provider_state.parts_json)[0]
        )
        assert rebuilt.thought_signature == b"sig3"
        assert rebuilt.function_call.id == "native-1"

    def test_replay_rejects_unsupported_media_generation(self) -> None:
        """首期 Chat adapter 不把媒体输出当作空的成功回答。"""
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01"
        with pytest.raises(LLMAPIError, match="Unsupported Gemini response Part"):
            decode_response(
                build_response([
                    types.Part(inline_data=types.Blob(mime_type="image/png", data=png), thought_signature=b"s")
                ]), TARGET,
            )

    def test_signed_payload_is_replayed_verbatim(self) -> None:
        """带快照的 assistant payload 回放时使用原始 Part，而不是重建。"""
        result = decode_response(
            build_response(
                [
                    types.Part(text="thinking", thought=True, thought_signature=b"sig1"),
                    types.Part(text="hello", thought_signature=b"sig2"),
                ]
            ),
            TARGET,
        )
        content: list[Any] = []
        content.extend(result.reasoning_content or [])
        if result.message:
            content.append(Text(result.message))
        payload = LLMPayload(ROLE.ASSISTANT, content, provider_state=result.provider_state)

        contents, _system, _tools = encode_payloads([payload], TARGET, [])

        assert len(contents) == 1
        assert contents[0].role == "model"
        assert [part.thought_signature for part in contents[0].parts] == [
            b"sig1",
            b"sig2",
        ]

    def test_tampered_text_is_rejected(self) -> None:
        """签名内容被篡改后不得回放。"""
        result = decode_response(
            build_response([types.Part(text="hello", thought_signature=b"sig")]), TARGET
        )
        tampered = LLMPayload(
            ROLE.ASSISTANT, [Text("tampered")], provider_state=result.provider_state
        )
        with pytest.raises(LLMContextError):
            encode_payloads([tampered], TARGET, [])

    def test_tampered_tool_args_are_rejected(self) -> None:
        """工具参数被篡改后不得回放。"""
        result = decode_response(
            build_response(
                [
                    types.Part(
                        function_call=types.FunctionCall(
                            id="native-1", name="t", args={"a": 1}
                        ),
                        thought_signature=b"sig",
                    )
                ]
            ),
            TARGET,
        )
        tampered = LLMPayload(
            ROLE.ASSISTANT,
            [ToolCall(id="native-1", name="t", args={"a": 999})],
            provider_state=result.provider_state,
        )
        with pytest.raises(LLMContextError):
            encode_payloads([tampered], TARGET, [])

    def test_other_target_is_rejected(self) -> None:
        """快照属于其他目标（endpoint/模型不同）时不得回放。"""
        result = decode_response(
            build_response([types.Part(text="hello", thought_signature=b"sig")]), TARGET
        )
        payload = LLMPayload(
            ROLE.ASSISTANT, [Text("hello")], provider_state=result.provider_state
        )
        other_target = ("gemini", "https://other.test/v1beta", "gemini-2.5-pro")
        with pytest.raises(LLMContextError):
            encode_payloads([payload], other_target, [])

    def test_foreign_provider_state_is_ignored(self) -> None:
        """其他 provider 的快照不得当作 Gemini 签名回放。"""
        from src.kernel.llm.provider_state import ProviderReplayState, payload_fingerprint

        foreign = ProviderReplayState(
            provider="openai",
            target=("openai", "https://api.openai.com/v1", "gpt-4"),
            parts_json=json.dumps([{"text": "x"}]),
            content_fingerprint=payload_fingerprint([Text("x")]),
        )
        payload = LLMPayload(
            ROLE.ASSISTANT, [Text("hello")], provider_state=foreign
        )
        contents, _system, _tools = encode_payloads([payload], TARGET, [])
        assert len(contents) == 1
        assert contents[0].parts[0].text == "hello"

    def test_response_payload_round_trips_through_production_path(self) -> None:
        """``LLMResponse`` 产出的 assistant payload 必须能被原样回放。"""
        from src.kernel.llm.response import LLMResponse

        result = decode_response(
            build_response(
                [
                    types.Part(text="thinking", thought=True, thought_signature=b"sig1"),
                    types.Part(text="hello", thought_signature=b"sig2"),
                ]
            ),
            TARGET,
        )

        class _Upper:
            """最小化的请求替身。"""

            request_name = "r"
            meta_data: dict[str, Any] = {}

        response = LLMResponse(
            _stream=None,
            _upper=_Upper(),
            _auto_append_response=False,
            payloads=[],
            model_set=[],
        )
        response.message = result.message
        response.reasoning_content = "".join(
            part.text for part in result.reasoning_content or []
        ) or None
        response.reasoning_parts = result.reasoning_content
        response.provider_state = result.provider_state

        payload = response.to_payload()

        assert payload.provider_state is result.provider_state
        assert result.provider_state.matches(payload.content) is True

        contents, _system, _tools = encode_payloads([payload], TARGET, [])
        assert len(contents) == 1
        assert [part.thought_signature for part in contents[0].parts] == [
            b"sig1",
            b"sig2",
        ]


# ============================================================================
# encode_payloads
# ============================================================================


class TestEncodePayloadsRoles:
    """``encode_payloads`` 的角色映射。"""

    def test_system_text_becomes_system_instruction(self) -> None:
        """SYSTEM 文本进入 system 指令，不进入对话内容。"""
        contents, system, tools = encode_payloads(
            [LLMPayload(ROLE.SYSTEM, Text("你是助手"))], TARGET, []
        )
        assert contents == []
        assert len(system) == 1
        assert system[0].text == "你是助手"
        assert tools == []

    def test_system_requires_text(self) -> None:
        """SYSTEM 指令只接受文本。"""
        image = Image(base64.b64encode(base64.b64decode(PNG_BASE64)).decode())
        with pytest.raises(LLMConfigurationError):
            encode_payloads([LLMPayload(ROLE.SYSTEM, image)], TARGET, [])

    def test_user_text_becomes_user_content(self) -> None:
        """USER 文本映射为 user 角色的 Content。"""
        contents, system, _tools = encode_payloads(
            [LLMPayload(ROLE.USER, Text("hi"))], TARGET, []
        )
        assert system == []
        assert len(contents) == 1
        assert contents[0].role == "user"
        assert contents[0].parts[0].text == "hi"

    def test_assistant_text_becomes_model_content(self) -> None:
        """ASSISTANT 文本映射为 model 角色。"""
        contents, _system, _tools = encode_payloads(
            [LLMPayload(ROLE.ASSISTANT, Text("hello"))], TARGET, []
        )
        assert contents[0].role == "model"
        assert contents[0].parts[0].text == "hello"

    def test_empty_user_payload_is_dropped(self) -> None:
        """没有实际 Part 的 payload 不产生空 Content。"""
        contents, _system, _tools = encode_payloads(
            [LLMPayload(ROLE.USER, [])], TARGET, []
        )
        assert contents == []

    def test_foreign_reasoning_is_dropped(self) -> None:
        """其他 provider 的思考片段不进入 Gemini 请求。"""
        contents, _system, _tools = encode_payloads(
            [LLMPayload(ROLE.ASSISTANT, [ReasoningText("foreign"), Text("kept")])],
            TARGET,
            [],
        )
        assert len(contents) == 1
        assert [part.text for part in contents[0].parts] == ["kept"]

    def test_unsupported_content_is_rejected(self) -> None:
        """未知内容类型必须显式报错，不得静默丢弃。"""

        class Unsupported:
            """既非 Text 也非 ToolCall 的内容。"""

        with pytest.raises(LLMConfigurationError):
            encode_payloads(
                [LLMPayload(ROLE.USER, Unsupported())], TARGET, []
            )


class TestEncodePayloadsImages:
    """图片内容的 MIME 校验。"""

    def test_valid_png_is_accepted(self) -> None:
        """合法 PNG 以 inline_data 形式发送，MIME 由内容推断。"""
        image = Image(base64.b64encode(base64.b64decode(PNG_BASE64)).decode())
        contents, _system, _tools = encode_payloads(
            [LLMPayload(ROLE.USER, image)], TARGET, []
        )
        inline = contents[0].parts[0].inline_data
        assert inline is not None
        assert inline.mime_type == "image/png"

    def test_non_image_payload_is_rejected(self) -> None:
        """无法识别为图片的内容必须报错。"""
        image = Image(base64.b64encode(b"not an image").decode())
        with pytest.raises(LLMConfigurationError):
            encode_payloads([LLMPayload(ROLE.USER, image)], TARGET, [])


class TestEncodePayloadsTools:
    """工具声明与工具结果的编码。"""

    def test_tool_schema_becomes_function_declaration(self) -> None:
        """工具 schema 转换为 SDK 的 FunctionDeclaration。"""
        _contents, _system, tools = encode_payloads([], TARGET, [WeatherTool])
        assert len(tools) == 1
        declaration = tools[0].function_declarations[0]
        assert declaration.name == "get_weather"
        assert declaration.description == "查询天气"
        parameters = declaration.parameters_json_schema
        assert parameters is not None
        assert parameters["properties"]["city"]["type"] == "string"

    def test_flat_tool_schema_is_accepted(self) -> None:
        """不带 ``function`` 包装的 schema 同样支持。"""
        _contents, _system, tools = encode_payloads([], TARGET, [FlatTool])
        declaration = tools[0].function_declarations[0]
        assert declaration.name == "flat_tool"

    def test_tool_role_payload_registers_declaration(self) -> None:
        """ROLE.TOOL payload 只登记声明，不进入对话内容。"""
        contents, _system, tools = encode_payloads(
            [LLMPayload(ROLE.TOOL, WeatherTool)], TARGET, []
        )
        assert contents == []
        assert tools[0].function_declarations[0].name == "get_weather"

    def test_conflicting_declarations_are_rejected(self) -> None:
        """同名但定义不同的工具声明必须报错。"""
        with pytest.raises(LLMConfigurationError):
            encode_payloads([], TARGET, [WeatherTool, ConflictingTool])

    def test_tool_call_is_encoded_as_function_call(self) -> None:
        """assistant 的工具调用映射为 function_call Part。"""
        contents, _system, _tools = encode_payloads(
            [LLMPayload(ROLE.ASSISTANT, ToolCall(id="c1", name="t", args={"a": 1}))],
            TARGET,
            [],
        )
        call = contents[0].parts[0].function_call
        assert call is not None
        assert call.id == "c1"
        assert call.name == "t"
        assert call.args == {"a": 1}

    def test_tool_call_string_args_are_parsed(self) -> None:
        """字符串形式的参数被解析为对象。"""
        contents, _system, _tools = encode_payloads(
            [LLMPayload(ROLE.ASSISTANT, ToolCall(id="c1", name="t", args='{"a": 1}'))],
            TARGET,
            [],
        )
        assert contents[0].parts[0].function_call.args == {"a": 1}

    def test_tool_call_without_id_is_rejected(self) -> None:
        """没有稳定 id 的工具调用必须报错。"""
        with pytest.raises(LLMContextError):
            encode_payloads(
                [LLMPayload(ROLE.ASSISTANT, ToolCall(id=None, name="t", args={"a": 1}))],
                TARGET,
                [],
            )

    def test_tool_result_is_encoded_as_function_response(self) -> None:
        """工具结果映射为 function_response，并带上原生 id。"""
        contents, _system, _tools = encode_payloads(
            [
                LLMPayload(
                    ROLE.ASSISTANT, ToolCall(id="native-1", name="t", args={"a": 1})
                ),
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(value={"ok": 1}, call_id="native-1", name="t"),
                ),
            ],
            TARGET,
            [],
        )
        response = contents[1].parts[0].function_response
        assert response is not None
        assert response.id == "native-1"
        assert response.name == "t"
        assert response.response == {"ok": 1}

    def test_tool_result_string_value_is_wrapped(self) -> None:
        """非字典结果被包装为 ``result`` 字段。"""
        contents, _system, _tools = encode_payloads(
            [
                LLMPayload(
                    ROLE.ASSISTANT, ToolCall(id="c1", name="t", args={"a": 1})
                ),
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(value="plain text", call_id="c1", name="t"),
                ),
            ],
            TARGET,
            [],
        )
        assert contents[1].parts[0].function_response.response == {
            "result": "plain text"
        }

    def test_orphan_tool_result_is_rejected(self) -> None:
        """找不到对应调用的工具结果必须报错。"""
        with pytest.raises(LLMContextError):
            encode_payloads(
                [
                    LLMPayload(
                        ROLE.TOOL_RESULT,
                        ToolResult(value="x", call_id="missing", name="t"),
                    )
                ],
                TARGET,
                [],
            )

    def test_signed_tool_call_keeps_native_id(self) -> None:
        """带快照回放时，工具结果使用快照记录中的原生 id。"""
        result = decode_response(
            build_response(
                [
                    types.Part(
                        function_call=types.FunctionCall(
                            id="native-1", name="t", args={"a": 1}
                        ),
                        thought_signature=b"sig",
                    )
                ]
            ),
            TARGET,
        )
        assistant = LLMPayload(
            ROLE.ASSISTANT,
            [ToolCall(id="native-1", name="t", args={"a": 1})],
            provider_state=result.provider_state,
        )
        contents, _system, _tools = encode_payloads(
            [
                assistant,
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(value={"ok": 1}, call_id="native-1", name="t"),
                ),
            ],
            TARGET,
            [],
        )
        assert contents[1].parts[0].function_response.id == "native-1"
