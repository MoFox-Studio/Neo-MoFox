"""插件系统公共类型模块测试。"""

from __future__ import annotations

from src.app.plugin_system import types
from src.app.plugin_system.api import llm_api
from src.core.components.types import EventType
from src.core.models import ChatStream, Message, MessageType, StreamContext
from src.core.prompt import PromptTemplate, SystemReminderBucket
from src.kernel.llm import ROLE, Text


def test_types_module_reexports_prompt_and_llm_types() -> None:
    """types 模块应导出插件作者常用的 prompt 与 LLM 类型。"""
    assert types.PromptTemplate is PromptTemplate
    assert types.SystemReminderBucket is SystemReminderBucket
    assert types.Text is Text
    assert types.ROLE is ROLE


def test_types_module_reexports_component_types() -> None:
    """types 模块应导出组件层常用枚举。"""
    assert types.EventType is EventType
    assert types.EventType.ON_START.value == "on_start"


def test_types_module_reexports_message_and_stream_models() -> None:
    """types 模块应导出插件作者常用的消息与流模型。"""
    assert types.Message is Message
    assert types.MessageType is MessageType
    assert types.ChatStream is ChatStream
    assert types.StreamContext is StreamContext
    assert types.MessageType.TEXT.value == "text"


def test_llm_api_task_type_uses_public_types_module() -> None:
    """llm_api 中的 TaskType 应与公共类型层保持同一对象。"""
    assert llm_api.TaskType is types.TaskType
    assert types.TaskType.ACTOR.value == "actor"