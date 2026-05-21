"""LLM 上下文结构辅助逻辑。

这个模块只负责对话结构本身：payload 的追加方式，以及 assistant tool call
与后续 tool result 的配对校验。token 预算和压缩逻辑放在别处。
"""

from __future__ import annotations

import uuid

from .exceptions import LLMContextError
from .payload import LLMPayload
from .payload.tooling import ToolCall, ToolResult
from .roles import ROLE


def append_payload(
    payloads: list[LLMPayload],
    payload: LLMPayload,
    *,
    position: int | None = None,
) -> list[LLMPayload]:
    """在保持现有合并语义的前提下追加一个 payload。"""
    updated = list(payloads)
    if position is not None:
        updated.insert(int(position), payload)
        return updated
    if updated and updated[-1].role == payload.role:
        updated[-1].content.extend(payload.content)
        return updated
    updated.append(payload)
    return updated


def validate_payload_sequence(
    payloads: list[LLMPayload],
    *,
    allow_incomplete_tail: bool,
) -> None:
    """校验对话结构以及 tool-call 配对规则。"""
    pinned_roles = {ROLE.SYSTEM, ROLE.TOOL}
    convo = [p for p in payloads if p.role not in pinned_roles]

    def err(message: str) -> None:
        roles = [p.role.value for p in convo]
        raise LLMContextError(f"LLM 上下文不合法: {message}; roles={roles}")

    idx = 0
    while idx < len(convo):
        payload = convo[idx]

        if payload.role == ROLE.USER:
            idx += 1
            continue

        if payload.role == ROLE.ASSISTANT:
            if idx == 0:
                err("对话不能以 assistant 开始")
            prev_role = convo[idx - 1].role
            if prev_role not in {ROLE.USER, ROLE.TOOL_RESULT}:
                err("assistant 前必须是 user 或 tool_result")

            tool_calls = [part for part in payload.content if isinstance(part, ToolCall)]
            if not tool_calls:
                idx += 1
                continue

            expected_ids: set[str] = set()
            for part in tool_calls:
                # 这里补齐缺失 id，保证后续校验和 provider 转换都能依赖稳定的
                # tool-call 标识符。
                if not part.id:
                    object.__setattr__(part, "id", f"call_{uuid.uuid4().hex[:8]}")
                expected_ids.add(str(part.id))

            j = idx + 1
            if j >= len(convo):
                if allow_incomplete_tail:
                    return
                err("assistant(tool_calls) 后缺少 tool_result")

            seen: set[str] = set()
            while j < len(convo) and convo[j].role == ROLE.TOOL_RESULT:
                results = [
                    part for part in convo[j].content if isinstance(part, ToolResult)
                ]
                if not results:
                    err("tool_result payload 中缺少 ToolResult 内容")
                for result in results:
                    if not result.call_id:
                        err("ToolResult 缺少 call_id")
                    call_id = str(result.call_id)
                    if call_id not in expected_ids:
                        err(f"ToolResult.call_id={call_id} 不匹配任何 tool_call")
                    if call_id in seen:
                        err(f"重复的 ToolResult.call_id={call_id}")
                    seen.add(call_id)
                j += 1

            missing = expected_ids - seen
            if missing:
                if allow_incomplete_tail and j >= len(convo):
                    return
                err(f"tool_result 未覆盖全部 tool_call: missing={sorted(missing)}")

            if j < len(convo) and convo[j].role == ROLE.USER:
                err("tool_result 后不能直接跟 user（缺少 assistant 承接）")

            if j < len(convo) and convo[j].role != ROLE.ASSISTANT:
                err("tool_result 后只能是 assistant 或结束")

            idx = j
            continue

        if payload.role == ROLE.TOOL_RESULT:
            err("孤立的 tool_result（未紧随 assistant.tool_calls）")

        err(f"未知的对话角色 {payload.role}")
