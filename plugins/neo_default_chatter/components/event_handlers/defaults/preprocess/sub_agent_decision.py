"""Neo-Default-Chatter 预处理事件处理器：sub_agent 轻量 LLM 判定。

订阅 ``neo_default_chatter:preprocess`` 事件，在 概率直通门未命中放行后，发起一次
轻量 LLM 单轮判定，让模型基于「最近历史 + 本轮未读消息 + 一点上下文」决定
本轮是否值得主 chatter 立即回复。

判定结果通过修改事件参数中的决策字段回传：

- 值得回复 → ``proceed=True``、``reason="sub_agent 判定值得回复: <模型理由>"``
- 不值得 → ``proceed=False``、``reason="sub_agent 判定不值得回复: <模型理由>"``

提示词模板注册在 :mod:`utils.prompts`，由 :mod:`utils.prompt_builder` 渲染；
LLM 请求通过 ``llm_api`` 公开入口构造，判定解析与决策回写自包含于本处理器。
"""

from __future__ import annotations

import json
from typing import Any

from src.app.plugin_system.api import llm_api
from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import (
    ChatStream,
    LLMPayload,
    LLMResponse,
    Message,
    ROLE,
    Text,
)
from src.kernel.event import EventDecision

from ....config import NeoChatterConfig
from .....utils.event_publisher import NdfcEvent
from .....utils.prompt_builder import NeoChatterPromptBuilder

logger = get_logger("neo_default_chatter.preprocess.sub_agent")

#: NFC 预处理事件名。
_PREPROCESS_EVENT = NdfcEvent.PREPROCESS


