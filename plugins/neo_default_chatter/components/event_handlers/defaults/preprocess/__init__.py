"""NDFC ``:preprocess`` 事件的三个默认策略处理器。

与 :mod:`components.event_handlers.defaults` 下 16 个 weight=0 的 Tier II 兜底
handler 不同，这三个是 NDFC 自带的具体预处理策略（weight>0），按 weight 顺序
串行执行：

- :class:`PrivateChatBypassHandler` (weight=2)：私聊直通门，私聊场景直接放行
  并 ``STOP`` 阻断后续处理器。
- :class:`ProbabilityBypassHandler` (weight=1)：概率直通门，命中放行概率即
  ``STOP`` 阻断后续处理器。
- :class:`SubAgentDecisionHandler` (weight=0)：概率直通未命中时发起一次轻量
  LLM 单轮判定。

第三方可用更高 weight 先执行来替换或协作；也可直接 ``STOP`` 完全旁路这三个。
"""

from .private_chat_bypass import PrivateChatBypassHandler
from .probability_bypass import ProbabilityBypassHandler
from .sub_agent_decision import SubAgentDecisionHandler

__all__ = [
    "PrivateChatBypassHandler",
    "ProbabilityBypassHandler",
    "SubAgentDecisionHandler",
]
