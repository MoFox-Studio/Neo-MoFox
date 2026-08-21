"""Action 管理器。

本模块提供 Action 管理器，负责 Action 组件的注册、发现和筛选。
Action 是"主动的响应"，通过 LLM Tool Calling 调用。
筛选仅针对传入的组件类列表进行，不从聊天流或全局注册表获取组件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger
from src.kernel.llm import LLMUsable

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType
from src.core.components.utils import should_strip_auto_reason_argument
from src.core.managers.stream_manager import get_stream_manager

from src.core.components.base.action import BaseAction

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message


logger = get_logger("action_manager")


class ActionManager:
    """Action 管理器。

    负责管理所有 Action 组件，提供查询、筛选和执行接口。
    筛选仅针对传入的组件类列表进行，不从聊天流或全局注册表获取组件。

    Attributes:
        _schema_cache: Action schema 缓存
    """

    def __init__(self) -> None:
        """初始化 Action 管理器。"""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    def get_all_actions(self) -> dict[str, type["BaseAction"]]:
        """获取所有已注册的 Action 组件。

        Returns:
            dict[str, type[BaseAction]]: Action 签名到类的映射
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.ACTION)

    def get_actions_for_plugin(self, plugin_name: str) -> dict[str, type["BaseAction"]]:
        """获取指定插件的所有 Action 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseAction]]: Action 签名到类的映射
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.ACTION)

    def get_action_class(self, signature: str) -> type["BaseAction"] | None:
        """通过签名获取 Action 类。

        Args:
            signature: Action 组件签名

        Returns:
            type[BaseAction] | None: Action 类，未找到则返回 None
        """
        registry = get_global_registry()
        return registry.get(signature)

    def get_action_schema(self, signature: str) -> dict[str, Any] | None:
        """获取 Action 的 Tool Schema。

        如果 schema 已缓存则返回缓存，否则生成新的 schema。

        Args:
            signature: Action 组件签名

        Returns:
            dict[str, Any] | None: OpenAI Tool Calling 格式的 schema

        Examples:
            >>> schema = manager.get_action_schema("my_plugin:action:send_message")
            >>> {
            ...     "type": "function",
            ...     "function": {
            ...         "name": "send_message",
            ...         "description": "发送消息",
            ...         "parameters": {...}
            ...     }
            ... }
        """
        if signature in self._schema_cache:
            return self._schema_cache[signature]

        action_cls = self.get_action_class(signature)
        if not action_cls:
            return None

        schema = action_cls.to_schema()
        self._schema_cache[signature] = schema
        return schema

    def get_action_schemas(
        self,
        component_classes: list[type["LLMUsable"]],
    ) -> list[dict[str, Any]]:
        """获取组件类列表对应的 Action Schema 列表。

        Args:
            component_classes: 已筛选的 Action 组件类列表

        Returns:
            list[dict[str, Any]]: Action schema 列表
        """
        schemas = []
        for action_cls in component_classes:
            schema = action_cls.to_schema()
            if schema:
                schemas.append(schema)
        return schemas

    async def filter_actions(
        self,
        component_classes: list[type["LLMUsable"]],
        *,
        stream_id: str = "",
        chatter_name: str = "",
        chatter_signature: str = "",
        chat_type: ChatType = ChatType.ALL,
        platform: str = "",
    ) -> list[type["LLMUsable"]]:
        """筛选传入的 Action 组件类列表。

        先经统一入口（发布 ``BEFORE_ACTION_FILTER`` 事件 + 通用静态过滤），
        再执行 Action 特有的激活逻辑：关联类型校验与 ``go_activate`` 判定。

        Args:
            component_classes: 待筛选的 Action 组件类列表
            stream_id: 聊天流 ID
            chatter_name: 聊天器名称
            chatter_signature: 聊天器签名
            chat_type: 聊天类型
            platform: 平台名称

        Returns:
            筛选后的 Action 组件类列表
        """
        from src.core.components.types import EventType
        from src.core.managers.utils.filtering import filter_component_classes

        filtered = await filter_component_classes(
            component_classes,
            event_type=EventType.BEFORE_ACTION_FILTER,
            stream_id=stream_id,
            chatter_name=chatter_name,
            chatter_signature=chatter_signature,
            chat_type=chat_type,
            platform=platform,
        )

        if not filtered or not stream_id:
            return filtered

        return await self._apply_action_activation(filtered, stream_id=stream_id)

    async def _apply_action_activation(
        self,
        component_classes: list[type["LLMUsable"]],
        *,
        stream_id: str,
    ) -> list[type["LLMUsable"]]:
        """执行 Action 特有的激活逻辑：关联类型校验 + go_activate 判定。

        Args:
            component_classes: 已通过通用静态过滤的 Action 组件类列表
            stream_id: 聊天流 ID

        Returns:
            激活判定后的 Action 组件类列表
        """
        from src.core.managers import get_plugin_manager, get_stream_manager
        from src.core.managers.utils.filtering import (
            create_component_instance,
            extract_message_content,
            resolve_component_plugin,
        )

        chat_stream = await get_stream_manager().get_or_create_stream(
            stream_id=stream_id
        )
        chat_context = chat_stream.context

        # Action 特有：关联类型校验
        type_supported = self._filter_by_associated_types(
            component_classes, chat_context
        )

        # Action 特有：go_activate 激活判定
        removals: list[tuple[str, str]] = []
        plugin_manager = get_plugin_manager()
        message_content = extract_message_content(chat_context)
        tasks = []
        task_signatures = []

        for usable_cls in type_supported:
            signature = self._component_signature(usable_cls)

            plugin = resolve_component_plugin(plugin_manager, usable_cls, signature)
            if plugin is None:
                logger.warning(f"未找到 Plugin 实例，跳过激活判定: {signature}")
                continue

            try:
                instance = create_component_instance(
                    usable_cls, plugin, chat_stream, stream_id
                )
                if isinstance(instance, BaseAction):
                    instance._last_message = message_content
                go_activate = getattr(instance, "go_activate", None)
                if not callable(go_activate):
                    continue
                tasks.append(go_activate())
                task_signatures.append(signature)
            except Exception as e:
                logger.error(f"创建 Action 实例 {signature} 失败: {e}")
                removals.append((signature, f"创建实例失败: {e}"))

        if tasks:
            from src.kernel.concurrency import get_task_manager

            results = await get_task_manager().gather(*tasks, return_exceptions=True)
            for signature, result in zip(task_signatures, results, strict=False):
                if isinstance(result, Exception):
                    logger.error(f"[{stream_id}] 激活判定 {signature} 时出错: {result}")
                    removals.append((signature, f"激活判定出错: {result}"))
                elif not result:
                    removals.append((signature, "go_activate 返回 False"))

        if removals:
            summary = " | ".join(
                f"{name}({reason})" for name, reason in removals
            )
            logger.info(f"[{stream_id}] 移除 Action: {summary}")

        removal_names = {name for name, _ in removals}
        available = [
            usable_cls
            for usable_cls in type_supported
            if self._component_signature(usable_cls) not in removal_names
        ]
        logger.info(
            f"[{stream_id}] 可用 Action: {len(available)}/{len(component_classes)}"
        )

        return available

    def _filter_by_associated_types(
        self,
        component_classes: list[type["LLMUsable"]],
        chat_context: Any,
    ) -> list[type["LLMUsable"]]:
        """Action 特有的关联类型校验：当前流不支持的 Action 被剔除。"""
        survivors: list[type["LLMUsable"]] = []
        for usable_cls in component_classes:
            if not issubclass(usable_cls, BaseAction):
                survivors.append(usable_cls)
                continue

            try:
                required_types = usable_cls.validate_associated_types()
            except ValueError:
                required_types = []

            if not chat_context.check_types(required_types):
                types_str = ", ".join(required_types)
                signature = self._component_signature(usable_cls)
                logger.debug(f"[移除组件] {signature}：适配器不支持（需要: {types_str}）")
                continue

            survivors.append(usable_cls)

        return survivors

    @staticmethod
    def _component_signature(usable_cls: type["LLMUsable"]) -> str:
        """获取组件签名，无法获取时回退到类名。"""
        signature = getattr(usable_cls, "get_signature", None)
        if callable(signature):
            resolved = signature()
            if resolved:
                return str(resolved)
        return str(usable_cls.__name__)

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
            from src.core.components.types import EventType
            from src.kernel.event import get_event_bus

            event_bus = get_event_bus()
            if event_bus.get_subscribers(EventType.BEFORE_ACTION_CALL):
                _, modified_params = await event_bus.publish(
                    EventType.BEFORE_ACTION_CALL,
                    {
                        "signature": signature,
                        "action_name": action_instance.action_name,
                        "action_description": getattr(
                            action_instance, "action_description", ""
                        ),
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
                from src.core.components.types import EventType
                from src.kernel.event import get_event_bus

                event_bus = get_event_bus()
                if event_bus.get_subscribers(EventType.AFTER_ACTION_CALL):
                    _, modified_params = await event_bus.publish(
                        EventType.AFTER_ACTION_CALL,
                        {
                            "signature": signature,
                            "action_name": action_instance.name,
                            "action_description": getattr(
                                action_instance, "description", ""
                            ),
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
                from src.core.components.types import EventType
                from src.kernel.event import get_event_bus

                event_bus = get_event_bus()
                if event_bus.get_subscribers(EventType.ON_ACTION_CALL_FAILED):
                    await event_bus.publish(
                        EventType.ON_ACTION_CALL_FAILED,
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

    def clear_schema_cache(self, signature: str | None = None) -> None:
        """清除 schema 缓存。

        Args:
            signature: 要清除的 Action 签名，None 表示清除全部
        """
        if signature:
            self._schema_cache.pop(signature, None)
        else:
            self._schema_cache.clear()


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