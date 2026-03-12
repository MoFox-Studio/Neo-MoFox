"""聊天器组件基类。

本模块提供 BaseChatter 类，定义聊天器组件的基本行为。
Chatter 是 Bot 的智能核心，定义对话逻辑和流程。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, cast

from src.core.components.types import ChatType
from src.core.components.base.action import BaseAction
from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool
from src.core.managers import (
    get_tool_use,
    get_action_manager,
    get_stream_manager,
    get_plugin_manager,
)
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.prompt import SystemReminderBucket
    from src.core.components.base.action import BaseAction
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.tool import BaseTool
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message
    from src.kernel.llm import LLMRequest
    from src.kernel.llm.payload.tooling import LLMUsable, ToolRegistry


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

        从全局注册表中获取所有可用的 Action、Tool 组件。

        Returns:
            list[type[LLMUsable]]: LLMUsable 组件类列表

        Examples:
            >>> usables = await self.get_llm_usables()
            >>> [MyAction, MyTool]
        """
        from src.core.components.registry import get_global_registry
        from src.core.components.types import ComponentType, ComponentState
        from src.core.components.state_manager import get_global_state_manager

        usables: list[type["LLMUsable"]] = []

        state_manager = get_global_state_manager()
        registry = get_global_registry()

        # 从全局注册表按类型收集组件
        llm_usable_components: list[tuple[str, str, type]] = []

        for comp_type in (
            ComponentType.ACTION,
            ComponentType.AGENT,
            ComponentType.TOOL,
        ):
            components = registry.get_by_type(comp_type)
            for sig, component_cls in components.items():
                llm_usable_components.append((sig, comp_type.value, component_cls))

        for sig, comp_type, component_cls in llm_usable_components:
            # 仅返回“可用”的组件
            if state_manager.get_state(sig) != ComponentState.ACTIVE:
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
            usable_cls = cast(type["BaseAction|BaseAgent|BaseTool"], usable_cls)  # 类型提示
            signature = usable_cls.get_signature() or usable_cls.__name__

            chatter_allow = getattr(usable_cls, "chatter_allow", [])
            if chatter_allow:
                chatter_signature = self.get_signature()
                allowed = self.chatter_name in chatter_allow
                if chatter_signature and not allowed:
                    allowed = chatter_signature in chatter_allow

                if not allowed:
                    allow_str = ", ".join(chatter_allow)
                    reason = f"chatter 不匹配（允许: {allow_str}）"
                    removals.append((signature, reason))
                    logger.debug(f"[移除组件] {signature}：{reason}")
                    continue

            if (
                (issubclass(usable_cls, BaseAction) or issubclass(usable_cls, BaseAgent))
                and usable_cls.associated_types
            ):
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
            usable_cls = cast(type["BaseAction|BaseAgent|BaseTool"], usable_cls)  # 类型提示
            signature = usable_cls.get_signature() or usable_cls.__name__

            try:
                component_plugin = self._resolve_component_plugin(signature)
                instance: BaseAction | BaseAgent | BaseTool
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
                elif issubclass(usable_cls, BaseAgent):
                    instance = usable_cls(stream_id=self.stream_id, plugin=component_plugin)
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

        usable_cls = cast(type["BaseAction|BaseAgent|BaseTool"], usable_cls)  # 类型提示
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
        elif issubclass(usable_cls, BaseAgent):
            owner_plugin = self._resolve_component_plugin(sig)
            kwargs.pop("reason", None)
            agent_instance = usable_cls(stream_id=self.stream_id, plugin=owner_plugin)
            return await agent_instance.execute(**kwargs)
        else:
            raise ValueError("未知的 LLMUsable 组件类型，无法执行")

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        max_context: int | None = None,
        with_reminder: str | SystemReminderBucket | None = None,
    ) -> "LLMRequest":
        """快速创建 LLM 请求，自动加载任务模型集与上下文管理器。

        封装了「获取模型集 → 创建上下文管理器 → 创建 LLMRequest」的固定样板。
        request_name 默认取 chatter_name。

        Args:
            task: 模型任务名称（对应 config/model.toml 中的 task key），默认 "actor"
            request_name: LLM 请求名称，默认使用 chatter_name
            max_context: 上下文最大 payload 数，None 时从 core config 读取
            with_reminder: 可选的 system reminder bucket；传入后会自动登记到上下文管理器

        Returns:
            LLMRequest: 配置好上下文管理器的 LLM 请求对象

        Raises:
            KeyError: 当 task 在模型配置中不存在时
        """
        from src.core.config import get_model_config, get_core_config
        from src.kernel.llm import LLMRequest, LLMContextManager

        model_set = get_model_config().get_task(task)
        max_payloads = max_context if max_context is not None else get_core_config().chat.max_context_size
        context_manager = LLMContextManager(
            max_payloads=max_payloads,
        )

        _logger = get_logger("chatter")
        if model_set:
            first = model_set[0]
            _logger.debug(
                f"provider={first.get('api_provider')}, "
                f"base_url={first.get('base_url')}, "
                f"timeout={first.get('timeout')}"
            )

        request = LLMRequest(
            model_set=model_set,
            request_name=request_name or self.chatter_name,
            context_manager=context_manager,
        )

        if with_reminder is not None:
            from src.core.prompt import get_system_reminder_store

            reminder_text = get_system_reminder_store().get(with_reminder)
            if reminder_text:
                context_manager.reminder(
                    reminder_text,
                    wrap_with_system_tag=True,
                )

        return request

    async def inject_usables(self, request: Any) -> "ToolRegistry":
        """将可用工具过滤后注入 LLM 请求，返回工具注册表。

        封装了「get_llm_usables → modify_llm_usables → ToolRegistry → 注入 TOOL payload」
        的固定四步链，调用方可使用返回的注册表进行后续工具调度。

        Args:
            request: 已创建的 LLMRequest，工具 schema 将以 TOOL payload 追加其中

        Returns:
            ToolRegistry: 注册了所有可用工具的注册表
        """
        from src.kernel.llm import ToolRegistry, LLMPayload, ROLE

        usables = await self.get_llm_usables()
        usables = await self.modify_llm_usables(usables)

        registry = ToolRegistry()
        for usable in usables:
            registry.register(usable)

        if registry.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, registry.get_all()))  # type: ignore[arg-type]

        return registry

    async def run_tool_call(
        self,
        call: Any,
        response: Any,
        usable_map: "ToolRegistry",
        trigger_msg: "Message | None",
    ) -> tuple[bool, bool]:
        """执行单个普通 tool call 并将 TOOL_RESULT 追加到 response。

        处理「查找工具 → 调用 exec_llm_usable → 异常处理 → 追加 TOOL_RESULT」的固定模式。
        仅适用于非控制流工具（pass_and_wait / stop_conversation 等应由调用方自行处理）。

        Args:
            call: LLM 返回的工具调用对象（含 name / id / args）
            response: 当前 LLM 响应对象，TOOL_RESULT payload 将追加于此
            trigger_msg: 触发本次对话的消息；为 None 且工具有效时跳过执行

        Returns:
            tuple[bool, bool]: (appended, exec_success)
                appended: 是否向 response 追加了 TOOL_RESULT
                exec_success: 底层工具是否执行成功；跳过时为 False
        """
        from src.kernel.llm import LLMPayload, ROLE, ToolResult

        _logger = get_logger("chatter")
        args = dict(call.args) if isinstance(call.args, dict) else {}
        args.pop("reason", None)

        exec_success = False
        usable_cls = usable_map.get(call.name)
        if not usable_cls:
            result_text = f"未知的工具: {call.name}"
            _logger.warning(result_text)
        else:
            if trigger_msg is None:
                # 无触发消息时无法执行，但仍需追加 TOOL_RESULT 以保证对话历史完整性，
                # 否则 ASSISTANT 的 tool_calls 缺少对应 tool 消息会导致 API Field required 错误。
                result_text = "无触发消息，跳过执行"
                _logger.debug(f"[{self.chatter_name}] 无触发消息，跳过工具调用: {call.name}")
            else:
                try:
                    exec_success, result = await self.exec_llm_usable(usable_cls, trigger_msg, **args)
                    result_text = str(result) if exec_success else f"执行失败: {result}"
                except Exception as e:
                    result_text = f"执行异常: {e}"
                    _logger.error(f"执行 {call.name} 异常: {e}", exc_info=True)

        response.add_payload(
            LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult(  # type: ignore[arg-type]
                    value=result_text,
                    call_id=call.id,
                    name=call.name,
                ),
            )
        )
        return True, exec_success

    @staticmethod
    def _format_role(role: str | None) -> str:
        """将发送者角色值转为中文显示名。

        Args:
            role: 角色字符串（owner/operator/member/bot/other）或 None

        Returns:
            str: 中文角色名
        """
        _ROLE_MAP = {
            "owner": "群主",
            "operator": "管理员",
            "member": "成员",
            "bot": "机器人",
            "other": "其他",
        }
        if not role:
            return ""
        return _ROLE_MAP.get(str(role).lower(), str(role))

    @staticmethod
    def format_message_line(
        msg: "Message",
        time_format: str = "%H:%M",
    ) -> str:
        """将单条消息格式化为统一的显示行。

        格式：【时间】<role> [platform_id] nickname$cardname [msg_id]： 消息

        Args:
            msg: 消息对象
            time_format: 时间格式化字符串

        Returns:
            str: 格式化后的消息行
        """
        # 时间
        raw_time = getattr(msg, "time", None)
        if isinstance(raw_time, (int, float)):
            time_str = datetime.fromtimestamp(raw_time).strftime(time_format)
        elif isinstance(raw_time, datetime):
            time_str = raw_time.strftime(time_format)
        else:
            time_str = str(raw_time or "")

        # 角色
        role_raw = getattr(msg, "sender_role", None)
        role_str = BaseChatter._format_role(role_raw)
        role_part = f"<{role_str}> " if role_str else ""

        # 平台 ID（优先使用 sender_id，这是平台原始 ID）
        platform_id = getattr(msg, "sender_id", "") or ""
        id_part = f"[{platform_id}] " if platform_id else ""

        # 名称部分：nickname$cardname（无 cardname 时省略 $cardname）
        nickname = getattr(msg, "sender_name", "") or ""
        cardname = getattr(msg, "sender_cardname", None)
        if cardname and cardname != nickname:
            name_part = f"{nickname}${cardname}"
        else:
            name_part = nickname or "未知发送者"

        # 消息 ID 部分（用于LLM引用回复）
        message_id = getattr(msg, "message_id", "") or ""
        msg_id_part = f"[{message_id}]" if message_id else ""

        # 消息内容
        content = getattr(msg, "processed_plain_text", None) or str(getattr(msg, "content", ""))

        return f"【{time_str}】{role_part}{id_part}{name_part} {msg_id_part}： {content}"

    async def fetch_unreads(
        self,
        time_format: str = "%H:%M",
    ) -> tuple[str, list["Message"]]:
        """仅读取未读消息，不修改上下文。

        Args:
            time_format: 时间格式化字符串

        Returns:
            tuple[str, list[Message]]: (格式化后的未读消息文本，每条消息占一行, 未读消息列表)
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

        lines = [self.format_message_line(msg, time_format) for msg in unread_messages]
        return "\n".join(lines), unread_messages

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
