"""MoFox payload and result conversion for the official Google Gen AI SDK."""
from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from google.genai import types
from PIL import Image as PILImage

from ..exceptions import LLMAPIError, LLMConfigurationError, LLMContentFilterError, LLMContextError
from ..payload import Image, LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from ..provider_state import ProviderReplayState, payload_fingerprint
from ..roles import ROLE
from .base import ModelClientResult
from .shared import inject_reason_parameter, normalize_schema_for_grammar


def normalize_endpoint(base_url: str | None, api_version: str = "v1beta") -> tuple[str, str]:
    """Separate the SDK service root from an optional legacy version suffix."""
    value = base_url or "https://generativelanguage.googleapis.com"
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMConfigurationError("Gemini base_url must be an HTTPS service root without credentials/query")
    path = parsed.path.rstrip("/")
    for version in ("v1beta", "v1alpha", "v1"):
        if path.endswith("/" + version):
            if api_version != "v1beta" and api_version != version:
                raise LLMConfigurationError("Gemini API version conflicts with base_url")
            api_version = version
            path = path[:-(len(version) + 1)]
            break
    if api_version not in {"v1", "v1beta", "v1alpha"}:
        raise LLMConfigurationError("Unsupported Gemini API version")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")), api_version


def gemini_options(model: dict[str, Any]) -> dict[str, Any]:
    """Validate nested configuration before any consumer dereferences it."""
    extra = model.get("extra_params", {})
    if not isinstance(extra, dict):
        raise LLMConfigurationError("Gemini extra_params must be an object")
    options = extra.get("gemini", {})
    if not isinstance(options, dict) or not isinstance(options.get("generation_config", {}), dict):
        raise LLMConfigurationError("Gemini options and generation_config must be objects")
    return options


def target_identity(model: dict[str, Any]) -> tuple[str, str, str]:
    """Identify a signing source without storing credentials."""
    options = gemini_options(model)
    root, version = normalize_endpoint(model.get("base_url"), options.get("api_version", "v1beta"))
    return str(model.get("api_provider", "")), root + "/" + version, str(model["model_identifier"]).removeprefix("models/")


def encode_payloads(payloads: list[LLMPayload], target: tuple[str, str, str], tools: list[Any]) -> tuple[list[types.Content], list[types.Part], list[types.Tool]]:
    """Convert messages explicitly; the SDK never owns MoFox tool execution."""
    contents: list[types.Content] = []
    system: list[types.Part] = []
    declarations: dict[str, types.FunctionDeclaration] = {}
    calls: dict[str, tuple[str, str | None]] = {}

    def add_tool(tool: Any) -> None:
        """Convert a MoFox declaration, not a Python callable tool."""
        schema = tool.to_schema()
        schema = schema.get("function", schema)
        name = schema.get("name")
        if not isinstance(name, str) or not name:
            raise LLMConfigurationError("Gemini tool requires a name")
        parameters = json.loads(json.dumps(schema.get("parameters", {"type": "object", "properties": {}})))
        normalize_schema_for_grammar(parameters)
        parameters = inject_reason_parameter(tool, parameters)
        declaration = types.FunctionDeclaration(name=name, description=schema.get("description", ""), parameters_json_schema=parameters)
        if name in declarations and declarations[name] != declaration:
            raise LLMConfigurationError("Conflicting Gemini tool declarations")
        declarations[name] = declaration

    for tool in tools:
        add_tool(tool)
    for payload in payloads:
        if payload.role == ROLE.TOOL:
            for tool in payload.content:
                add_tool(tool)
            continue
        if payload.role == ROLE.SYSTEM:
            for part in payload.content:
                if not isinstance(part, Text):
                    raise LLMConfigurationError("Gemini system instructions require text")
                system.append(types.Part(text=part.text))
            continue
        state = payload.provider_state
        if state is not None and state.provider == "gemini":
            if state.target != target or not state.matches(payload.content):
                raise LLMContextError("Gemini signed content was changed or belongs to another target")
            parts = [types.Part.model_validate(item) for item in json.loads(state.parts_json)]
            native_ids = dict(state.call_ids)
            for part in payload.content:
                if isinstance(part, ToolCall) and part.id:
                    calls[part.id] = (part.name, native_ids.get(part.id))
            contents.append(types.Content(role="model", parts=parts))
            continue
        parts: list[types.Part] = []
        for part in payload.content:
            if isinstance(part, Text):
                parts.append(types.Part(text=part.text))
            elif isinstance(part, ReasoningText):
                # Foreign provider summaries are not signed Gemini reasoning.
                continue
            elif isinstance(part, ToolCall):
                args = json.loads(part.args) if isinstance(part.args, str) else part.args
                if not isinstance(args, dict) or not part.id:
                    raise LLMContextError("Gemini function call needs object arguments and a stable ID")
                calls[part.id] = (part.name, part.id)
                parts.append(types.Part(function_call=types.FunctionCall(id=part.id, name=part.name, args=args)))
            elif isinstance(part, ToolResult):
                if not part.call_id or part.call_id not in calls:
                    raise LLMContextError("Gemini tool result has no matching function call")
                name, native_id = calls[part.call_id]
                value = part.value if isinstance(part.value, dict) else {"result": part.to_text()}
                parts.append(types.Part(function_response=types.FunctionResponse(name=name, id=native_id, response=value)))
            elif isinstance(part, Image):
                try:
                    data = base64.b64decode(part.value, validate=True)
                    with PILImage.open(BytesIO(data)) as image:
                        mime = PILImage.MIME.get(image.format or "")
                    if mime not in {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}:
                        raise ValueError("unsupported image")
                except Exception as exc:
                    raise LLMConfigurationError("Gemini image MIME could not be validated") from exc
                parts.append(types.Part.from_bytes(data=data, mime_type=mime))
            else:
                raise LLMConfigurationError("Unsupported Gemini payload content: " + type(part).__name__)
        if parts:
            contents.append(types.Content(role="model" if payload.role == ROLE.ASSISTANT else "user", parts=parts))
    declared = [types.Tool(function_declarations=list(declarations.values()))] if declarations else []
    return contents, system, declared


