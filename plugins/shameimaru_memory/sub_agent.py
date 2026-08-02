"""Shameimaru Memory 内部子 agent。

封装 LLM 调用：通过配置的模型任务名发起请求，
提示词优先读取 prompt_api 中注册的模板，缺失时回退到 prompts 常量。
提供 JSON 数组解析等辅助函数。
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.app.plugin_system.api import llm_api, log_api, prompt_api
from src.kernel.llm import LLMPayload, ROLE, Text

logger = log_api.get_logger("shameimaru_memory.sub_agent")


def resolve_prompt(name: str, fallback: str) -> str:
    """解析子 agent 提示词：优先 prompt_api 已注册模板，缺失时回退常量。"""
    try:
        template = prompt_api.get_template(name)
        if template is not None:
            return str(getattr(template, "template", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"读取提示词模板失败 {name}: {exc}")
    return fallback


async def call_sub_agent(
    *,
    task: str,
    request_name: str,
    system: str,
    user: str,
    stream_id: str | None = None,
) -> str:
    """调用一次内部子 agent，返回模型纯文本响应。

    Args:
        task: 模型任务名（model.toml 中的 model_tasks 节）。
        request_name: LLM 请求名称，用于统计。
        system: 系统提示词。
        user: 用户输入。
        stream_id: 可选的聊天流 ID，用于 LLM 统计聚合。

    Returns:
        str: 模型响应文本（去除首尾空白）。
    """
    try:
        model_set = llm_api.get_model_set_by_task(task)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"获取模型任务失败 task={task}: {exc}")
        return ""

    try:
        request = llm_api.create_llm_request(
            model_set=model_set,
            request_name=request_name,
            stream_id=stream_id,
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user)))
        response = await request.send(stream=False)
        text = str(await response or "").strip()
        return text
    except Exception as exc:  # noqa: BLE001
        logger.error(f"子 agent 调用失败 request_name={request_name}: {exc}")
        return ""


def extract_json_array(text: str) -> list[dict[str, Any]]:
    """从模型输出中提取 JSON 数组。

    依次尝试：
    1. 直接 json.loads（若整体就是 JSON）。
    2. 去掉代码围栏后 json.loads。
    3. 正则提取平衡的 [...] 片段后 json.loads。
    全部失败时返回空列表。

    Args:
        text: 模型输出文本。

    Returns:
        list[dict[str, Any]]: 解析出的 JSON 数组；无法解析时为空列表。
    """
    if not text or not text.strip():
        return []

    candidates: list[str] = [text.strip()]
    if text.startswith("```"):
        stripped_fence = re.sub(r"^```[a-zA-Z0-9_+-]*\s*|\s*```$", "", text.strip())
        candidates.append(stripped_fence)

    try:
        parsed = json.loads(candidates[0])
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except (json.JSONDecodeError, TypeError):
        pass

    for candidate in candidates[1:]:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except (json.JSONDecodeError, TypeError):
            continue

    for candidate in candidates:
        match = _find_balanced_bracket(candidate)
        if match is None:
            continue
        try:
            parsed = json.loads(match)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except (json.JSONDecodeError, TypeError):
            continue

    return []


def _find_balanced_bracket(text: str) -> str | None:
    """从文本中提取第一个平衡的方括号片段。"""
    start = text.find("[")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
