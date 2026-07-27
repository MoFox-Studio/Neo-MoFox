"""Neo-Default-Chatter 默认动作集合。"""

from .control import PassAndWaitAction, StopConversationAction
from .send_text import SendTextAction

__all__ = [
    "PassAndWaitAction",
    "SendTextAction",
    "StopConversationAction",
]
