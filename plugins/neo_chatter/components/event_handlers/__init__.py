"""Neo-Chatter 消息预处理事件处理器集合。

本包内的处理器都订阅 ``neo_chatter:preprocess`` 事件，按权重串行执行：

- :class:`ProbabilityBypassHandler` (weight=100)：移植自 default_chatter 的概率门，
  命中放行概率即 ``STOP`` 阻断后续处理器，让主 chatter 直接处理本轮消息。
- :class:`SubAgentDecisionHandler` (weight=50)：概率直通未命中时发起一次轻量 LLM
  单轮判定，决定是否值得主 chatter 立即回复。

所有处理逻辑均自包含在处理器内部，不依赖额外的工具模块。
"""

from .probability_bypass import ProbabilityBypassHandler
from .sub_agent_decision import SubAgentDecisionHandler

__all__ = ["ProbabilityBypassHandler", "SubAgentDecisionHandler"]
