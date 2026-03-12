"""Booku Memory Agent 共享辅助函数。"""

from __future__ import annotations

from typing import Any

from src.kernel.llm import LLMPayload, ROLE, Text

from ..config import BookuMemoryConfig


def get_max_reasoning_steps(config: Any) -> int:
    """从插件配置读取内部 LLM 的最大推理轮次限制。"""
    if isinstance(config, BookuMemoryConfig):
        return max(1, int(config.internal_llm.max_reasoning_steps))
    return 6


def get_internal_task_name(config: Any) -> str:
    """从插件配置读取内部 LLM 决策使用的模型任务名。"""
    if isinstance(config, BookuMemoryConfig):
        task_name = config.internal_llm.task_name.strip()
        if task_name:
            return task_name
    return "tool_use"


def normalize_tool_name(name: str) -> str:
    """去除工具名中可能存在的 ``tool-`` 前缀。"""
    return name[5:] if name.startswith("tool-") else name


def build_step_reminder(
    *,
    step_index: int,
    max_steps: int,
    final_round_instruction: str,
    ongoing_instruction: str,
) -> str:
    """构建内部推理轮次提醒文本。"""
    current = step_index + 1
    remaining_after = max(0, max_steps - current)

    if current >= max_steps:
        return (
            "【推理轮次提醒】"
            f"你已到达最后一轮 follow-up（{current}/{max_steps}）。"
            f"{final_round_instruction}"
        )

    return (
        "【推理轮次提醒】"
        f"当前 follow-up 轮次：{current}/{max_steps}。"
        f"本轮结束后剩余可用轮数：{remaining_after}。"
        f"{ongoing_instruction}"
    )


def with_single_system_payload(
    payloads: list[LLMPayload],
    *,
    base_system_prompt: str,
    step_reminder: str,
) -> list[LLMPayload]:
    """确保 payloads 中只存在一个 SYSTEM payload，并注入最新轮次提醒。"""
    tool_payloads: list[LLMPayload] = []
    convo_payloads: list[LLMPayload] = []
    for payload in payloads:
        if payload.role == ROLE.SYSTEM:
            continue
        if payload.role == ROLE.TOOL:
            tool_payloads.append(payload)
            continue
        convo_payloads.append(payload)

    system_payload = LLMPayload(
        ROLE.SYSTEM,
        [Text(step_reminder), Text(base_system_prompt)],
    )
    return [system_payload, *tool_payloads, *convo_payloads]