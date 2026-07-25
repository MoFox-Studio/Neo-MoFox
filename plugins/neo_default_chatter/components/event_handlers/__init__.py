"""Neo-Default-Chatter 消息预处理事件处理器集合。

本包内的处理器按事件订阅串行执行：

- :class:`ProbabilityBypassHandler` (weight=100)：移植自 default_chatter 的概率门，
  命中放行概率即 ``STOP`` 阻断后续处理器，让主 chatter 直接处理本轮消息。
- :class:`SubAgentDecisionHandler` (weight=50)：概率直通未命中时发起一次轻量 LLM
  单轮判定，决定是否值得主 chatter 立即回复。
- 16 个 Tier II 默认 handler (weight=0)：NDFC 自带的可替换 seam 兜底实现，
  位于 :mod:`.defaults` 子包；第三方用更高 weight 即可替换或协作。

所有处理逻辑均自包含在处理器内部，不依赖额外的工具模块。
"""

from .defaults import (
    BuildHistoryTextDefaultHandler,
    BuildNegativeExtraDefaultHandler,
    BuildResumePromptDefaultHandler,
    ComputeCooldownDefaultHandler,
    ComputeStopWakeDefaultHandler,
    CreateRequestDefaultHandler,
    DedupeToolCallDefaultHandler,
    FetchUnreadsDefaultHandler,
    FlushUnreadsDefaultHandler,
    FormatToolResultDefaultHandler,
    FormatUnreadLineDefaultHandler,
    InjectUnreadPayloadDefaultHandler,
    InjectUsablesDefaultHandler,
    PickTriggerMessageDefaultHandler,
    RunToolCallDefaultHandler,
    SessionTransitionDefaultHandler,
)
from .probability_bypass import ProbabilityBypassHandler
from .sub_agent_decision import SubAgentDecisionHandler

__all__ = [
    "ProbabilityBypassHandler",
    "SubAgentDecisionHandler",
    "BuildHistoryTextDefaultHandler",
    "BuildNegativeExtraDefaultHandler",
    "BuildResumePromptDefaultHandler",
    "ComputeCooldownDefaultHandler",
    "ComputeStopWakeDefaultHandler",
    "CreateRequestDefaultHandler",
    "DedupeToolCallDefaultHandler",
    "FetchUnreadsDefaultHandler",
    "FlushUnreadsDefaultHandler",
    "FormatToolResultDefaultHandler",
    "FormatUnreadLineDefaultHandler",
    "InjectUnreadPayloadDefaultHandler",
    "InjectUsablesDefaultHandler",
    "PickTriggerMessageDefaultHandler",
    "RunToolCallDefaultHandler",
    "SessionTransitionDefaultHandler",
]
