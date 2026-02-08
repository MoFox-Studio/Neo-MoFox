"""聊天器组件基类。

本模块提供 BaseChatter 类，定义聊天器组件的基本行为。
Chatter 是 Bot 的智能核心，定义对话逻辑和流程。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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
        chatter_allow: 支持的 Chatter 列表（用于多 Chatter 场景）
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
    chatter_allow: list[str] = []
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

        从插件中获取所有可用的 Action、Tool、Collection 组件。

        Returns:
            list[type[LLMUsable]]: LLMUsable 组件类列表

        Examples:
            >>> usables = await self.get_llm_usables()
            >>> [MyAction, MyTool, MyCollection]
        """
        from src.core.components.types import ComponentType, ComponentState
        from src.core.components.state_manager import get_global_state_manager

        usables: list[type["LLMUsable"]] = []

        state_manager = get_global_state_manager()
        collection_manager = get_collection_manager()

        # 获取所有组件
        components = self.plugin.get_components()

        for component_cls in components:
            # 检查是否是 LLMUsable（Action、Tool、Collection）
            sig = getattr(component_cls, "_signature_", None)
            if sig:
                # 仅返回“可用”的组件
                if state_manager.get_state(sig) != ComponentState.ACTIVE:
                    continue
                sig_parts = sig.split(":")
                if len(sig_parts) == 3:
                    comp_type = sig_parts[1]
                    if comp_type in (
                        ComponentType.ACTION.value,
                        ComponentType.TOOL.value,
                        ComponentType.COLLECTION.value,
                    ):
                        # Collection 解包只影响当前聊天流：对 Action/Tool 做 stream 级门控过滤
                        if comp_type in (
                            ComponentType.ACTION.value,
                            ComponentType.TOOL.value,
                        ):
                            if not collection_manager.is_component_available(
                                sig, self.stream_id
                            ):
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
                instance: BaseAction | BaseTool | BaseCollection
                if issubclass(usable_cls, BaseAction):
                    instance = usable_cls(chat_stream=chat_stream, plugin=self.plugin)

                    current_msg = chat_context.current_message
                    if current_msg:
                        instance._last_message = (
                            current_msg.processed_plain_text
                            if current_msg.processed_plain_text
                            else str(current_msg.content or "")
                        )
                elif issubclass(usable_cls, BaseTool):
                    instance = usable_cls(plugin=self.plugin)
                elif issubclass(usable_cls, BaseCollection):
                    instance = usable_cls(plugin=self.plugin)
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
            manager = get_tool_use()
            return await manager.execute_tool(sig, self.plugin, message, **kwargs)
        elif issubclass(usable_cls, BaseAction):
            manager = get_action_manager()
            return await manager.execute_action(sig, self.plugin, message, **kwargs)
        elif issubclass(usable_cls, BaseCollection):
            manager = get_collection_manager()
            await manager.unpack_collection(sig, self.stream_id, plugin=self.plugin)
            return True, "Collection 已解包"
        else:
            raise ValueError("未知的 LLMUsable 组件类型，无法执行")

    async def fetch_and_flush_unreads(
        self,
        format_as_group: bool = True,
        time_format: str = "%H:%M",
    ) -> tuple[str, list["Message"]]:
        """获取并刷新未读消息。

        从聊天流中获取所有未读消息，按格式组装，并flush到历史消息中。

        Args:
            format_as_group: 是否将未读消息格式化为一个组
            time_format: 时间格式化字符串（默认只显示时分）

        Returns:
            tuple[str, list[Message]]: (格式化后的未读消息文本, 未读消息列表)

        Examples:
            >>> # 格式化为组
            >>> text, messages = await chatter.fetch_and_flush_unreads()
            >>> print(text)
            "【14:30】Alice: 你好\\n【14:31】Bob: 在吗？"
            >>>
            >>> # 不分组，返回原始消息列表
            >>> text, messages = await chatter.fetch_and_flush_unreads(format_as_group=False)
        """
        from datetime import datetime

        logger = get_logger("chatter")

        sm = get_stream_manager()
        chat_stream = sm._streams.get(self.stream_id)

        if not chat_stream:
            logger.warning(
                f"[{self.chatter_name}] 无法获取聊天流: {self.stream_id[:8]}"
            )
            return "", []

        context = chat_stream.context
        unread_messages = list(context.unread_messages)  # Copy the list

        if not unread_messages:
            return "", []

        if format_as_group:
            # 格式化为组
            formatted_lines = []
            for msg in unread_messages:
                # 格式化时间
                if isinstance(msg.time, (int, float)):
                    time_str = datetime.fromtimestamp(msg.time).strftime(time_format)
                else:
                    time_str = str(msg.time)

                # 格式化发送人
                sender_name = msg.sender_name or msg.sender_id or "未知用户"

                # 格式化内容
                content = str(msg.processed_plain_text) if msg.processed_plain_text else ""

                # 组装行
                line = f"【{time_str}】{sender_name}: {content}"
                formatted_lines.append(line)

            formatted_text = "\n".join(formatted_lines)
        else:
            formatted_text = ""

        # Flush to history
        for msg in unread_messages:
            context.add_history_message(msg)

        # Clear unread messages
        context.unread_messages.clear()

        logger.debug(
            f"[{self.chatter_name}] 获取并flush了 {len(unread_messages)} 条未读消息"
        )

        return formatted_text, unread_messages
