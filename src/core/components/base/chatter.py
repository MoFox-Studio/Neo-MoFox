"""聊天器组件基类。

本模块提供 BaseChatter 类，定义聊天器组件的基本行为。
Chatter 是 Bot 的智能核心，定义对话逻辑和流程。
"""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, cast

from src.core.components.base.component import BaseComponent
from src.core.components.types import (
    ChatterResult,
    ChatType,
    WaitResumeEvent,
)
from src.core.components.base.action import BaseAction
from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool
from src.core.utils.context_compression import default_chat_context_compression_handler
from src.kernel.logger import get_logger, COLOR


def get_stream_manager() -> Any:
    """获取 StreamManager 单例（延迟导入避免模块循环依赖）。"""
    from src.core.managers import get_stream_manager as _get_sm

    return _get_sm()


def get_plugin_manager() -> Any:
    """获取 PluginManager 单例（延迟导入避免模块循环依赖）。"""
    from src.core.managers import get_plugin_manager as _get_pm

    return _get_pm()

if TYPE_CHECKING:
    from src.core.components.base.action import BaseAction
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.tool import BaseTool
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message
    from src.kernel.llm import LLMRequest
    from src.kernel.llm.payload.tooling import LLMUsable, ToolRegistry


class BaseChatter(BaseComponent):
    """聊天器组件基类。

    Chatter 定义 Bot 的对话逻辑和流程。
    使用生成器模式，通过 yield 返回 Wait/Success/Failure/Stop 结果。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        name: 聊天器名称
        description: 聊天器描述
        associated_platforms: 关联的平台列表
        chat_type: 支持的聊天类型

    Examples:
        >>> class MyChatter(BaseChatter):
        ...     name = "my_chatter"
        ...     description = "我的聊天器"
        ...
        ...     async def execute(self, unreads: list[Message]) -> AsyncGenerator[ChatterResult, None]:
        ...         yield Wait("等待 LLM 响应")
        ...         # 执行逻辑...
        ...         yield Success("完成")
    """

    _plugin_: str
    _signature_: str

    component_type = "chatter"
    _legacy_name_attr = "chatter_name"
    _legacy_desc_attr = "chatter_description"
    chatter_name: str = ""
    chatter_description: str = ""

    # 聊天器元数据
    name: str = ""
    description: str = ""

    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL

    # 可选的聊天流运行时声明；不设置时保持框架默认行为。
    stream_tick_interval: float | None = None
    allow_message_buffer: bool | None = None

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

    def apply_stream_runtime_options(self, chat_stream: Any) -> None:
        """将 chatter 声明的流运行时选项写入当前聊天流。"""

        context = getattr(chat_stream, "context", None)
        if context is None:
            return

        tick_interval = self.stream_tick_interval
        if tick_interval is not None and tick_interval > 0:
            context.tick_interval_override = float(tick_interval)

        if self.allow_message_buffer is not None:
            context.allow_message_buffer = bool(self.allow_message_buffer)

    @abstractmethod
    async def execute(
        self
    ) -> AsyncGenerator[ChatterResult, WaitResumeEvent | None]:
        """执行聊天器的主要逻辑。

        使用生成器模式，通过 yield 返回执行结果。

        Yields:
            ChatterResult: Wait/Success/Failure/Stop 结果

        Examples:
            >>> async for result in my_chatter.execute():
            ...     if isinstance(result, Wait):
            ...         print(f"等待: {result.time}")
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

        将筛选与激活全部委托给各组件管理器经 API 封装的流程：
        1. 静态筛选（Fast-Path）：按组件类型调用各 API 的 get_*_for_chat。
        2. 动态激活（Slow-Path）：对存活组件调用各 API 的 activate_*_for_chat。

        Args:
            llm_usables: 原始 LLMUsable 组件列表

        Returns:
            list[type[LLMUsable]]: 修改后的可用组件类列表
        """
        logger = get_logger("chatter", display="聊天器", color=COLOR.MAGENTA)
        chat_stream = await get_stream_manager().get_or_create_stream(
            stream_id=self.stream_id
        )
        chat_context = chat_stream.context

        # 构造当前流的通用过滤快照
        from src.core.components.usable_filter import build_filter_context_from_stream

        filter_ctx = build_filter_context_from_stream(chat_stream, self)

        # 按组件类型分组待筛选列表
        action_usables: list[type[LLMUsable]] = []
        tool_usables: list[type[LLMUsable]] = []
        agent_usables: list[type[LLMUsable]] = []

        for usable_cls in llm_usables:
            usable_cls = cast(type["BaseAction|BaseAgent|BaseTool"], usable_cls)  # 类型提示
            if issubclass(usable_cls, BaseAction):
                action_usables.append(usable_cls)
            elif issubclass(usable_cls, BaseTool):
                tool_usables.append(usable_cls)
            elif issubclass(usable_cls, BaseAgent):
                agent_usables.append(usable_cls)

        # Phase 1：静态筛选（全走 API）
        from src.app.plugin_system.api import action_api, agent_api, tool_api

        kept_actions = await action_api.get_actions_for_chat(
            action_usables,
            chat_type=filter_ctx.chat_type,
            chatter_name=filter_ctx.chatter_name,
            platform=filter_ctx.platform,
            stream_id=self.stream_id,
            chat_stream=chat_stream,
            stream_context=chat_context,
            chatter=self,
        )
        kept_tools = await tool_api.get_tools_for_chat(
            tool_usables,
            chat_type=filter_ctx.chat_type,
            chatter_name=filter_ctx.chatter_name,
            platform=filter_ctx.platform,
            stream_id=self.stream_id,
            chat_stream=chat_stream,
            stream_context=chat_context,
            chatter=self,
        )
        kept_agents = await agent_api.get_agents_for_chat(
            agent_usables,
            chat_type=filter_ctx.chat_type,
            chatter_name=filter_ctx.chatter_name,
            platform=filter_ctx.platform,
            stream_id=self.stream_id,
            chat_stream=chat_stream,
            stream_context=chat_context,
            chatter=self,
        )

        # Phase 2：动态激活（全走 API，plugin 作为签名解析失败的兜底）
        kept_actions = await action_api.activate_actions_for_chat(
            kept_actions,
            chat_stream=chat_stream,
            plugin=self.plugin,
            message_content=(
                chat_context.current_message.processed_plain_text
                if chat_context.current_message and chat_context.current_message.processed_plain_text
                else str(chat_context.current_message.content or "")
                if chat_context.current_message
                else ""
            ),
        )
        kept_tools = await tool_api.activate_tools_for_chat(
            kept_tools,
            chat_stream=chat_stream,
            plugin=self.plugin,
            message=chat_context.current_message,
        )
        kept_agents = await agent_api.activate_agents_for_chat(
            kept_agents,
            chat_stream=chat_stream,
            plugin=self.plugin,
        )

        available = [*kept_actions, *kept_tools, *kept_agents]

        logger.info(
            f"[{chat_stream.stream_id}] 可用组件: {len(available)}/{len(llm_usables)}"
        )

        return available

    def _resolve_component_plugin(self, signature: str | None) -> "BasePlugin":
        """根据组件签名解析其所属插件实例。"""
        if not signature:
            return self.plugin

        try:
            from src.app.plugin_system.api import plugin_api
            from src.core.components.types import parse_signature

            plugin_name = parse_signature(signature)["plugin_name"]
            target_plugin = plugin_api.get_plugin(plugin_name)
            if target_plugin:
                return target_plugin
        except Exception:
            pass

        try:
            from src.core.components.types import parse_signature

            plugin_name = parse_signature(signature)["plugin_name"]
            target_plugin = get_plugin_manager().get_plugin(plugin_name)
            if target_plugin:
                return target_plugin
        except Exception:
            pass

        return self.plugin

    async def exec_llm_usable(
        self,
        usable_cls: type[LLMUsable],
        message: "Message",
        **kwargs: Any,
    ) -> tuple[bool, Any]:
        """执行指定的 LLMUsable 组件。

        此方法执行单个 Tool、Action 或 Agent，并委托统一执行器处理 coroutine
        与异步生成器两种 execute 写法。异步生成器约定最后一次非空 ``yield``
        为返回结果。

        Args:
            usable_cls: 要执行的 LLMUsable 组件类。
            message: 触发本次调用的消息；Action 会用它恢复发送上下文。
            **kwargs: 传递给组件 ``execute`` 的关键字参数。

        Returns:
            tuple[bool, Any]: ``(是否执行成功, 返回结果)``。

        Raises:
            ValueError: 组件未注入插件签名、传入 Chatter，或类型不受支持时抛出。

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

        if not issubclass(usable_cls, (BaseTool, BaseAction, BaseAgent)):
            raise ValueError("未知的 LLMUsable 组件类型，无法执行")

        from src.app.plugin_system.api import llm_api

        owner_plugin = self._resolve_component_plugin(sig)
        return await llm_api.exec_llm_usable(
            usable_cls,
            plugin=owner_plugin,
            stream_id=self.stream_id,
            message=message,
            kwargs=kwargs,
        )

    def create_request(
        self,
        task: str = "actor",
        request_name: str = "",
        with_reminder: str | None = None,
    ) -> "LLMRequest":
        """快速创建 LLM 请求，自动加载任务模型集与上下文管理器。

        封装了「获取模型集 → 创建上下文管理器 → 创建 LLMRequest」的固定样板。
         request_name 默认取 name。

        当 ``with_reminder`` 非空时，会自动为该 bucket 生成两个 reminder source：

        1. **全局 source** — bucket 原名，读取所有流共享的全局 reminder。
        2. **流私有 source** — ``stream:{stream_id}:{bucket}``，仅读取当前流的私有 reminder。

        插件通过 :func:`prompt_api.add_system_reminder` 写入全局 reminder，
        通过 :func:`prompt_api.add_stream_reminder` 写入流私有 reminder，
        chatter 侧无需任何额外操作即可同时拾取两者。

        Args:
            task: 模型任务名称（对应 config/model.toml 中的 task key），默认 "actor"
            request_name: LLM 请求名称，默认使用 name
            with_reminder: 可选的 system reminder bucket 名称（也接受
                :class:`SystemReminderBucket` 枚举值，因其继承 :class:`str`）。
                传入后会自动登记全局 + 流私有两个 bucket 到上下文管理器。

        Returns:
            LLMRequest: 配置好上下文管理器的 LLM 请求对象

        Raises:
            KeyError: 当 task 在模型配置中不存在时
        """
        from src.app.plugin_system.api import llm_api
        from src.core.prompt import STREAM_BUCKET_PREFIX
        from src.kernel.llm import LLMContextManager, ReminderSourceSpec

        model_set = llm_api.get_model_set_by_task(task)
        reminder_sources = None
        if with_reminder is not None:
            bucket = with_reminder

            reminder_sources = [
                ReminderSourceSpec(
                    bucket=bucket,
                    wrap_with_system_tag=True,
                )
            ]
            if self.stream_id:
                reminder_sources.append(
                    ReminderSourceSpec(
                        bucket=f"{STREAM_BUCKET_PREFIX}{self.stream_id}:{bucket}",
                        wrap_with_system_tag=True,
                    )
                )

        context_manager = LLMContextManager(
            context_compression_handler=default_chat_context_compression_handler,
            reminder_sources=reminder_sources,
        )

        _logger = get_logger("chatter")
        if model_set:
            first = model_set[0]
            _logger.debug(
                f"provider={first.get('api_provider')}, "
                f"base_url={first.get('base_url')}, "
                f"timeout={first.get('timeout')}"
            )

        return llm_api.create_llm_request(
            model_set=model_set,
            request_name=request_name or self.name,
            context_manager=context_manager,
            stream_id=self.stream_id,
        )

    async def inject_usables(self, request: Any) -> "ToolRegistry":
        """将可用工具过滤后注入 LLM 请求，返回工具注册表。

        封装了「get_llm_usables → modify_llm_usables → ToolRegistry → 注入 TOOL payload」
        的固定四步链，调用方可使用返回的注册表进行后续工具调度。

        Args:
            request: 已创建的 LLMRequest，工具 schema 将以 TOOL payload 追加其中

        Returns:
            ToolRegistry: 注册了所有可用工具的注册表
        """
        from src.app.plugin_system.api import llm_api
        from src.kernel.llm import LLMPayload, ROLE

        usables = await self.get_llm_usables()
        usables = await self.modify_llm_usables(usables)

        registry = llm_api.create_tool_registry(tools=usables)

        if registry.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, registry.get_all()))  # type: ignore[arg-type]

        return registry

    async def run_tool_call(
        self,
        calls: Any,
        response: Any,
        usable_map: "ToolRegistry",
        trigger_msg: "Message | None",
        task_observer: Callable[[asyncio.Task[None]], None] | None = None,
    ) -> list[tuple[bool, bool]]:
        """执行一次响应中的普通 tool calls 并写回 TOOL_RESULT。

        此方法面向一批 call，实际执行会委托统一调度器：可并发的 coroutine
        会并发运行，顺序敏感的异步生成器会在 ``"_READY"`` 阶段按原始顺序门控。
        控制流工具（pass_and_wait / stop_conversation 等）仍应由调用方先行处理。

        Args:
            calls: LLM 返回的一批工具调用对象，按模型输出顺序排列。
            response: 当前 LLM 响应对象，TOOL_RESULT payload 将按 ``calls`` 顺序追加。
            usable_map: 可调用组件注册表，用 call name 查找组件类。
            trigger_msg: 触发本次对话的消息；为 None 时跳过实际执行并写回结果。

        Returns:
            list[tuple[bool, bool]]: 与 ``calls`` 顺序一致的结果列表。
            每项为 ``(是否已写回 TOOL_RESULT, execute 是否成功)``。
        """
        from src.app.plugin_system.api import llm_api

        return await llm_api.run_tool_call(
            calls=calls,
            response=response,
            usable_map=usable_map,
            trigger_msg=trigger_msg,
            plugin=self.plugin,
            stream_id=self.stream_id,
            resolve_component_plugin=self._resolve_component_plugin,
            display_name=self.name,
            task_observer=task_observer,
        )

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

        格式：【时间】<role> [platform_id] 昵称:nickname$群名片:cardname [msg_id]： 消息

        Args:
            msg: 消息对象
            time_format: 时间格式化字符串

        Returns:
            str: 格式化后的消息行
        """
        # 时间
        raw_time = msg.time
        if isinstance(raw_time, (int, float)):
            time_str = datetime.fromtimestamp(raw_time).strftime(time_format)
        elif isinstance(raw_time, datetime):
            time_str = raw_time.strftime(time_format)
        else:
            time_str = str(raw_time or "")

        # 角色
        role_raw = msg.sender_role
        role_str = BaseChatter._format_role(role_raw)
        role_part = f"<{role_str}> " if role_str else ""

        # 平台 ID（优先使用 sender_id，这是平台原始 ID）
        platform_id = msg.sender_id or ""
        id_part = f"[{platform_id}] " if platform_id else ""

        # 名称部分：昵称:nickname$群名片:cardname（无 cardname 或与 nickname 相同时仅显示 nickname）
        nickname = msg.sender_name or ""
        cardname = msg.sender_cardname
        if cardname and cardname != nickname:
            name_part = f"昵称:{nickname}$群名片:{cardname}"
        else:
            name_part = nickname or "未知发送者"

        # 消息 ID 部分（用于LLM引用回复）
        message_id = msg.message_id or ""
        msg_id_part = f"[{message_id}]" if message_id else ""

        # 消息内容
        content = msg.processed_plain_text or str(msg.content)

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
        from src.app.plugin_system.api import stream_api

        logger = get_logger("chatter")

        chat_stream = await stream_api.get_stream(stream_id=self.stream_id)

        if not chat_stream:
            logger.warning(
                f"[{self.name}] 无法获取聊天流: {self.stream_id[:8]}"
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
        from src.app.plugin_system.api import stream_api

        logger = get_logger("chatter")

        if not unread_messages:
            return 0

        chat_stream = await stream_api.get_stream(stream_id=self.stream_id)

        if not chat_stream:
            logger.warning(
                f"[{self.name}] 无法获取聊天流: {self.stream_id[:8]}"
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
            f"[{self.name}] flush 未读消息 {flushed_count} 条"
        )

        return flushed_count