def extract_usage(usage: types.GenerateContentResponseUsageMetadata | None) -> dict[str, Any]:
    """Map cumulative Gemini usage without charging thought tokens twice."""
    if usage is None:
        return {}
    def number(name: str) -> int:
        """Accept only finite nonnegative integer counters."""
        value = getattr(usage, name, None)
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LLMAPIError("Invalid Gemini usage counter", status_code=502)
        return value
    prompt, output, thought, cache = (number(n) for n in ("prompt_token_count", "candidates_token_count", "thoughts_token_count", "cached_content_token_count"))
    return {"prompt_tokens": prompt, "completion_tokens": output, "reasoning_tokens": thought,
            "completion_includes_reasoning": False, "total_tokens": number("total_token_count"),
            "cache_hit_tokens": cache, "cache_miss_tokens": max(0, prompt-cache), "cache_write_tokens": 0}


def decode_response(response: types.GenerateContentResponse, target: tuple[str, str, str], *, require_finish: bool = True) -> ModelClientResult:
    """Parse SDK Parts and keep the exact signed model Content for the next turn."""
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise LLMContentFilterError("Gemini blocked the prompt", filter_type=str(response.prompt_feedback.block_reason))
    if not response.candidates:
        raise LLMAPIError("Gemini returned no candidates", status_code=502)
    candidate = response.candidates[0]
    finish = candidate.finish_reason.value if candidate.finish_reason else None
    if finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "RECITATION"}:
        raise LLMContentFilterError("Gemini blocked the response", filter_type=finish)
    if require_finish and finish not in {"STOP", "MAX_TOKENS"}:
        raise LLMAPIError("Gemini returned an unsupported or missing finish reason", status_code=502)
    parts = list(candidate.content.parts or []) if candidate.content else []
    texts: list[str] = []
    thoughts: list[ReasoningText] = []
    tool_calls: list[dict[str, Any]] = []
    mapping: list[tuple[str, str | None]] = []
    for index, part in enumerate(parts):
        unsupported = set(part.model_dump(exclude_none=True)) - {"text", "thought", "thought_signature", "function_call"}
        if unsupported:
            raise LLMAPIError("Unsupported Gemini response Part", status_code=422)
        if part.text:
            if part.thought:
                thoughts.append(ReasoningText(part.text))
            else:
                texts.append(part.text)
        if part.function_call:
            call = part.function_call
            if call.partial_args or call.will_continue or finish == "MAX_TOKENS":
                raise LLMAPIError("Incomplete Gemini function arguments", status_code=502)
            if not call.name or not isinstance(call.args, dict):
                raise LLMAPIError("Invalid Gemini function call", status_code=502)
            internal_id = call.id or f"gemini_call_{index}"
            tool_calls.append({"id": internal_id, "name": call.name, "args": call.args})
            mapping.append((internal_id, call.id))
    if require_finish and not texts and not thoughts and not tool_calls and not any(p.thought_signature for p in parts):
        raise LLMAPIError("Gemini returned empty completed content", status_code=422)
    standard: list[Any] = list(thoughts)
    message = "".join(texts) or None
    if message:
        standard.append(Text(message))
    standard.extend(ToolCall(id=c["id"], name=c["name"], args=c["args"]) for c in tool_calls)
    replay = ProviderReplayState(provider="gemini", target=target,
        parts_json=json.dumps([p.model_dump(mode="json", exclude_none=True) for p in parts]),
        content_fingerprint=payload_fingerprint(standard), call_ids=tuple(mapping))
    return ModelClientResult(message=message, tool_calls=tool_calls or None, reasoning_content=thoughts or None,
        usage=extract_usage(response.usage_metadata), stop_reason=finish, provider_state=replay)
