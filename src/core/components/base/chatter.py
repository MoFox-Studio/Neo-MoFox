"""聊天器组件基类。

本模块提供 BaseChatter 类，定义聊天器组件的基本行为。
Chatter 是 Bot 的智能核心，定义对话逻辑和流程。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, cast

from src.core.components.types import ChatType
from src.core.components.base.action import BaseAction
from src.core.components.base.collection import BaseCollection
from src.core.components.base.tool import BaseTool
from src.core.managers import (
    get_collection_manager,
    get_tool_use,
    get_action_manager,
    get_stream_manager,
    get_plugin_manager,
)
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.components.base.action import BaseAction
    from src.core.components.base.tool import BaseTool
    from src.core.components.base.collection import BaseCollection
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message
    from src.kernel.llm.payload.tooling import LLMUsable


@dataclass
class Wait:
    """等待结果。

    表示 Chatter 需要等待一段时间。

    Attributes:
        time: 等待时间（秒），如果为 None 则表示无限等待直到有新消息
    """

    time: float | int | None = None


@dataclass
class Success:
    """成功结果。

    表示 Chatter 成功完成执行。

    Attributes:
        message: 成功消息
        data: 可选的附加数据
    """

    message: str
    data: dict[str, Any] | None = None


@dataclass
class Failure:
    """失败结果。

    表示 Chatter 执行失败。

    Attributes:
        error: 错误消息
        exception: 可选的异常对象
    """

    error: str
    exception: Exception | None = None

@dataclass
class Stop:
    """停止结果。

    表示 Chatter 将在一段时间后重新开始对话。

    Attributes:
        time: 停止时间（秒）
    """

    time: float | int

# 类型别名
ChatterResult = Wait | Success | Failure | Stop


class BaseChatter(ABC):
    """聊天器组件基类。

    Chatter 定义 Bot 的对话逻辑和流程。
    使用生成器模式，通过 yield 返回 Wait/Success/Failure/Stop 结果。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        chatter_name: 聊天器名称
        chatter_description: 聊天器描述
        associated_platforms: 关联的平台列表
        chat_type: 支持的聊天类型

    Examples:
        >>> class MyChatter(BaseChatter):
        ...     chatter_name = "my_chatter"
        ...     chatter_description = "我的聊天器"
        ...
        ...     async def execute(self, unreads: list[Message]) -> AsyncGenerator[ChatterResult, None]:
        ...         yield Wait("等待 LLM 响应")
        ...         # 执行逻辑...
        ...         yield Success("完成")
    """

    _plugin_: str
    _signature_: str

    # 聊天器元数据
    chatter_name: str = ""
    chatter_description: str = ""

    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:service:memory"]

    def __init__(
        self,
        stream_id: str,
        plugin: "BasePlugin",
    ) -> None:
        """初始化聊天器组件。

        Args:
            stream_id: 聊天流 ID
            plugin: 所属插件实例
        """
        self.stream_id = stream_id
        self.plugin = plugin

    @classmethod
    def get_signature(cls) -> str | None:
        """获取聊天器组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:chatter:chatter_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = MyChatter.get_signature()
            >>> "my_plugin:chatter:my_chatter"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.chatter_name:
            return f"{cls._plugin_}:chatter:{cls.chatter_name}"
        return None

    @abstractmethod
    async def execute(
        self
    ) -> AsyncGenerator[ChatterResult, None]:
        """执行聊天器的主要逻辑。

        使用生成器模式，通过 yield 返回执行结果。

        Yields:
            ChatterResult: Wait/Success/Failure/Stop 结果

        Examples:
            >>> async for result in my_chatter.execute():
            ...     if isinstance(result, Wait):
            ...         print(f"等待: {result.reason}")
            ...     elif isinstance(result, Success):
            ...         print(f"成功: {result.message}")
            ...     elif isinstance(result, Failure):
            ...         print(f"失败: {result.error}")
            ...     elif isinstance(result, Stop):
            ...         print(f"停止: {result.time} 秒")
        """
        ...

    async def get_llm_usables(self) -> list[type["LLMUsable"]]:
        """获取可用的 LLMUsable 组件列表。

        从全局注册表中获取所有可用的 Action、Tool、Collection 组件。

        Returns:
            list[type[LLMUsable]]: LLMUsable 组件类列表

        Examples:
            >>> usables = await self.get_llm_usables()
            >>> [MyAction, MyTool, MyCollection]
        """
        from src.core.components.registry import get_global_registry
        from src.core.components.types import ComponentType, ComponentState
        from src.core.components.state_manager import get_global_state_manager

        usables: list[type["LLMUsable"]] = []

        state_manager = get_global_state_manager()
        collection_manager = get_collection_manager()
        registry = get_global_registry()

        # 从全局注册表按类型收集组件
        llm_usable_components: list[tuple[str, str, type]] = []

        for comp_type in (
            ComponentType.ACTION,
            ComponentType.TOOL,
            ComponentType.COLLECTION,
        ):
            components = registry.get_by_type(comp_type)
            for sig, component_cls in components.items():
                llm_usable_components.append((sig, comp_type.value, component_cls))

        for sig, comp_type, component_cls in llm_usable_components:
            # 仅返回“可用”的组件
            if state_manager.get_state(sig) != ComponentState.ACTIVE:
                continue

            # Collection 解包只影响当前聊天流：对 Action/Tool 做 stream 级门控过滤
            if comp_type in (ComponentType.ACTION.value, ComponentType.TOOL.value):
                if not collection_manager.is_component_available(sig, self.stream_id):
                    continue

            usables.append(component_cls)

        return usables

    async def modify_llm_usables(
        self, llm_usables: list[LLMUsable]
    ) -> list[type[LLMUsable]]:
        """修改 LLMUsable 组件列表。

        调用其go_activate方法进行激活判定，并核对associate_type，返回最终可用的组件列表。

        Args:
            llm_usables: 原始 LLMUsable 组件列表

        Returns:
            list[type[LLMUsable]]: 修改后的组件列表
        """

        logger = get_logger("chatter", display="聊天器", color=COLOR.MAGENTA)
        chat_stream = await get_stream_manager().get_or_create_stream(
            stream_id=self.stream_id
        )
        chat_context = chat_stream.context

        removals: list[tuple[str, str]] = []
        filtered: list[type[LLMUsable]] = []

        for usable_cls in llm_usables:
            usable_cls = cast(type["BaseAction|BaseTool|BaseCollection"], usable_cls)  # 类型提示
            signature = usable_cls.get_signature() or usable_cls.__name__

            if issubclass(usable_cls, BaseAction) and usable_cls.associated_types:
                if not chat_context.check_types(usable_cls.associated_types):
                    types_str = ", ".join(usable_cls.associated_types)
                    reason = f"适配器不支持（需要: {types_str}）"
                    removals.append((signature, reason))
                    logger.debug(f"[移除组件] {signature}：{reason}")
                    continue

            filtered.append(usable_cls)

        # 并行执行 go_activate（如果组件提供）
        tasks = []
        signatures = []
        for usable_cls in filtered:
            usable_cls = cast(type["BaseAction|BaseTool|BaseCollection"], usable_cls)  # 类型提示
            signature = usable_cls.get_signature() or usable_cls.__name__

            try:
                component_plugin = self._resolve_component_plugin(signature)
                instance: BaseAction | BaseTool | BaseCollection
                if issubclass(usable_cls, BaseAction):
                    instance = usable_cls(chat_stream=chat_stream, plugin=component_plugin)

                    current_msg = chat_context.current_message
                    if current_msg:
                        instance._last_message = (
                            current_msg.processed_plain_text
                            if current_msg.processed_plain_text
                            else str(current_msg.content or "")
                        )
                elif issubclass(usable_cls, BaseTool):
                    instance = usable_cls(plugin=component_plugin)
                elif issubclass(usable_cls, BaseCollection):
                    instance = usable_cls(plugin=component_plugin)
                else:
                    continue

                go_activate = getattr(instance, "go_activate", None)
                if not callable(go_activate):
                    continue

                tasks.append(go_activate())
                signatures.append(signature)

            except Exception as e:
                logger.error(f"创建 LLMUsable 实例 {signature} 失败: {e}")
                removals.append((signature, f"创建实例失败: {e}"))

        if tasks:
            logger.debug(
                f"[{chat_stream.stream_id}] 并行执行激活判断，任务数: {len(tasks)}"
            )
            try:
                results = await get_task_manager().gather(
                    *tasks, return_exceptions=True
                )

                for signature, result in zip(signatures, results, strict=False):
                    if isinstance(result, Exception):
                        logger.error(
                            f"[{chat_stream.stream_id}] 激活判断 {signature} 时出错: {result}"
                        )
                        removals.append((signature, f"激活判断出错: {result}"))
                    elif not result:
                        removals.append((signature, "go_activate 返回 False"))
                        logger.debug(
                            f"[{chat_stream.stream_id}] 未激活组件: {signature}"
                        )
                    else:
                        logger.debug(f"[{chat_stream.stream_id}] 激活组件: {signature}")

            except Exception as e:
                logger.error(f"[{chat_stream.stream_id}] 并行激活判断失败: {e}")
                removals.extend((sig, f"并行判断失败: {e}") for sig in signatures)

        if removals:
            removals_summary = " | ".join(
                [f"{name}({reason})" for name, reason in removals]
            )
            logger.info(f"[{chat_stream.stream_id}] 移除组件: {removals_summary}")

        removal_names = {name for name, _ in removals}
        available = [
            usable_cls
            for usable_cls in filtered
            if (usable_cls.get_signature() or usable_cls.__name__) not in removal_names # type: ignore
        ]

        logger.info(
            f"[{chat_stream.stream_id}] 可用组件: {len(available)}/{len(llm_usables)}"
        )

        return available

    def _resolve_component_plugin(self, signature: str | None) -> "BasePlugin":
        """根据组件签名解析其所属插件实例。"""
        if not signature:
            return self.plugin

        try:
            from src.core.components.types import parse_signature

            plugin_name = parse_signature(signature)["plugin_name"]
        except Exception:
            return self.plugin

        target_plugin = get_plugin_manager().get_plugin(plugin_name)
        if target_plugin:
            return target_plugin

        return self.plugin

    async def exec_llm_usable(
        self,
        usable_cls: type[LLMUsable],
        message: "Message",
        **kwargs: Any,
    ) -> tuple[bool, Any]:
        """执行指定的 LLMUsable 组件。

        Args:
            usable_cls: LLMUsable 组件类
            message: 触发的消息
            **kwargs: 传递给组件的参数

        Returns:
            tuple[bool, Any]: (是否成功, 返回结果)

        Examples:
            >>> success, result = await self.exec_llm_usable(
            ...     MyTool,
            ...     message,
            ...     param1="value1"
            ... )
        """

        usable_cls = cast(type["BaseAction|BaseTool|BaseCollection"], usable_cls)  # 类型提示
        sig = usable_cls.get_signature()
        if not sig:
            raise ValueError("LLMUsable 组件未注入插件名称，无法执行")

        if issubclass(usable_cls, BaseChatter):
            raise ValueError("无法直接执行 Chatter 组件")

        if issubclass(usable_cls, BaseTool):
            owner_plugin = self._resolve_component_plugin(sig)
            manager = get_tool_use()
            return await manager.execute_tool(sig, owner_plugin, message, **kwargs)
        elif issubclass(usable_cls, BaseAction):
            owner_plugin = self._resolve_component_plugin(sig)
            manager = get_action_manager()
            return await manager.execute_action(sig, owner_plugin, message, **kwargs)
        elif issubclass(usable_cls, BaseCollection):
            owner_plugin = self._resolve_component_plugin(sig)
            manager = get_collection_manager()
            await manager.unpack_collection(sig, self.stream_id, plugin=owner_plugin)
            return True, "Collection 已解包"
        else:
            raise ValueError("未知的 LLMUsable 组件类型，无法执行")

    async def fetch_unreads(
        self,
        format_as_group: bool = True,
        time_format: str = "%H:%M",
    ) -> tuple[str, list["Message"]]:
        """仅读取未读消息，不修改上下文。

        Args:
            format_as_group: 是否将未读消息格式化为一个 JSON 组
            time_format: 时间格式化字符串（用于 time 字段）

        Returns:
            tuple[str, list[Message]]: (格式化后的未读消息 JSON 文本, 未读消息列表)
        """
        logger = get_logger("chatter")

        sm = get_stream_manager()
        chat_stream = sm._streams.get(self.stream_id)

        if not chat_stream:
            logger.warning(
                f"[{self.chatter_name}] 无法获取聊天流: {self.stream_id[:8]}"
            )
            return "", []

        context = chat_stream.context
        unread_messages = list(context.unread_messages)

        if not unread_messages:
            return "", []

        if not format_as_group:
            return "", unread_messages

        formatted_messages = []
        for msg in unread_messages:
            if isinstance(msg.time, (int, float)):
                time_str = datetime.fromtimestamp(msg.time).strftime(time_format)
            elif isinstance(msg.time, datetime):
                time_str = msg.time.strftime(time_format)
            else:
                time_str = str(msg.time)

            message_type_value = (
                msg.message_type.value
                if hasattr(msg.message_type, "value")
                else str(msg.message_type)
            )
            formatted_messages.append(
                {
                    "message_id": msg.message_id,
                    "time": time_str,
                    "reply_to": msg.reply_to,
                    "message_type": message_type_value,
                    "processed_plain_text": msg.processed_plain_text,
                    "sender_id": msg.sender_id,
                    "sender_name": msg.sender_name,
                    "sender_cardname": msg.sender_cardname,
                }
            )

        return json.dumps(formatted_messages, ensure_ascii=False), unread_messages

    async def flush_unreads(self, unread_messages: list["Message"]) -> int:
        """将指定未读消息从 unread 移入 history。

        仅搬运传入的消息，避免将“读取时刻之后新增”的未读消息一并清空。

        Args:
            unread_messages: 待 flush 的未读消息快照

        Returns:
            int: 实际 flush 的消息数量
        """
        logger = get_logger("chatter")

        if not unread_messages:
            return 0

        sm = get_stream_manager()
        chat_stream = sm._streams.get(self.stream_id)

        if not chat_stream:
            logger.warning(
                f"[{self.chatter_name}] 无法获取聊天流: {self.stream_id[:8]}"
            )
            return 0

        context = chat_stream.context
        pending_by_id: dict[str, Message] = {
            msg.message_id: msg
            for msg in unread_messages
            if msg.message_id
        }

        flushed_count = 0
        remained_unreads: list[Message] = []
        for msg in context.unread_messages:
            msg_id = msg.message_id
            if msg_id and msg_id in pending_by_id:
                context.add_history_message(msg)
                flushed_count += 1
            else:
                remained_unreads.append(msg)

        context.unread_messages = remained_unreads

        logger.debug(
            f"[{self.chatter_name}] flush 未读消息 {flushed_count} 条"
        )

        return flushed_count