class SubAgentDecisionHandler(BaseEventHandler):
    """sub_agent 轻量 LLM 判定处理器。

    在 ``neo_default_chatter:preprocess`` 事件链中位于 概率直通门之后（weight 较低）。
    若 概率直通门已经 ``STOP``，本处理器不会执行；若 概率直通门未命中放行，
    本处理器发起一次 LLM 单轮判定决定是否 proceed。

    提示词模板由 :mod:`utils.prompt_builder` 统一渲染；判定解析与决策回写
    自包含在本处理器内部，不依赖 sub_agent 协作管理器或持久化子代理会话。

    Class Attributes:
        weight: 0，低于 概率直通处理器（1），确保 概率直通处理器先执行。
        init_subscribe: 订阅 ``neo_default_chatter:preprocess`` 事件。
    """

    name = "sub_agent_decision"
    description = "sub_agent 轻量 LLM 判定处理器 - 单轮判定是否值得主 chatter 立即回复"
    weight = 0
    init_subscribe = [_PREPROCESS_EVENT]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行轻量 LLM 判定。

        Args:
            event_name: 事件名称（由 EventBus 传入）。
            params: 事件参数字典，包含 ``chat_stream`` / ``unreads`` / ``config``
                以及预填的决策字段 ``proceed`` / ``reason`` 等。

        Returns:
            ``(EventDecision, params)``：

            - 判定值得回复 → ``(SUCCESS, params)``，``proceed=True``、``reason`` 写入理由；
            - 判定不值得回复 → ``(SUCCESS, params)``，``proceed=False``、``reason`` 写入理由，
              主 chatter 走 Wait 不处理本轮未读；
            - 上游已放行（``proceed=True``）/ 处理器禁用 / 无未读 → ``(SUCCESS, params)``，
              不修改 ``proceed``（保持进入时的值）；
            - LLM 异常 / 模型未配置 / 响应解析失败 → ``(SUCCESS, params)``，
              fail-open 置 ``proceed=True``，避免误拦截。
        """
        cfg = self._get_sub_agent_config(params)
        if cfg is None or not bool(cfg.enabled):
            return EventDecision.SUCCESS, params

        # 上游处理器已放行（proceed=True）则不再判定，保持放行
        if params.get("proceed") is True:
            return EventDecision.SUCCESS, params

        unreads = self._extract_unreads(params)
        chat_stream = params.get("chat_stream")
        if not unreads or not isinstance(chat_stream, ChatStream):
            return EventDecision.SUCCESS, params

        try:
            response = await self._call_llm(chat_stream, unreads, params, cfg)
        except Exception as error:  # noqa: BLE001
            params["proceed"] = True
            logger.warning(
                f"[SubAgent] LLM 判定失败，按放行处理: {error}",
                exc_info=True,
            )
            return EventDecision.SUCCESS, params

        if response is None:
            params["proceed"] = True
            return EventDecision.SUCCESS, params

        respond, reason = self._parse_decision(response)
        if respond:
            params["proceed"] = True
            params["reason"] = f"sub_agent 判定值得回复: {reason}"
            logger.info(
                f"[SubAgent] 判定值得回复 stream={chat_stream.stream_id[:8]} "
                f"reason={reason}"
            )
        else:
            params["proceed"] = False
            params["reason"] = f"sub_agent 判定不值得回复: {reason}"
            logger.info(
                f"[SubAgent] 判定不值得回复 stream={chat_stream.stream_id[:8]} "
                f"reason={reason}"
            )
        return EventDecision.SUCCESS, params

    # ==================== 私有辅助：所有处理逻辑自包含 ====================

    @staticmethod
    def _get_sub_agent_config(
        params: dict[str, Any]
    ) -> "NeoChatterConfig.PluginSection.PreprocessSubAgentSection | None":
        """从 ``params['config']`` 读取 sub_agent 配置子节。"""
        config = params.get("config")
        if not isinstance(config, NeoChatterConfig):
            return None
        return config.plugin.preprocess_sub_agent

    @staticmethod
    def _extract_unreads(params: dict[str, Any]) -> list[Message]:
        """从 ``params['unreads']`` 取未读消息列表。"""
        raw = params.get("unreads")
        if not isinstance(raw, list):
            return []
        return [msg for msg in raw if isinstance(msg, Message)]

    async def _call_llm(
        self,
        chat_stream: ChatStream,
        unreads: list[Message],
        params: dict[str, Any],
        cfg: "NeoChatterConfig.PluginSection.PreprocessSubAgentSection",
    ) -> LLMResponse | None:
        """构造并执行一次轻量 LLM 判定请求。

        Args:
            chat_stream: 当前聊天流。
            unreads: 本轮未读消息。
            params: 事件参数（用于读取 ``history_text``）。
            cfg: sub_agent 配置子节。

        Returns:
            :class:`LLMResponse` 实例；模型任务未配置时返回 ``None``。
        """
        try:
            model_set = llm_api.get_model_set_by_task(str(cfg.task_name))
        except (KeyError, ValueError, RuntimeError) as error:
            logger.warning(
                f"[SubAgent] 模型任务 '{cfg.task_name}' 未配置或未初始化，跳过判定: {error}"
            )
            return None

        # 用配置里的判定温度覆盖任务默认温度，让判定更确定
        # ModelEntry 是 TypedDict，运行时即 dict，可直接按 key 修改
        decision_temp = float(cfg.decision_temperature)
        for entry in model_set:
            entry["temperature"] = decision_temp

        request = llm_api.create_llm_request(
            model_set,
            request_name=str(cfg.request_name),
        )

        history_text = str(params.get("history_text") or "").strip()
        system_prompt = await NeoChatterPromptBuilder.build_sub_agent_system_prompt()
        user_prompt = await NeoChatterPromptBuilder.build_sub_agent_user_prompt(
            chat_stream, history_text, unreads, cfg
        )

        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))

        response = await request.send(stream=False)
        await response
        return response

    @staticmethod
    def _parse_decision(response: LLMResponse) -> tuple[bool, str]:
        """从 LLM 响应解析 ``(respond, reason)``。

        优先按 JSON 解析；失败时回退到关键词匹配（RESPOND / SKIP）；
        仍失败时默认 ``respond=True``，避免误拦截。
        """
        raw_message = getattr(response, "message", "") or ""
        text = raw_message.strip() if isinstance(raw_message, str) else str(raw_message).strip()

        # 1) 尝试 JSON 解析（容忍模型额外包了 markdown 代码块）
        json_text = text
        if json_text.startswith("```"):
            json_text = json_text.strip("`")
            # 去掉可能的 ```json 前缀
            if json_text.lower().startswith("json"):
                json_text = json_text[4:].lstrip()
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict) and "respond" in parsed:
            respond = bool(parsed.get("respond"))
            reason = str(parsed.get("reason") or "").strip() or "未提供理由"
            return respond, reason

        # 2) 关键词回退：RESPOND / SKIP
        lowered = text.lower()
        if "skip" in lowered or "不回复" in lowered or "不值得" in lowered:
            return False, _first_line(text) or "模型判定不回复"
        if "respond" in lowered or "回复" in lowered or "值得" in lowered:
            return True, _first_line(text) or "模型判定回复"

        # 3) 解析失败：默认放行，避免误拦截
        return True, "判定解析失败，默认放行"


def _first_line(text: str) -> str:
    """取文本首行，去掉可能的 JSON / markdown 前缀。"""
    if not text:
        return ""
    return text.splitlines()[0].strip()


__all__ = ["SubAgentDecisionHandler"]
