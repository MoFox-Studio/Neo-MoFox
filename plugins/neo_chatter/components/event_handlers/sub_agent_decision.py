"""Neo-Chatter 预处理事件处理器：sub_agent 轻量 LLM 判定。

订阅 ``neo_chatter:preprocess`` 事件，在 概率直通门未命中放行后，发起一次
轻量 LLM 单轮判定，让模型基于「最近历史 + 本轮未读消息 + 一点上下文」决定
本轮是否值得主 chatter 立即回复。

判定结果通过修改事件参数中的决策字段回传：

- 值得回复 → ``proceed=True``、``reason="sub_agent 判定值得回复: <模型理由>"``
- 不值得 → ``proceed=False``、``reason="sub_agent 判定不值得回复: <模型理由>"``

所有 prompt 构造、LLM 调用与响应解析均自包含在本处理器内部，不依赖外部
prompt 模块或子代理协作管理器；模型请求通过 ``llm_api`` 公开入口构造。
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

from ..config import NeoChatterConfig

logger = get_logger("neo_chatter.preprocess.sub_agent")

#: NFC 预处理事件名。
_PREPROCESS_EVENT = "neo_chatter:preprocess"

#: 判定系统提示词模板。要求模型严格输出 JSON。
_SYSTEM_PROMPT = """你是消息判定子代理，负责判断主聊天代理是否应当立即回复本轮未读消息。

判定原则：
1. 以下情况判定为「值得回复」(respond=true)：
   - 用户直接向机器人提问或请求帮助
   - 用户精准 @ 机器人或回复机器人发言
   - 当前对话主题与机器人人设 / 角色相关
   - 群聊中出现需要机器人介入的话题或明确的指向性发言
2. 以下情况判定为「不值得回复」(respond=false)：
   - 与机器人无关的闲聊、刷屏、纯表情 / 符号
   - 已经冷却结束但没有新内容、用户没有新的发言意图
   - 内容明显应当由人类成员回应而非机器人插话

输出格式（严格 JSON，不要包裹 markdown 代码块、不要附加任何额外文本）：
{"respond": true|false, "reason": "简短理由，不超过 50 字"}
"""

#: 判定用户提示词模板。运行时把上下文填入 ``{context}`` 占位符。
_USER_PROMPT_TEMPLATE = """当前聊天流：{stream_name}
聊天类型：{chat_type}
机器人昵称：{bot_nickname}

# 最近历史消息（含已 flush 的发言摘要）
{history}

# 本轮未读消息
{unreads}

请根据以上内容判断：主聊天代理是否应当立即回复本轮未读消息？
严格按系统提示词约定的 JSON 格式输出。"""


class SubAgentDecisionHandler(BaseEventHandler):
    """sub_agent 轻量 LLM 判定处理器。

    在 ``neo_chatter:preprocess`` 事件链中位于 概率直通门之后（weight 较低）。
    若 概率直通门已经 ``STOP``，本处理器不会执行；若 概率直通门未命中放行，
    本处理器发起一次 LLM 单轮判定决定是否 proceed。

    所有判定逻辑（prompt 构造、LLM 请求、响应解析、回写决策字段）均自包含
    在本处理器内部，不依赖 sub_agent 协作管理器或持久化子代理会话。

    Class Attributes:
        weight: 50，低于 概率直通处理器（100），确保 概率直通处理器先执行。
        init_subscribe: 订阅 ``neo_chatter:preprocess`` 事件。
    """

    name = "sub_agent_decision"
    description = "sub_agent 轻量 LLM 判定处理器 - 单轮判定是否值得主 chatter 立即回复"
    weight = 50
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
            - 处理器禁用 / 无未读 / 上游已拦截 / LLM 异常 → ``(SUCCESS, params)``，
              ``proceed`` 保持原值（默认放行）。
        """
        cfg = self._get_sub_agent_config(params)
        if cfg is None or not bool(cfg.enabled):
            return EventDecision.SUCCESS, params

        # 上游处理器已拦截（proceed=False）则不再判定，保持拦截
        if _coerce_proceed(params.get("proceed")) is False:
            return EventDecision.SUCCESS, params

        unreads = self._extract_unreads(params)
        chat_stream = params.get("chat_stream")
        if not unreads or not isinstance(chat_stream, ChatStream):
            return EventDecision.SUCCESS, params

        try:
            response = await self._call_llm(chat_stream, unreads, params, cfg)
        except Exception as error:  # noqa: BLE001
            logger.warning(
                f"[SubAgent] LLM 判定失败，按放行处理: {error}",
                exc_info=True,
            )
            return EventDecision.SUCCESS, params

        if response is None:
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

        system_prompt = _SYSTEM_PROMPT
        user_prompt = self._build_user_prompt(chat_stream, unreads, params, cfg)

        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
        request.add_payload(LLMPayload(ROLE.USER, Text(user_prompt)))

        response = await request.send(stream=False)
        await response
        return response

    def _build_user_prompt(
        self,
        chat_stream: ChatStream,
        unreads: list[Message],
        params: dict[str, Any],
        cfg: "NeoChatterConfig.PluginSection.PreprocessSubAgentSection",
    ) -> str:
        """构造判定用户提示词。

        历史消息：直接取 ``params['history_text']`` 截断到 ``max_context_messages``
        行；未读消息按 ``max_unread_messages`` 截断后逐条格式化。
        """
        history_text = str(params.get("history_text") or "").strip()
        max_ctx = max(0, int(cfg.max_context_messages))
        if max_ctx > 0 and history_text:
            history_lines = history_text.splitlines()
            if len(history_lines) > max_ctx:
                history_lines = history_lines[-max_ctx:]
            history_block = "\n".join(history_lines)
        else:
            history_block = history_text or "（无）"

        max_unread = max(1, int(cfg.max_unread_messages))
        recent_unreads = unreads[-max_unread:]
        unread_lines = [self._format_message_line(msg) for msg in recent_unreads]
        unread_block = "\n".join(unread_lines) if unread_lines else "（无）"

        return _USER_PROMPT_TEMPLATE.format(
            stream_name=chat_stream.stream_name or chat_stream.stream_id or "未知",
            chat_type=str(chat_stream.chat_type or "unknown"),
            bot_nickname=chat_stream.bot_nickname or "机器人",
            history=history_block,
            unreads=unread_block,
        )

    @staticmethod
    def _format_message_line(msg: Message) -> str:
        """把单条 :class:`Message` 格式化为提示词里的一行。"""
        sender = msg.sender_name or msg.sender_id or "匿名"
        text = msg.processed_plain_text
        if not isinstance(text, str) or not text.strip():
            content = msg.content
            text = content if isinstance(content, str) else str(content)
        return f"[{sender}] {text.strip() or '（非文本内容）'}"

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


def _coerce_proceed(value: Any) -> bool:
    """容错解析 ``proceed`` 字段，缺省视为 True。"""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "off"}:
            return False
        return True
    return bool(value)


def _first_line(text: str) -> str:
    """取文本首行，去掉可能的 JSON / markdown 前缀。"""
    if not text:
        return ""
    return text.splitlines()[0].strip()


__all__ = ["SubAgentDecisionHandler"]
