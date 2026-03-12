"""插件系统 API 聚合模块。"""

from src.app.plugin_system.api import action_api
from src.app.plugin_system.api import adapter_api
from src.app.plugin_system.api import agent_api
from src.app.plugin_system.api import chat_api
from src.app.plugin_system.api import command_api
from src.app.plugin_system.api import config_api
from src.app.plugin_system.api import database_api
from src.app.plugin_system.api import event_api
from src.app.plugin_system.api import llm_api
from src.app.plugin_system.api import log_api
from src.app.plugin_system.api import media_api
from src.app.plugin_system.api import message_api
from src.app.plugin_system.api import permission_api
from src.app.plugin_system.api import plugin_api
from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.api import router_api
from src.app.plugin_system.api import send_api
from src.app.plugin_system.api import service_api
from src.app.plugin_system.api import storage_api
from src.app.plugin_system.api import stream_api

__all__ = [
    "action_api",
    "adapter_api",
    "agent_api",
    "chat_api",
    "command_api",
    "config_api",
    "database_api",
    "event_api",
    "llm_api",
    "log_api",
    "media_api",
    "message_api",
    "permission_api",
    "plugin_api",
    "prompt_api",
    "router_api",
    "send_api",
    "service_api",
    "storage_api",
    "stream_api",
]
