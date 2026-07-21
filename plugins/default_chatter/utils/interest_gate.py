"""Default Chatter 兴趣值决策门。

封装三种过滤模式的决策逻辑：
- sub_only: 程序化概率门 + LLM sub-agent 决策
- interest_only: 纯兴趣值判断
- interest_then_sub: 先兴趣值初筛，达标后进入 sub-agent 判断
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatStream, Message

from ..components.config import DefaultChatterConfig
from .decision_agent import decide_should_respond
from .interest_calculator import InterestCalculator, InterestConfig, InterestResult
from .prompts import sub_agent_system_prompt
from .probability_gate import should_bypass_via_probability
from ..type_defs import FilterMode, SubAgentDecision, SupportsRequestCreation

logger = get_logger("default_chatter")


class InterestGate:
    """根据配置执行概率、兴趣值和 LLM 响应决策。"""

    def __init__(
        self,
        plugin: Any,
        request_runtime: SupportsRequestCreation,
    ) -> None:
        """初始化响应决策组件。

        Args:
            plugin: 插件实例
            request_runtime: LLM 请求创建接口
        """
        self._plugin = plugin
        self._request_runtime = request_runtime
        self._interest_calculator_instance: InterestCalculator | None = None

    async def decide(
        self,
        unreads_text: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
        history_text: str = "",
        decision_history: list[SubAgentDecision] | None = None,
    ) -> SubAgentDecision:
        """判断当前未读消息是否需要响应。

        支持三种过滤模式：
        - sub_only: 程序化概率门和 LLM 决策
        - interest_only: 兴趣值判断
        - interest_then_sub: 兴趣值初筛后执行 LLM 决策

        Args:
            unreads_text: 格式化后的未读消息文本
            unread_msgs: 未读消息对象列表
            chat_stream: 当前会话流，用于读取历史消息
            history_text: 历史消息摘要文本（供 sub-agent 上下文感知）
            decision_history: 之前的决策记录列表

        Returns:
            决策结果 dict
        """
        if str(chat_stream.chat_type).lower() == "private":
            return {
                "reason": "私聊场景跳过 sub-agent，直接响应",
                "should_respond": True,
            }

        if getattr(chat_stream.context, "_interest_reply_sent", False):
            setattr(chat_stream.context, "_interest_reply_sent", False)
            calculator = await self._get_interest_calculator()
            if calculator is not None:
                calculator.on_message_processed(chat_stream.stream_id, replied=True)
                logger.debug("[兴趣值] 检测到上次回复成功，已重置 boost 状态")

        filter_mode = self._get_filter_mode()
        decision: SubAgentDecision

        plugin_config = self._get_plugin_config()
        if (
            plugin_config is not None
            and plugin_config.plugin.enable_programmatic_controller
        ):
            bypassed, bypass_reason = should_bypass_via_probability(
                unread_msgs,
                chat_stream,
                plugin_config,
            )
            if bypassed:
                return {
                    "reason": f"概率直通响应：{bypass_reason}",
                    "should_respond": True,
                    "source": "probability",
                }

        if filter_mode == FilterMode.INTEREST_ONLY:
            decision = await self._interest_only_decision(unread_msgs, chat_stream)
        elif filter_mode == FilterMode.INTEREST_THEN_SUB:
            interest_result, stats = await self._interest_filter(unread_msgs, chat_stream)
            if not interest_result:
                decision = {
                    "reason": f"兴趣值未达标，{stats}",
                    "should_respond": False,
                    "source": "interest",
                }
            else:
                logger.info(f"[cyan]兴趣值过滤[/]: 兴趣值达标，{stats} → 进入子代理决策")
                decision = await self._sub_agent_with_context(
                    unreads_text, chat_stream, history_text, decision_history
                )
        else:
            decision = await self._sub_agent_with_context(
                unreads_text, chat_stream, history_text, decision_history
            )

        if not decision.get("should_respond", False):
            calculator = await self._get_interest_calculator()
            if calculator is not None:
                calculator.on_message_processed(
                    chat_stream.stream_id,
                    replied=False,
                )

        return decision

    def _get_plugin_config(self) -> DefaultChatterConfig | None:
        """获取插件配置，类型不匹配时返回 None。"""
        plugin_config = getattr(self._plugin, "config", None)
        if isinstance(plugin_config, DefaultChatterConfig):
            return plugin_config
        return None

    def _get_filter_mode(self) -> FilterMode:
        """读取当前过滤模式配置。"""
        plugin_config = self._get_plugin_config()
        if plugin_config is None:
            return FilterMode.SUB_ONLY
        mode_str = str(getattr(plugin_config.plugin, "filter_mode", "sub_only") or "sub_only")
        try:
            return FilterMode(mode_str)
        except ValueError:
            return FilterMode.SUB_ONLY

    def _is_sub_agent_context_enabled(self) -> bool:
        """读取 sub-agent 上下文感知开关。"""
        plugin_config = self._get_plugin_config()
        if plugin_config is None:
            return True
        return bool(getattr(plugin_config.plugin, "enable_sub_agent_context", True))

    async def _interest_only_decision(
        self,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
    ) -> SubAgentDecision:
        """兴趣值模式：纯兴趣值判断。

        Args:
            unread_msgs: 未读消息列表
            chat_stream: 当前会话流

        Returns:
            决策结果
        """
        interest_result, stats = await self._interest_filter(unread_msgs, chat_stream)
        if interest_result:
            return {
                "reason": f"达标({stats})",
                "should_respond": True,
                "source": "interest",
            }
        return {
            "reason": f"兴趣值未达标，{stats}",
            "should_respond": False,
            "source": "interest",
        }

    async def _interest_filter(
        self,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
    ) -> tuple[InterestResult | None, str]:
        """兴趣值初筛：检查是否有消息达到回复阈值。

        Args:
            unread_msgs: 未读消息列表
            chat_stream: 当前会话流

        Returns:
            (达标的 InterestResult 或 None, 批次统计摘要字符串)
        """
        calculator = await self._get_interest_calculator()
        if calculator is None:
            logger.debug("[兴趣值] 计算器未初始化，跳过兴趣值筛选")
            return None, ""

        thresholds = calculator.get_adjusted_thresholds(chat_stream.stream_id)

        max_result: InterestResult | None = None
        all_results: list[InterestResult] = []
        for msg in unread_msgs:
            try:
                result = await calculator.calculate(
                    msg, chat_stream, precomputed_thresholds=thresholds
                )
                logger.debug(
                    f"[兴趣值] 消息 {msg.message_id}: {result.interest_value:.3f} "
                    f"(语义={result.semantic_score:.3f} 提及={result.mentioned_score:.3f} "
                    f"阈值={result.should_reply})"
                )
                all_results.append(result)
                if max_result is None or result.interest_value > max_result.interest_value:
                    max_result = result
                if result.should_reply:
                    stats = self._build_interest_stats(
                        all_results, max_result, thresholds[0]
                    )
                    return result, stats
            except Exception as e:
                logger.warning(f"[兴趣值] 计算异常: {e}")

        stats = self._build_interest_stats(
            all_results, max_result, thresholds[0]
        )

        if max_result is not None:
            logger.debug(f"[cyan]兴趣值批次汇总[/] {stats}")

        return None, stats

    @staticmethod
    def _build_interest_stats(
        results: list[InterestResult],
        max_result: InterestResult | None,
        reply_threshold: float,
    ) -> str:
        """构建兴趣值批次统计摘要字符串。

        Args:
            results: 本批所有计算结果
            max_result: 最高兴趣值结果
            reply_threshold: 回复阈值

        Returns:
            统计摘要字符串
        """
        if not results:
            return "无有效消息"

        avg_value = sum(r.interest_value for r in results) / len(results)

        if max_result is not None:
            return (
                f"{len(results)}条 "
                f"max={max_result.interest_value:.2f}"
                f"(语义={max_result.semantic_score:.2f} 提及={max_result.mentioned_score:.2f}) "
                f"avg={avg_value:.2f} "
                f"阈值={reply_threshold:.2f}"
            )

        return f"{len(results)}条 avg={avg_value:.2f} 阈值={reply_threshold:.2f}"

    async def _get_interest_calculator(self) -> InterestCalculator | None:
        """获取兴趣值计算器实例（惰性初始化，异步加载语义评分器）。"""
        cached = self._interest_calculator_instance
        if cached is not None:
            if cached.has_semantic_scorer:
                return cached
            model_path = Path(
                "data/semantic_interest/models/semantic_interest_latest.pkl"
            )
            if not model_path.exists():
                return cached
            logger.info("[兴趣值] 检测到模型文件已生成，重新加载语义评分器")

        plugin_config = self._get_plugin_config()
        if plugin_config is None:
            self._interest_calculator_instance = None
            return None

        interest_cfg = plugin_config.plugin.interest
        config = InterestConfig(
            reply_threshold=interest_cfg.reply_threshold,
            action_threshold=interest_cfg.action_threshold,
            semantic_weight=interest_cfg.semantic_weight,
            mentioned_weight=interest_cfg.mentioned_weight,
            strong_mention_score=interest_cfg.strong_mention_score,
            weak_mention_score=interest_cfg.weak_mention_score,
            no_reply_threshold_adjustment=interest_cfg.no_reply_threshold_adjustment,
            max_no_reply_count=interest_cfg.max_no_reply_count,
            reply_cooldown_reduction=interest_cfg.reply_cooldown_reduction,
            enable_post_reply_boost=interest_cfg.enable_post_reply_boost,
            post_reply_threshold_reduction=interest_cfg.post_reply_threshold_reduction,
            post_reply_boost_max_count=interest_cfg.post_reply_boost_max_count,
            post_reply_boost_decay_rate=interest_cfg.post_reply_boost_decay_rate,
        )

        semantic_scorer = await self._try_load_semantic_scorer()

        self._interest_calculator_instance = InterestCalculator(
            config=config,
            semantic_scorer=semantic_scorer,
        )
        return self._interest_calculator_instance

    async def _try_load_semantic_scorer(self) -> Any | None:
        """异步加载语义兴趣度评分器。

        当模型文件存在时通过全局单例异步加载；不存在时返回 None，
        由插件级后台训练任务负责生成模型。
        """
        try:
            from ..semantic_interest.runtime_scorer import get_semantic_scorer

            model_dir = Path("data/semantic_interest/models")
            latest = model_dir / "semantic_interest_latest.pkl"
            if latest.exists():
                scorer = await get_semantic_scorer(latest)
                logger.info("[语义评分] 模型异步加载完成")
                return scorer
            logger.info("[语义评分] 未找到模型文件，语义评分将返回 0.0")
            return None
        except Exception as e:
            logger.warning(f"[语义评分] 加载失败: {e}")
            return None

    async def _sub_agent_with_context(
        self,
        unreads_text: str,
        chat_stream: ChatStream,
        history_text: str = "",
        decision_history: list[SubAgentDecision] | None = None,
    ) -> SubAgentDecision:
        """执行带上下文的 LLM sub-agent 决策。

        Args:
            unreads_text: 未读消息文本
            chat_stream: 当前会话流
            history_text: 历史消息摘要
            decision_history: 决策历史

        Returns:
            决策结果
        """
        enable_context = self._is_sub_agent_context_enabled()

        effective_history = history_text if enable_context else ""
        effective_decisions = decision_history if enable_context else None

        return await decide_should_respond(
            chatter=self._request_runtime,
            logger=logger,
            unreads_text=unreads_text,
            chat_stream=chat_stream,
            fallback_prompt=sub_agent_system_prompt,
            history_text=effective_history,
            decision_history=effective_decisions,
        )
