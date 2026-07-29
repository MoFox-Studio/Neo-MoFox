"""插件系统 API 聚合模块。

本模块聚合导出全部 ``*_api`` 子模块，并提供 ``PLUGIN_API_VERSIONS`` 字典，
作为「插件 API 分模块版本号」的唯一事实源。加载器据此对插件 manifest 中声明的
``api_version`` 字段（兼容字符串与 dict 形式）逐一校验。
"""

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
from src.app.plugin_system.api import person_api
from src.app.plugin_system.api import plugin_api
from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.api import router_api
from src.app.plugin_system.api import send_api
from src.app.plugin_system.api import service_api
from src.app.plugin_system.api import storage_api
from src.app.plugin_system.api import stream_api

#: 各 ``*_api`` 模块的 API 版本号聚合表。
#:
#: - key: 模块名（与 Python import 路径完全对应，零歧义）
#: - value: 该模块顶部声明的 ``API_VERSION``
#:
#: 该字典的 key 集合即「合法 API 名称全集」，校验时以此为准。
#: 任何不在此清单中的 ``api_version`` dict key 都会被加载器拒绝。
PLUGIN_API_VERSIONS: dict[str, str] = {
    "action_api": action_api.API_VERSION,
    "adapter_api": adapter_api.API_VERSION,
    "agent_api": agent_api.API_VERSION,
    "chat_api": chat_api.API_VERSION,
    "command_api": command_api.API_VERSION,
    "config_api": config_api.API_VERSION,
    "database_api": database_api.API_VERSION,
    "event_api": event_api.API_VERSION,
    "llm_api": llm_api.API_VERSION,
    "log_api": log_api.API_VERSION,
    "media_api": media_api.API_VERSION,
    "message_api": message_api.API_VERSION,
    "permission_api": permission_api.API_VERSION,
    "person_api": person_api.API_VERSION,
    "plugin_api": plugin_api.API_VERSION,
    "prompt_api": prompt_api.API_VERSION,
    "router_api": router_api.API_VERSION,
    "send_api": send_api.API_VERSION,
    "service_api": service_api.API_VERSION,
    "storage_api": storage_api.API_VERSION,
    "stream_api": stream_api.API_VERSION,
}

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
    "person_api",
    "plugin_api",
    "prompt_api",
    "router_api",
    "send_api",
    "service_api",
    "storage_api",
    "stream_api",
    "PLUGIN_API_VERSIONS",
]
