"""
Gemini 消息编解码与传输配置解析。

在 MoFox payload 与官方 Gemini SDK 类型之间转换消息、工具声明和响应用量，
并保留签名回放所需的原始 Part、调用 ID 映射与目标来源。
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from google.genai import types
from PIL import Image as PILImage

from ..exceptions import (
    LLMAPIError,
    LLMConfigurationError,
    LLMContentFilterError,
    LLMContextError,
)
from ..payload import Image, LLMPayload, ReasoningText, Text, ToolCall, ToolResult
from ..provider_state import ProviderReplayState, payload_fingerprint
from ..roles import ROLE
from .base import ModelClientResult
from .shared import (
    extract_model_transport_params,
    inject_reason_parameter,
    normalize_schema_for_grammar,
)


def normalize_endpoint(base_url: str | None, api_version: str = "v1beta") -> tuple[str, str]:
    """将 base_url 的旧版本后缀与 SDK 服务根分离，并校验 api_version。

    Args:
        base_url: HTTPS 服务根，可带旧版 API 版本后缀；为空时使用官方端点。
        api_version: 请求使用的 API 版本；默认值可由 base_url 的版本后缀覆盖。

    Returns:
        不含版本后缀的服务根与解析后的 API 版本二元组。

    Raises:
        LLMConfigurationError: 端点包含非法组成部分、版本冲突或版本不受支持。
    """
    value = base_url or "https://generativelanguage.googleapis.com"
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise LLMConfigurationError(
            "Gemini base_url must be an HTTPS service root without credentials/query"
        )
    path = parsed.path.rstrip("/")
    for version in ("v1beta", "v1alpha", "v1"):
        if path.endswith("/" + version):
            if api_version != "v1beta" and api_version != version:
                raise LLMConfigurationError("Gemini API version conflicts with base_url")
            api_version = version
            path = path[: -(len(version) + 1)]
            break
    if api_version not in {"v1", "v1beta", "v1alpha"}:
        raise LLMConfigurationError("Unsupported Gemini API version")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")), api_version


def gemini_options(model: dict[str, Any]) -> dict[str, Any]:
    """在使用 model 的嵌套配置前验证 extra_params 与 Gemini 参数形状。

    Args:
        model: 模型技术条目，Gemini 专属配置位于 extra_params.gemini。

    Returns:
        原始 Gemini 配置字典；未配置时返回空字典。

    Raises:
        LLMConfigurationError: extra_params、gemini 或 generation_config 不是字典。
    """
    extra = model.get("extra_params", {})
    if not isinstance(extra, dict):
        raise LLMConfigurationError("Gemini extra_params must be an object")
    options = extra.get("gemini", {})
    if not isinstance(options, dict) or not isinstance(
        options.get("generation_config", {}), dict
    ):
        raise LLMConfigurationError("Gemini options and generation_config must be objects")
    return options


def resolve_gemini_endpoint(model: dict[str, Any]) -> tuple[str, str]:
    """解析模型的服务根与版本，不要求凭据或其他传输字段。

    Args:
        model: 含可选 base_url 与 extra_params.gemini.api_version 的技术条目。

    Returns:
        规范服务根和 API 版本；不创建网络资源，不改变原始配置。

    Raises:
        LLMConfigurationError: 嵌套配置形状、端点或 API 版本不符合要求。
    """
    extra = model.get("extra_params", {})
    if not isinstance(extra, dict):
        raise LLMConfigurationError("Gemini extra_params must be an object")
    options = extra.get("gemini", {})
    if not isinstance(options, dict):
        raise LLMConfigurationError("Gemini options and generation_config must be objects")
    return normalize_endpoint(model.get("base_url"), options.get("api_version", "v1beta"))


@dataclass(frozen=True, slots=True)
class GeminiTransportSnapshot:
    """生成池与临时预检共用的不可变技术传输配置，也是完整分组键。

    快照只保存值，不创建或持有网络资源；凭据和自定义头不参与 repr，
    但仍参与相等比较与哈希，避免不同认证配置误用同一组传输。
    """

    key: str = field(repr=False)
    root: str
    version: str
    timeout: float
    trust_env: bool
    force_ipv4: bool
    headers: tuple[tuple[str, str], ...] = field(repr=False)


def resolve_gemini_transport(model: dict[str, Any]) -> GeminiTransportSnapshot:
    """验证模型传输边界并冻结快照，不校验或承诺生成能力。

    Args:
        model: 已合并的技术条目，包含认证、端点、超时及传输覆盖字段。

    Returns:
        可用于池键或预检分组的冻结值，不创建或借用网络资源。

    Raises:
        LLMConfigurationError: 端点、超时或传输覆盖不符合现有 Gemini 契约。
        ValueError: 共用传输解析器拒绝缺失凭据或非法配置形状。
    """
    key, _, timeout, trust, ipv4, _, headers, query, _ = extract_model_transport_params(model)
    root, version = resolve_gemini_endpoint(model)
    if query:
        raise LLMConfigurationError("Gemini query overrides are not supported")
    if any(
        name.casefold() in {"authorization", "x-goog-api-key", "host", "content-type"}
        for name in headers
    ):
        raise LLMConfigurationError("Gemini authentication/host headers cannot be overridden")
    if timeout is None or not math.isfinite(timeout) or timeout <= 0:
        raise LLMConfigurationError("Gemini timeout must be a positive finite number")
    return GeminiTransportSnapshot(
        key,
        root,
        version,
        timeout,
        trust,
        ipv4,
        tuple(sorted(headers.items())),
    )


def target_identity(model: dict[str, Any]) -> tuple[str, str, str]:
    """从 model 提取不含凭据的签名来源：provider、端点版本和模型标识。

    Args:
        model: 候选模型条目，只要求模型标识，不额外要求 api_key。

    Returns:
        原有 provider、服务根加版本、移除 models/ 前缀的模型标识三元组。
        本函数只解析值，不创建网络资源。

    Raises:
        LLMConfigurationError: 嵌套配置形状、端点或 API 版本不符合要求。
        KeyError: 模型条目缺少 model_identifier。
    """
    root, version = resolve_gemini_endpoint(model)
    return (
        str(model.get("api_provider", "")),
        root + "/" + version,
        str(model["model_identifier"]).removeprefix("models/"),
    )


def _is_empty_gemini_part(part: types.Part) -> bool:
    """识别没有数据且没有签名的空 Part，避免发送未初始化的 protobuf oneof。

    Args:
        part: Gemini SDK 原生 Part。

    Returns:
        True 表示 Part 没有可回放内容；有数据或非空 thought signature 时返回 False。
    """
    if part.thought_signature:
        return False
    fields = part.model_dump(mode="python", exclude_none=True)
    for metadata_field in ("thought", "thought_signature", "media_resolution", "part_metadata", "speech_metadata"):
        fields.pop(metadata_field, None)
    if fields.get("text") == "":
        fields.pop("text")
    return not fields


@dataclass(slots=True)
class _LogicalFunctionCall:
    """在单个 candidate 内收集一项逻辑调用及其原始 Part 位置。"""

    first_index: int
    call: types.FunctionCall
    has_signature: bool


def _assemble_function_call_parts(
    parts: list[types.Part],
    finish: str | None,
) -> tuple[list[_LogicalFunctionCall], list[types.Part], bool]:
    """组装可唯一关联的 Gemini 调用片段，并保留独立回放视图。

    无 ID 的兼容只接受紧邻的具名空 ``args`` 调用和无名非空完整参数对象。
    多个待配调用时必须以原生调用 ID 消歧；不会根据候选/Part 索引猜测身份。

    Args:
        parts: 首个 candidate 的原始有序 Part。
        finish: Gemini candidate 的终止原因。

    Returns:
        按首次出现顺序排列的逻辑调用、回放用 Part 顺序，以及是否生成规范回放视图。

    Raises:
        LLMAPIError: 调用片段无法唯一关联、字段冲突或当前不支持参数增量协议。
    """
    groups: list[_LogicalFunctionCall] = []
    pending_by_id: dict[str, _LogicalFunctionCall] = {}
    native_id_owners: dict[str, _LogicalFunctionCall] = {}
    adjacent_pending: list[_LogicalFunctionCall] = []
    replacement_by_index: dict[int, types.Part] = {}
    removed_indices: set[int] = set()

    for index, part in enumerate(parts):
        call = part.function_call
        if call is None:
            # 非调用 Part 会关闭仅限无 ID 的相邻配对窗口；ID 匹配仍有效。
            adjacent_pending.clear()
            continue
        if call.partial_args or call.will_continue or finish == "MAX_TOKENS":
            raise LLMAPIError("Incomplete Gemini function arguments", status_code=502)
        if call.name is not None and not isinstance(call.name, str):
            raise LLMAPIError("Invalid Gemini function call", status_code=502)
        if not isinstance(call.args, dict):
            raise LLMAPIError("Invalid Gemini function call", status_code=502)

        if call.name:
            if call.id:
                adjacent_pending.clear()
            if call.id and call.id in native_id_owners:
                raise LLMAPIError("Duplicate Gemini function call ID", status_code=502)
            group = _LogicalFunctionCall(
                first_index=index,
                call=call,
                has_signature=part.thought_signature is not None,
            )
            groups.append(group)
            if call.id:
                native_id_owners[call.id] = group
            if call.args:
                # 完整调用关闭无 ID 相邻窗口；此前零参数调用仍有效。
                adjacent_pending.clear()
            else:
                if call.id:
                    pending_by_id[call.id] = group
                adjacent_pending.append(group)
            continue

        if not call.args:
            raise LLMAPIError("Invalid Gemini function call", status_code=502)

        previous_adjacent = list(adjacent_pending)
        if call.id:
            adjacent_pending.clear()
        group = pending_by_id.get(call.id) if call.id else None
        if group is None:
            if call.id and call.id in native_id_owners:
                raise LLMAPIError("Duplicate Gemini function call ID", status_code=502)
            if call.id and previous_adjacent:
                if len(previous_adjacent) > 1:
                    raise LLMAPIError(
                        "Ambiguous Gemini function call fragments", status_code=502
                    )
                previous_group = previous_adjacent[0]
                if previous_group.call.id:
                    raise LLMAPIError(
                        "Conflicting Gemini function call IDs", status_code=502
                    )
                raise LLMAPIError("Unmatched Gemini function call ID", status_code=502)
            if len(adjacent_pending) > 1:
                raise LLMAPIError(
                    "Ambiguous Gemini function call fragments", status_code=502
                )
            if len(adjacent_pending) == 1:
                group = adjacent_pending[0]
            elif call.id:
                raise LLMAPIError("Unmatched Gemini function call ID", status_code=502)
            else:
                raise LLMAPIError(
                    "Unmatched Gemini function call arguments", status_code=502
                )

        if group.has_signature or part.thought_signature is not None:
            raise LLMAPIError(
                "Signed Gemini function call fragments cannot be merged",
                status_code=502,
            )
        native_id = group.call.id or call.id
        group.call = types.FunctionCall(
            id=native_id,
            name=group.call.name,
            args=call.args,
        )
        if call.id:
            native_id_owners[call.id] = group
        if group.call.id:
            pending_by_id.pop(group.call.id, None)
        adjacent_pending[:] = [pending for pending in adjacent_pending if pending is not group]

        replacement_by_index[group.first_index] = parts[group.first_index].model_copy(
            update={"function_call": group.call}
        )
        removed_indices.add(index)

    replay_parts = [
        replacement_by_index.get(index, part)
        for index, part in enumerate(parts)
        if index not in removed_indices
    ]
    return groups, replay_parts, bool(replacement_by_index)


def encode_payloads(
    payloads: list[LLMPayload],
    target: tuple[str, str, str],
    tools: list[Any],
) -> tuple[list[types.Content], list[types.Part], list[types.Tool]]:
    """转换对话与工具声明；工具执行始终由 MoFox 管理，不交给 SDK。

    Args:
        payloads: 按对话顺序排列的消息；系统文本单独收集，签名消息原样回放，
            工具结果通过内部调用 ID 关联原生调用 ID（原生 ID 可以为 None）；同名无
            原生 ID 的并行调用结果按原始调用顺序写回。
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
    call_order: list[str] = []
    pending_tool_results: list[tuple[str | None, types.Part]] = []

    def flush_tool_results() -> None:
        """仅为同名且无原生 ID 的调用保持原始请求顺序。"""
        if not pending_tool_results:
            return
        order = {call_id: index for index, call_id in enumerate(call_order)}
        ordered_results = list(pending_tool_results)
        ambiguous_names: dict[str, list[int]] = {}
        for index, (call_id, _part) in enumerate(ordered_results):
            call_info = calls.get(call_id or "")
            if call_info is not None and call_info[1] is None:
                ambiguous_names.setdefault(call_info[0], []).append(index)
        for indexes in ambiguous_names.values():
            if len(indexes) < 2:
                continue
            values = sorted(
                (ordered_results[index] for index in indexes),
                key=lambda result: order.get(result[0] or "", len(order)),
            )
            for index, value in zip(indexes, values):
                ordered_results[index] = value
        contents.append(
            types.Content(
                role="user",
                parts=[part for _, part in ordered_results],
            )
        )
        pending_tool_results.clear()

    def add_tool(tool: Any) -> None:
        """转换并按名称收集工具声明，不把 Python 可调用对象交给 SDK 执行。

        Args:
            tool: 实现 to_schema() 的 MoFox 工具；同名声明必须保持一致。

        Raises:
            LLMConfigurationError: 工具缺少有效名称，或同名声明互相冲突。
        """
        schema = tool.to_schema()
        schema = schema.get("function", schema)
        name = schema.get("name")
        if not isinstance(name, str) or not name:
            raise LLMConfigurationError("Gemini tool requires a name")
        parameters = json.loads(
            json.dumps(schema.get("parameters", {"type": "object", "properties": {}}))
        )
        normalize_schema_for_grammar(parameters)
        parameters = inject_reason_parameter(tool, parameters)
        declaration = types.FunctionDeclaration(
            name=name,
            description=schema.get("description", ""),
            parameters_json_schema=parameters,
        )
        if name in declarations and declarations[name] != declaration:
            raise LLMConfigurationError("Conflicting Gemini tool declarations")
        declarations[name] = declaration

    for tool in tools:
        add_tool(tool)
    for payload in payloads:
        if payload.role != ROLE.TOOL_RESULT:
            flush_tool_results()
        if payload.role == ROLE.ASSISTANT:
            call_order = []
        if payload.role == ROLE.TOOL:
            for tool in payload.content:
                add_tool(tool)
            continue
        if payload.role == ROLE.SYSTEM:
            for part in payload.content:
                if not isinstance(part, Text):
                    raise LLMConfigurationError("Gemini system instructions require text")
                if part.text:
                    system.append(types.Part(text=part.text))
            continue
        state = payload.provider_state
        if state is not None and state.provider == "gemini":
            if state.target != target or not state.matches(payload.content):
                raise LLMContextError(
                    "Gemini signed content was changed or belongs to another target"
                )
            replay_json = state.replay_parts_json or state.parts_json
            parts: list[types.Part] = []
            for item in json.loads(replay_json):
                part = types.Part.model_validate(item)
                if not _is_empty_gemini_part(part):
                    parts.append(part)
            native_ids = dict(state.call_ids)
            for part in payload.content:
                if isinstance(part, ToolCall) and part.id:
                    calls[part.id] = (part.name, native_ids.get(part.id))
                    call_order.append(part.id)
            contents.append(types.Content(role="model", parts=parts))
            continue
        parts: list[types.Part] = []
        for part in payload.content:
            if isinstance(part, Text):
                if part.text:
                    parts.append(types.Part(text=part.text))
            elif isinstance(part, ReasoningText):
                # 其他供应商的推理摘要不是带签名的 Gemini 思考内容。
                continue
            elif isinstance(part, ToolCall):
                args = json.loads(part.args) if isinstance(part.args, str) else part.args
                if not isinstance(args, dict) or not part.id:
                    raise LLMContextError(
                        "Gemini function call needs object arguments and a stable ID"
                    )
                calls[part.id] = (part.name, part.id)
                call_order.append(part.id)
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=part.id,
                            name=part.name,
                            args=args,
                        )
                    )
                )
            elif isinstance(part, ToolResult):
                if not part.call_id or part.call_id not in calls:
                    raise LLMContextError("Gemini tool result has no matching function call")
                name, native_id = calls[part.call_id]
                value = (
                    part.value
                    if isinstance(part.value, dict)
                    else {"result": part.to_text()}
                )
                pending_tool_results.append(
                    (
                        part.call_id,
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=name,
                                id=native_id,
                                response=value,
                            )
                        ),
                    )
                )
                continue
            elif isinstance(part, Image):
                try:
                    data = base64.b64decode(part.value, validate=True)
                    with PILImage.open(BytesIO(data)) as image:
                        mime = PILImage.MIME.get(image.format or "")
                    if mime not in {
                        "image/png",
                        "image/jpeg",
                        "image/webp",
                        "image/heic",
                        "image/heif",
                    }:
                        raise ValueError("unsupported image")
                except Exception as exc:
                    raise LLMConfigurationError(
                        "Gemini image MIME could not be validated"
                    ) from exc
                parts.append(types.Part.from_bytes(data=data, mime_type=mime))
            else:
                raise LLMConfigurationError(
                    "Unsupported Gemini payload content: " + type(part).__name__
                )
        if parts:
            contents.append(
                types.Content(
                    role="model" if payload.role == ROLE.ASSISTANT else "user",
                    parts=parts,
                )
            )
    flush_tool_results()
    declared = (
        [types.Tool(function_declarations=list(declarations.values()))]
        if declarations
        else []
    )
    return contents, system, declared


