"""NDFC 15 个 Tier II 默认 EventHandler。

每个文件一个类，名字与事件名同名（去掉 ``neo_default_chatter:`` 前缀，驼峰式）。
所有 handler ``weight=0``，保证第三方用更高 weight 先执行。

- 需要委托 ``BaseChatter`` 实例方法的（6 个）通过 :mod:`._runtime_helper` 拿到
  共享缓存的 ``NeoChatter`` 实例。
- 不需要 ``_runtime`` 的（9 个）直接调 ``utils`` 里的纯函数 / ``NeoChatterPromptBuilder``。

详见 ``docs/ndfc-event-hooks.md`` §5。
"""

from .build_history_text import BuildHistoryTextDefaultHandler
from .build_negative_extra import BuildNegativeExtraDefaultHandler
from .build_resume_prompt import BuildResumePromptDefaultHandler
from .compute_cooldown import ComputeCooldownDefaultHandler
from .compute_stop_wake import ComputeStopWakeDefaultHandler
from .create_request import CreateRequestDefaultHandler
from .dedupe_tool_call import DedupeToolCallDefaultHandler
from .fetch_unreads import FetchUnreadsDefaultHandler
from .flush_unreads import FlushUnreadsDefaultHandler
from .format_tool_result import FormatToolResultDefaultHandler
from .format_unread_line import FormatUnreadLineDefaultHandler
from .inject_unread_payload import InjectUnreadPayloadDefaultHandler
from .inject_usables import InjectUsablesDefaultHandler
from .pick_trigger_message import PickTriggerMessageDefaultHandler
from .run_tool_call import RunToolCallDefaultHandler
from .session_transition import SessionTransitionDefaultHandler

__all__ = [
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
