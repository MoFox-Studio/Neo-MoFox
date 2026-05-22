"""LLM 上下文的 token 预算与压缩辅助逻辑。

这个模块负责上下文管理里的预算侧问题：何时压缩、预留多少上下文空间，以及在
保留 pinned system/tool payload 的前提下如何裁剪更早的对话轮次。
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

from .payload import LLMPayload
from .roles import ROLE
from .token_counter import count_payload_tokens
from .types import ModelEntry

if TYPE_CHECKING:
    from .request import LLMRequest

TokenCounter = Callable[[list[LLMPayload]], int]
AsyncContextCompressionHandler = Callable[
    ["LLMRequest", list[LLMPayload], ModelEntry],
    Awaitable[list[LLMPayload]],
]

logger = get_logger("kernel.llm.context", display="LLM 上下文")

CONTEXT_COMPRESSION_TRIGGER_RATIO = 0.95


def maybe_trim_payloads(
    payloads: list[LLMPayload],
    *,
    max_token_budget: int | None = None,
    token_counter: TokenCounter | None = None,
) -> list[LLMPayload]:
    """仅在 token 计数确认超预算时才裁剪 payload。"""
    trimmed = payloads
    if (
        max_token_budget is not None
        and max_token_budget > 0
        and token_counter is not None
        and token_counter(trimmed) > max_token_budget
    ):
        trimmed = trim_payloads_by_tokens(trimmed, max_token_budget, token_counter)
    return trimmed


async def prepare_payloads_for_model(
    payloads: list[LLMPayload],
    model: ModelEntry,
    *,
    request: LLMRequest | None = None,
    context_compression_handler: AsyncContextCompressionHandler | None = None,
) -> list[LLMPayload]:  
    """在发送前，针对具体模型执行压缩和裁剪。"""
    prepared = await maybe_compress_payloads(
        payloads,
        model,
        request=request,
        context_compression_handler=context_compression_handler,
    )

    budget = compute_effective_context_budget(model)
    model_identifier = model.get("model_identifier")
    if budget is None or not isinstance(model_identifier, str) or not model_identifier:
        return prepared

    try:
        if count_payload_tokens(prepared, model_identifier=model_identifier) <= budget:
            return prepared
    except RuntimeError:
        return prepared

    def token_counter(items: list[LLMPayload]) -> int:
        try:
            return count_payload_tokens(items, model_identifier=model_identifier)
        except RuntimeError:
            return 0

    return maybe_trim_payloads(
        prepared,
        max_token_budget=budget,
        token_counter=token_counter,
    )


def compute_effective_context_budget(model: ModelEntry) -> int | None:
    """根据预留比例或预留 token 规则计算可用上下文预算。"""
    max_context = model.get("max_context")
    if not isinstance(max_context, int) or max_context <= 0:
        return None

    extra_params = model.get("extra_params")
    if not isinstance(extra_params, dict):
        extra_params = {}

    reserve_tokens = extra_params.get("context_reserve_tokens")
    fixed_reserve = (
        reserve_tokens if isinstance(reserve_tokens, int) and reserve_tokens > 0 else 0
    )

    reserve_ratio = extra_params.get("context_reserve_ratio")
    ratio = (
        max(0.0, float(reserve_ratio))
        if isinstance(reserve_ratio, (int, float))
        else 0.0
    )
    ratio_reserve = int(math.floor(max_context * ratio))

    effective_budget = max_context - max(fixed_reserve, ratio_reserve)
    return effective_budget if effective_budget > 0 else 1


async def maybe_compress_payloads(
    payloads: list[LLMPayload],
    model: ModelEntry,
    *,
    request: LLMRequest | None = None,
    context_compression_handler: AsyncContextCompressionHandler | None = None,
) -> list[LLMPayload]:
    """当上下文逼近上限时调用可选的压缩钩子。"""
    if context_compression_handler is None or request is None:
        return payloads

    model_identifier = model.get("model_identifier")
    max_context = model.get("max_context")
    if (
        not isinstance(model_identifier, str)
        or not model_identifier
        or not isinstance(max_context, int)
        or max_context <= 0
    ):
        return payloads

    try:
        total_tokens = count_payload_tokens(payloads, model_identifier=model_identifier)
    except RuntimeError:
        return payloads

    compression_trigger = max(
        1, int(math.floor(max_context * CONTEXT_COMPRESSION_TRIGGER_RATIO))
    )
    if total_tokens < compression_trigger:
        return payloads

    pinned, tail = split_pinned_prefix(payloads)
    if not tail:
        return payloads
    try:
        logger.info(
            f"触发上下文压缩: total_tokens={total_tokens}, "
            f"model_name={model.get('model_identifier')}, "
            f"request_name={request.request_name}",
        )
        summary_payloads = await context_compression_handler(
            request,
            payloads,
            model,
        )
    except Exception as exc:
        logger.warning(f"上下文压缩失败，跳过压缩并继续原请求: {exc}")
        return payloads

    if not summary_payloads:
        return payloads

    return pinned + summary_payloads


def trim_payloads_by_tokens(
    payloads: list[LLMPayload],
    token_budget: int,
    token_counter: TokenCounter,
) -> list[LLMPayload]:
    """丢弃最早的问答分组，直到对话重新落入 token 预算内。"""
    pinned, tail = split_pinned_prefix(payloads)
    groups = build_qa_groups(tail)
    if not groups:
        return payloads

    kept_groups = list(groups)
    while len(kept_groups) > 1:
        # 按整组对话裁剪，而不是逐条 payload 裁剪，这样 tool-call 链路和
        # assistant 的承接关系才不会被裁断。
        candidate = pinned + flatten_groups(kept_groups)
        if token_counter(candidate) <= token_budget:
            break
        kept_groups.pop(0)

    return pinned + flatten_groups(kept_groups)


def split_pinned_prefix(
    payloads: list[LLMPayload],
) -> tuple[list[LLMPayload], list[LLMPayload]]:
    """把上下文拆成 pinned 前缀和可裁剪尾部。"""
    pinned_roles = {ROLE.SYSTEM, ROLE.TOOL}
    pinned = [p for p in payloads if p.role in pinned_roles]
    tail = [p for p in payloads if p.role not in pinned_roles]
    return pinned, tail


def build_qa_groups(payloads: list[LLMPayload]) -> list[list[LLMPayload]]:
    """按 user 消息为锚点，把 payload 分组成对话轮次。"""
    groups: list[list[LLMPayload]] = []
    current: list[LLMPayload] = []

    for payload in payloads:
        if payload.role == ROLE.USER:
            if current:
                groups.append(current)
            current = [payload]
        elif not current:
            groups.append([payload])
        else:
            current.append(payload)

    if current:
        groups.append(current)

    return groups


def flatten_groups(groups: list[list[LLMPayload]]) -> list[LLMPayload]:
    """把分组后的 payload 重新拍平成一个有序列表。"""
    return [payload for group in groups for payload in group]