def extract_usage(usage: types.GenerateContentResponseUsageMetadata | None) -> dict[str, Any]:
    """映射累计 usage，并将思考 token 与输出 token 分开，避免重复计费。

    Args:
        usage: SDK 返回的累计用量；缺失的计数字段按零处理。

    Returns:
        内部标准用量字典，包含思考及缓存 token 计数；usage 为空时返回空字典。

    Raises:
        LLMAPIError: 用量计数不是非负整数，或使用了布尔值。
    """
    if usage is None:
        return {}

    def number(name: str) -> int:
        """读取 name 对应计数，只接受非负整数。

        Args:
            name: SDK 用量元数据中的计数字段名。

        Returns:
            字段的非负整数值；字段缺失或为 None 时返回零。

        Raises:
            LLMAPIError: 字段值不是非负整数，或使用了布尔值。
        """
        value = getattr(usage, name, None)
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LLMAPIError("Invalid Gemini usage counter", status_code=502)
        return value

    prompt, output, thought, cache = (
        number(n)
        for n in (
            "prompt_token_count",
            "candidates_token_count",
            "thoughts_token_count",
            "cached_content_token_count",
        )
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": output,
        "reasoning_tokens": thought,
        "completion_includes_reasoning": False,
        "total_tokens": number("total_token_count"),
        "cache_hit_tokens": cache,
        "cache_miss_tokens": max(0, prompt - cache),
        "cache_write_tokens": 0,
    }


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
        provider_state 保存原始 Part、可选规范回放 Part、签名字节、标准内容指纹，
        以及唯一内部调用 ID 到原生 ID（包括 None）的映射；函数不持有网络资源。

    Raises:
        LLMContentFilterError: 提示词或候选结果被供应商安全规则拦截。
        LLMAPIError: 缺少候选、终态或 Part 不受支持、工具参数不完整或非法，
            或非空原生调用 ID 重复导致结果无法无歧义回放。
    """
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise LLMContentFilterError(
            "Gemini blocked the prompt",
            filter_type=str(response.prompt_feedback.block_reason),
        )
    if not response.candidates:
        raise LLMAPIError("Gemini returned no candidates", status_code=502)
    candidate = response.candidates[0]
    finish = candidate.finish_reason.value if candidate.finish_reason else None
    if finish in {
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "RECITATION",
    }:
        raise LLMContentFilterError("Gemini blocked the response", filter_type=finish)
    if require_finish and finish not in {"STOP", "MAX_TOKENS"}:
        raise LLMAPIError(
            "Gemini returned an unsupported or missing finish reason",
            status_code=502,
        )
    parts = list(candidate.content.parts or []) if candidate.content else []
    parts = [part for part in parts if not _is_empty_gemini_part(part)]
    for part in parts:
        unsupported = set(part.model_dump(exclude_none=True)) - {
            "text",
            "thought",
            "thought_signature",
            "function_call",
        }
        if unsupported:
            raise LLMAPIError("Unsupported Gemini response Part", status_code=422)

    logical_calls, replay_parts, canonicalized_replay = _assemble_function_call_parts(
        parts, finish
    )
    texts: list[str] = []
    thoughts: list[ReasoningText] = []
    for part in parts:
        if part.text:
            if part.thought:
                thoughts.append(ReasoningText(part.text))
            else:
                texts.append(part.text)

    tool_calls: list[dict[str, Any]] = []
    mapping: list[tuple[str, str | None]] = []
    reserved_ids = {group.call.id for group in logical_calls if group.call.id}
    used_ids = set(reserved_ids)
    for group in logical_calls:
        call = group.call
        internal_id = call.id
        if not internal_id:
            internal_id = f"gemini_call_{group.first_index}"
            suffix = 1
            while internal_id in used_ids:
                internal_id = f"gemini_call_{group.first_index}_{suffix}"
                suffix += 1
            used_ids.add(internal_id)
        tool_calls.append({"id": internal_id, "name": call.name, "args": call.args})
        mapping.append((internal_id, call.id))
    if (
        require_finish
        and not texts
        and not thoughts
        and not tool_calls
        and not any(p.thought_signature for p in parts)
    ):
        raise LLMAPIError("Gemini returned empty completed content", status_code=422)
    standard: list[Any] = list(thoughts)
    message = "".join(texts) or None
    if message:
        standard.append(Text(message))
    standard.extend(
        ToolCall(id=c["id"], name=c["name"], args=c["args"]) for c in tool_calls
    )
    if not standard:
        # 与公开响应 to_payload() 的空占位一致；仅签名的原生 Part 仍原样保存。
        standard.append(Text(""))
    raw_parts_json = json.dumps(
        [part.model_dump(mode="json", exclude_none=True) for part in parts]
    )
    replay = ProviderReplayState(
        provider="gemini",
        target=target,
        parts_json=raw_parts_json,
        content_fingerprint=payload_fingerprint(standard),
        call_ids=tuple(mapping),
        schema_version=1,
        replay_parts_json=(
            json.dumps(
                [part.model_dump(mode="json", exclude_none=True) for part in replay_parts]
            )
            if canonicalized_replay
            else None
        ),
    )
    return ModelClientResult(
        message=message,
        tool_calls=tool_calls or None,
        reasoning_content=thoughts or None,
        usage=extract_usage(response.usage_metadata),
        stop_reason=finish,
        provider_state=replay,
    )
