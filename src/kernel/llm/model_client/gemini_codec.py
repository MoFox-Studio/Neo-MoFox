"""转换 MoFox 与官方 Gemini SDK 的消息，并保留签名回放所需的原始 Part。"""
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
    """将 base_url 的旧版本后缀与 SDK 服务根分离，并校验 api_version。"""
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
    """在使用 model 的嵌套配置前验证 extra_params 与 Gemini 参数形状。"""
    extra = model.get("extra_params", {})
    if not isinstance(extra, dict):
        raise LLMConfigurationError("Gemini extra_params must be an object")
    options = extra.get("gemini", {})
    if not isinstance(options, dict) or not isinstance(options.get("generation_config", {}), dict):
        raise LLMConfigurationError("Gemini options and generation_config must be objects")
    return options


def target_identity(model: dict[str, Any]) -> tuple[str, str, str]:
    """从 model 提取不含凭据的签名来源：provider、端点版本和模型标识。"""
    options = gemini_options(model)
    root, version = normalize_endpoint(model.get("base_url"), options.get("api_version", "v1beta"))
    return str(model.get("api_provider", "")), root + "/" + version, str(model["model_identifier"]).removeprefix("models/")


def encode_payloads(
    payloads: list[LLMPayload],
    target: tuple[str, str, str],
    tools: list[Any],
) -> tuple[list[types.Content], list[types.Part], list[types.Tool]]:
    """转换对话与工具声明；工具执行始终由 MoFox 管理，不交给 SDK。

    Args:
        payloads: 按对话顺序排列的消息；系统文本单独收集，签名消息原样回放，
            工具结果通过内部调用 ID 关联原生调用 ID（原生 ID 可以为 None）。
        target: 当前 provider、规范端点及 API 版本、模型标识组成的来源锁；
            必须与签名消息保存的来源一致，不包含凭据。
        tools: 具有 to_schema 接口的 MoFox 工具声明，与消息中的工具声明合并去重；
            不接收由 SDK 自动执行的 Python 函数。

    Returns:
        依次返回对话 contents、系统指令 Part、合并后的 SDK 工具声明。
        本函数不创建网络资源，也不执行工具或修改签名消息的 Part。

    Raises:
        LLMContextError: 签名消息内容或来源被修改，或工具调用/结果无法关联。
        LLMConfigurationError: 系统消息、工具声明或消息内容不受支持。
        ValueError: 非签名工具调用的字符串参数不是合法 JSON。
    """
    contents: list[types.Content] = []
    system: list[types.Part] = []
    declarations: dict[str, types.FunctionDeclaration] = {}
    calls: dict[str, tuple[str, str | None]] = {}

    def add_tool(tool: Any) -> None:
        """转换 tool 的 MoFox 声明，不把 Python 可调用对象交给 SDK 执行。"""
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
                # 其他供应商的推理摘要不是带签名的 Gemini 思考内容。
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
    """映射累计 usage，并将思考 token 与输出 token 分开，避免重复计费。"""
    if usage is None:
        return {}
    def number(name: str) -> int:
        """读取 name 对应计数，只接受非负整数。"""
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


def decode_response(
    response: types.GenerateContentResponse,
    target: tuple[str, str, str],
    *,
    require_finish: bool = True,
) -> ModelClientResult:
    """解析首个候选结果，并独立保存标准内容与下一轮签名回放状态。

    Args:
        response: 官方 SDK 响应；流式适配器通常先合并有序 Part，再解析终态。
        target: 本次响应的 provider、规范端点及 API 版本、模型标识来源锁；
            写入回放状态供下一轮严格匹配，不包含凭据。
        require_finish: 默认为 True，仅接受 STOP 或 MAX_TOKENS 终态且拒绝空结果；
            False 允许缺少 finish 的流中间块，但不放宽内容和工具调用校验。

    Returns:
        包含文本、思考片段、工具调用、用量及终止原因的 ModelClientResult。
        provider_state 保存原始 Part 顺序、签名字节、标准内容指纹，以及唯一
        内部调用 ID 到原生 ID（包括 None）的映射；函数不持有网络资源。

    Raises:
        LLMContentFilterError: 提示词或候选结果被供应商安全规则拦截。
        LLMAPIError: 缺少候选、终态或 Part 不受支持、工具参数不完整或非法，
            或非空原生调用 ID 重复导致结果无法无歧义回放。
    """
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
    # 先预留所有非空原生 ID，避免前面的省 ID 调用占用后面的原生身份。
    reserved_ids: set[str] = set()
    for part in parts:
        if part.function_call and part.function_call.id:
            native_id = part.function_call.id
            if native_id in reserved_ids:
                raise LLMAPIError("Duplicate Gemini function call ID", status_code=502)
            reserved_ids.add(native_id)
    used_ids = set(reserved_ids)
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
            internal_id = call.id
            if not internal_id:
                internal_id = f"gemini_call_{index}"
                suffix = 1
                while internal_id in used_ids:
                    internal_id = f"gemini_call_{index}_{suffix}"
                    suffix += 1
                used_ids.add(internal_id)
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
