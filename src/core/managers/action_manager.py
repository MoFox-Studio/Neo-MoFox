"""Action 管理器。

本模块提供 Action 管理器，负责 Action 组件的注册、发现、作用域筛选、
动态激活判定、schema 与执行。Action 是"主动的响应"，通过 LLM Tool Calling 调用。
管理器维护 Action 组件的全局集合，并按传入的组件类集合进行筛选。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger
from src.kernel.llm import LLMUsable
from src.kernel.concurrency import get_task_manager

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType, EventType
from src.core.components.utils import should_strip_auto_reason_argument
from src.core.managers.stream_manager import get_stream_manager

from src.core.managers.utils import (
    build_usable_static_context,
    filter_by_associated_types,
    publish_before_filter_event,
    static_filter_usables,
)

if TYPE_CHECKING:
    from src.core.components.base.action import BaseAction
    from src.core.components.base.chatter import BaseChatter
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream, StreamContext

logger = get_logger("action_manager")


class ActionManager:
    """Action 管理器。

    负责管理所有 Action 组件，提供查询、筛选、激活与执行接口。
    筛选前发布 ``BEFORE_ACTION_FILTER`` 事件，允许外部事件处理器改写组件类集合。
    筛选逻辑从"按聊天流获取"改为"按传入组件类筛选"，并支持聊天器名称参数。

    Attributes:
        _schema_cache: Action schema 缓存

    Examples:
        >>> manager = ActionManager()
        >>> actions = await manager.filter_actions_for_chat(
        ...     usables=[MyAction],
        ...     chat_type="private",
        ...     chatter_name="my_chatter",
        ... )
    """

    def __init__(self) -> None:
        """初始化 Action 管理器。"""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    # ── 查询 ──

    def get_all_actions(self) -> dict[str, type["BaseAction"]]:
        """获取所有已注册的 Action 组件。

        Returns:
            dict[str, type[BaseAction]]: 将签名映射到 Action 类的字典
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.ACTION)

    def get_actions_for_plugin(self, plugin_name: str) -> dict[str, type["BaseAction"]]:
        """获取指定插件的所有 Action 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseAction]]: 将签名映射到 Action 类的字典
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.ACTION)

    def get_action_class(self, signature: str) -> type["BaseAction"] | None:
        """通过签名获取 Action 类。

        Args:
            signature: Action 组件签名

        Returns:
            type[BaseAction] | None: Action 类，如果未找到则返回 None
        """
        registry = get_global_registry()
        return registry.get(signature)

    # ── 筛选 ──

    async def filter_actions_for_chat(
        self,
        usables: list[type["LLMUsable"]],
        *,
        chat_type: "ChatType | str" = "all",
        chatter_name: str = "",
        platform: str = "",
        stream_id: str = "",
        chat_stream: "ChatStream | None" = None,
        stream_context: "StreamContext | None" = None,
        chatter: "BaseChatter | None" = None,
        plugin: "BasePlugin | None" = None,
        message_content: str = "",
    ) -> list[type["LLMUsable"]]:
        """筛选适用于特定聊天上下文的 Action 组件类列表。

        只负责筛选：对传入的 ``usables`` 完成静态维度过滤（部署期）+
        筛选前事件钩子 + 动态 go_activate 激活。拉取全量由 ``get_all_actions``
        单独承担，调用方需自行获取后传入。

        Args:
            usables: 待筛选的组件类列表（必填，由调用方传入）
            chat_type: 聊天类型（private / group / all）
            chatter_name: Chatter 名称
            platform: 平台名称
            stream_id: 聊天流 ID（空表示不按流筛选）
            chat_stream: 候选聊天流实例（提供后由其派生完整上下文）
            stream_context: 聊天流上下文（提供后按内容类型过滤）
            chatter: 当前驱动执行的 Chatter 实例
            plugin: 归属插件实例（go_activate 签名解析失败时的兜底）
            message_content: 当前消息内容，供 go_activate 使用

        Returns:
            list[type[LLMUsable]]: 筛选并激活通过的 Action 组件类列表
        """
        if isinstance(chat_type, ChatType):
            chat_type = chat_type.value

        # 筛选前事件钩子：外部处理器可改写组件集合
        usables = await publish_before_filter_event(
            EventType.BEFORE_ACTION_FILTER,
            component_kind="action",
            usables=usables,
            stream_id=stream_id or (getattr(chat_stream, "stream_id", "") or ""),
            chatter_name=chatter_name,
            stream_context=stream_context,
        )

        ctx = build_usable_static_context(
            usables=usables,
            chat_type=chat_type,
            chatter_name=chatter_name,
            platform=platform,
            stream_id=stream_id,
            chat_stream=chat_stream,
            chatter=chatter,
        )

        filtered, removals = static_filter_usables(usables, ctx)

        if stream_context is not None:
            removals.extend(filter_by_associated_types(filtered, stream_context))

        # 恢复筛选结果日志
        logger.debug(
            f"为聊天上下文筛选 Action: chat_type={chat_type}, "
            f"chatter={chatter_name}, platform={platform}, "
            f"结果: {len(filtered)}/{len(usables)}"
        )
        if removals:
            summary = " | ".join(f"{n}({r})" for n, r in removals)
            logger.debug(f"移除 Action 组件: {summary}")

        # 动态激活判定（go_activate）；未提供流上下文时仅做静态筛选
        from src.core.managers import get_plugin_manager

        if chat_stream is None:
            return filtered

        tasks: list[Any] = []
        signatures: list[str] = []
        available: list[type["LLMUsable"]] = []

        for action_cls in filtered:
            signature = action_cls.get_signature() or action_cls.__name__  # type: ignore[attr-defined]
            # 优先按组件签名解析所属插件；无法解析时回退到传入的 plugin
            action_plugin = None
            parts = signature.split(":") if signature else []
            plugin_name = parts[0] if len(parts) >= 3 else ""
            if plugin_name:
                action_plugin = get_plugin_manager().get_plugin(plugin_name)
            if action_plugin is None:
                action_plugin = plugin
            if action_plugin is None:
                logger.warning(f"未找到 Action 所属插件实例: {signature}，跳过激活判定")
                continue
            try:
                instance = action_cls(chat_stream=chat_stream, plugin=action_plugin)  # type: ignore[call-arg]
                instance._last_message = message_content  # type: ignore[attr-defined]
                go_activate = getattr(instance, "go_activate", None)
                if not callable(go_activate):
                    available.append(action_cls)
                    continue
                tasks.append(go_activate())
                signatures.append(signature)
            except Exception as e:
                logger.error(f"创建 Action 实例 {signature} 失败: {e}")
                continue

        if tasks:
            results = await get_task_manager().gather(*tasks, return_exceptions=True)
            for signature, result in zip(signatures, results, strict=False):
                if isinstance(result, Exception) or not result:
                    continue
                action_cls = next(
                    (
                        c
                        for c in filtered
                        if (c.get_signature() or c.__name__) == signature  # type: ignore[attr-defined]
                    ),
                    None,
                )
                if action_cls is not None:
                    available.append(action_cls)

        return available

    # ── Schema ──

    def get_action_schema(self, signature: str) -> dict[str, Any] | None:
        """获取 Action 的 Tool Schema。

        如果 schema 已缓存则返回缓存，否则生成新的 schema。

        Args:
            signature: Action 组件签名

        Returns:
            dict[str, Any] | None: OpenAI Tool Calling 格式的 schema
        """
        if signature in self._schema_cache:
            return self._schema_cache[signature]

        action_cls = self.get_action_class(signature)
        if not action_cls:
            return None

        schema = action_cls.to_schema()
        self._schema_cache[signature] = schema
        return schema

    async def get_action_schemas(
        self,
        chat_type: "ChatType | str" = "all",
        chatter_name: str = "",
        platform: str = "",
        stream_id: str = "",
    ) -> list[dict[str, Any]]:
        """获取适用于特定聊天上下文的所有 Action Schema。

        Args:
            chat_type: 聊天类型（private / group / all）
            chatter_name: Chatter 名称
            platform: 平台名称
            stream_id: 聊天流 ID

        Returns:
            list[dict[str, Any]]: Action schema 列表
        """
        actions = await self.filter_actions_for_chat(
            list(self.get_all_actions().values()),
            chat_type=chat_type,
            chatter_name=chatter_name,
            platform=platform,
            stream_id=stream_id,
        )
        schemas = []

        for action_cls in actions:
            # 构建签名
            signature = self._build_signature(action_cls)  # type: ignore
            schema = self.get_action_schema(signature)
            if schema:
                schemas.append(schema)

        return schemas

    def clear_schema_cache(self, signature: str | None = None) -> None:
        """清除 schema 缓存。

        Args:
            signature: 要清除的 Action 签名，None 表示清除全部
        """
        if signature:
            self._schema_cache.pop(signature, None)
        else:
            self._schema_cache.clear()

    # ── 执行 ──

    async def execute_action(
        self,
        signature: str,
        plugin: "BasePlugin",
        message: "Message",
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """执行 Action。

        创建 Action 实例并调用其 execute 方法。
        在执行前、执行成功后、执行失败时分别触发对应的生命周期事件。

        Args:
            signature: Action 组件签名
            plugin: 所属插件实例
            message: 触发的消息
            **kwargs: 传递给 execute 方法的参数

        Returns:
            tuple[bool, str]: (是否成功, 结果详情)

        Raises:
            ValueError: 如果 Action 类未找到
            RuntimeError: 如果 Action 执行失败
        """
        action_cls = self.get_action_class(signature)
        if not action_cls:
            raise ValueError(f"Action 类未找到: {signature}")

        # 获取或创建 ChatStream（使用 StreamManager）
        stream_manager = get_stream_manager()
        chat_stream = await stream_manager.activate_stream(message.stream_id)

        # 如果流不存在，创建新的流
        if not chat_stream:
            group_id = message.extra.get("group_id") or message.extra.get(
                "target_group_id"
            )

            chat_stream = await stream_manager.get_or_create_stream(
                platform=message.platform,
                user_id=message.sender_id,
                group_id=str(group_id) if group_id else "",
                chat_type=message.chat_type,
            )

        # 创建 Action 实例
        action_instance = action_cls(chat_stream=chat_stream, plugin=plugin)

        # 仅剥离系统自动注入的 reason；组件原生声明 reason 时必须保留。
        if should_strip_auto_reason_argument(action_instance.execute, kwargs):
            kwargs.pop("reason", None)

        import time

        start_time = time.time()

        # 触发动作调用执行前事件
        try:
            from src.core.components.types import EventType as _EventType
            from src.kernel.event import get_event_bus

            event_bus = get_event_bus()
            if event_bus.get_subscribers(_EventType.BEFORE_ACTION_CALL):
                _, modified_params = await event_bus.publish(
                    _EventType.BEFORE_ACTION_CALL,
                    {
                        "signature": signature,
                        "action_name": action_instance.name,
                        "action_description": action_instance.description,
                        "args": dict(kwargs),
                        "message": message,
                    },
                )
                # 应用事件处理器对参数的修改
                if "args" in modified_params:
                    new_args = modified_params["args"]
                    if isinstance(new_args, dict):
                        kwargs = new_args
                if "message" in modified_params:
                    modified_message = modified_params["message"]
                    if modified_message is not None:
                        message = modified_message
        except Exception:
            # 事件触发失败不中断动作执行，静默降级
            pass

        # 执行 Action
        try:
            result = await action_instance._wrap_execute(**kwargs).wait_done()

            execution_time = time.time() - start_time
            status_emoji = "✅" if result[0] else "❌"
            logger.info(
                f"{status_emoji} 动作执行完成: {action_instance.name}, 耗时: {execution_time:.2f}s"
            )

            # 触发动作调用执行后事件
            try:
                from src.core.components.types import EventType as _EventType
                from src.kernel.event import get_event_bus

                event_bus = get_event_bus()
                if event_bus.get_subscribers(_EventType.AFTER_ACTION_CALL):
                    _, modified_params = await event_bus.publish(
                        _EventType.AFTER_ACTION_CALL,
                        {
                            "signature": signature,
                            "action_name": action_instance.name,
                            "action_description": action_instance.description,
                            "args": dict(kwargs),
                            "result": result[1],
                            "success": result[0],
                            "execution_time": execution_time,
                            "message": message,
                        },
                    )
                    # 应用事件处理器对结果和消息的修改
                    if "result" in modified_params:
                        new_result = modified_params["result"]
                        if new_result is not None:
                            result = (result[0], new_result)
                    if "message" in modified_params:
                        modified_message = modified_params["message"]
                        if modified_message is not None:
                            message = modified_message
            except Exception:
                # 事件触发失败不中断动作执行，静默降级
                pass

            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"执行 Action 失败 ({signature}): {e}",
                exc_info=True,
            )

            # 触发动作调用失败事件
            try:
                from src.core.components.types import EventType as _EventType
                from src.kernel.event import get_event_bus

                event_bus = get_event_bus()
                if event_bus.get_subscribers(_EventType.ON_ACTION_CALL_FAILED):
                    await event_bus.publish(
                        _EventType.ON_ACTION_CALL_FAILED,
                        {
                            "signature": signature,
                            "action_name": action_instance.name,
                            "action_description": getattr(
                                action_instance, "description", ""
                            ),
                            "args": dict(kwargs),
                            "error": e,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "execution_time": execution_time,
                            "message": message,
                        },
                    )
            except Exception:
                # 事件触发失败不中断异常传播，静默降级
                pass

            raise RuntimeError(f"Action 执行失败: {e}") from e

    # ── 内部 ──

    def _build_signature(self, action_cls: type["BaseAction"]) -> str:
        """构建 Action 组件签名。

        从 Action 类的 _signature_ 属性获取签名，该属性在组件注册时设置。
        如果属性不存在，则从注册表反向查找。

        Args:
            action_cls: Action 类

        Returns:
            str: 组件签名
        """
        # 优先使用 _signature_ 属性（在 plugin_manager 注册时设置）
        if hasattr(action_cls, "_signature_"):
            return action_cls._signature_  # type: ignore[attr-defined]

        # 如果属性不存在，从注册表反向查找
        registry = get_global_registry()
        all_actions = registry.get_by_type(ComponentType.ACTION)

        for signature, cls in all_actions.items():
            if cls is action_cls:
                return signature

        # 找不到签名，返回空字符串
        logger.warning(f"无法找到 Action 类的签名: {action_cls.__name__}")
        return ""


# 全局 Action 管理器实例
_global_action_manager: ActionManager | None = None


def get_action_manager() -> ActionManager:
    """获取全局 Action 管理器实例。

    Returns:
        ActionManager: 全局 Action 管理器单例
    """
    global _global_action_manager
    if _global_action_manager is None:
        _global_action_manager = ActionManager()
    return _global_action_manager
