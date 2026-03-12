"""Action 管理器。

本模块提供 Action 管理器，负责 Action 组件的注册、发现、激活判定和执行。
Action 是"主动的响应"，通过 LLM Tool Calling 调用。
管理器维护 Action 组件的全局集合，并根据聊天上下文过滤可用的 Action。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger
from src.kernel.llm import LLMUsable
from src.kernel.concurrency import get_task_manager

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType

if TYPE_CHECKING:
    from src.core.components.base.action import BaseAction
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream, StreamContext


logger = get_logger("action_manager")


class ActionManager:
    """Action 管理器。

    负责管理所有 Action 组件，提供查询、过滤和执行接口。
    根据 ChatType、Chatter 名称等条件过滤可用的 Action。

    Attributes:
        _schema_cache: Action schema 缓存

    Examples:
        >>> manager = ActionManager()
        >>> actions = manager.get_actions_for_chat(
        ...     chat_type=ChatType.PRIVATE,
        ...     chatter_name="my_chatter"
        ... )
        >>> schema = manager.get_action_schema("my_plugin:action:send_message")
    """

    def __init__(self) -> None:
        """初始化 Action 管理器。"""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    def get_all_actions(self) -> dict[str, type["BaseAction"]]:
        """获取所有已注册的 Action 组件。

        Returns:
            dict[str, type[BaseAction]]: 将签名映射到 Action 类的字典

        Examples:
            >>> actions = manager.get_all_actions()
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.ACTION)

    def get_actions_for_plugin(self, plugin_name: str) -> dict[str, type["BaseAction"]]:
        """获取指定插件的所有 Action 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseAction]]: 将签名映射到 Action 类的字典

        Examples:
            >>> actions = manager.get_actions_for_plugin("my_plugin")
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.ACTION)

    def get_actions_for_chat(
        self,
        chat_type: ChatType = ChatType.ALL,
        chatter_name: str = "",
        platform: str = "",
    ) -> list[type[LLMUsable]]:
        """获取适用于特定聊天上下文的 Action 组件列表。

        根据 ChatType、Chatter 名称和平台过滤 Action。

        Args:
            chat_type: 聊天类型（私聊/群聊/全部）
            chatter_name: Chatter 名称（空字符串表示不限制）
            platform: 平台名称（空字符串表示不限制）

        Returns:
            list[type[LLMUsable]]: 可用的 Action 类列表

        Examples:
            >>> actions = manager.get_actions_for_chat(
            ...     chat_type=ChatType.PRIVATE,
            ...     chatter_name="my_chatter"
            ... )
        """
        all_actions = self.get_all_actions()
        filtered_actions = []

        for signature, action_cls in all_actions.items():
            # 检查 chat_type 兼容性
            if (
                action_cls.chat_type != ChatType.ALL
                and action_cls.chat_type != chat_type
            ):
                continue

            # 检查 chatter_allow
            if chatter_name and action_cls.chatter_allow:
                if chatter_name not in action_cls.chatter_allow:
                    continue

            # 检查平台关联
            if platform and action_cls.associated_platforms:
                if platform not in action_cls.associated_platforms:
                    continue

            filtered_actions.append(action_cls)

        logger.debug(
            f"为聊天上下文筛选 Action: chat_type={chat_type.value}, "
            f"chatter={chatter_name}, platform={platform}, "
            f"结果: {len(filtered_actions)}/{len(all_actions)}"
        )

        return filtered_actions

    def get_action_class(self, signature: str) -> type["BaseAction"] | None:
        """通过签名获取 Action 类。

        Args:
            signature: Action 组件签名

        Returns:
            type[BaseAction] | None: Action 类，如果未找到则返回 None

        Examples:
            >>> action_cls = manager.get_action_class("my_plugin:action:send_message")
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
        chat_type: ChatType = ChatType.ALL,
        chatter_name: str = "",
        platform: str = "",
    ) -> list[dict[str, Any]]:
        """获取适用于特定聊天上下文的所有 Action Schema。

        Args:
            chat_type: 聊天类型
            chatter_name: Chatter 名称
            platform: 平台名称

        Returns:
            list[dict[str, Any]]: Action schema 列表

        Examples:
            >>> schemas = manager.get_action_schemas(
            ...     chat_type=ChatType.PRIVATE,
            ...     chatter_name="my_chatter"
            ... )
        """
        actions = self.get_actions_for_chat(chat_type, chatter_name, platform)
        schemas = []

        for action_cls in actions:
            # 构建签名
            signature = self._build_signature(action_cls)  # type: ignore
            schema = self.get_action_schema(signature)
            if schema:
                schemas.append(schema)

        return schemas

    async def execute_action(
        self,
        signature: str,
        plugin: "BasePlugin",
        message: "Message",
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """执行 Action。

        创建 Action 实例并调用其 execute 方法。

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

        Examples:
            >>> success, result = await manager.execute_action(
            ...     "my_plugin:action:send_message",
            ...     plugin,
            ...     message,
            ...     content="你好"
            ... )
        """
        action_cls = self.get_action_class(signature)
        if not action_cls:
            raise ValueError(f"Action 类未找到: {signature}")

        # 获取或创建 ChatStream（使用 StreamManager）
        from src.core.managers.stream_manager import get_stream_manager

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

        # 剥离 LLM 自动注入的 reason 参数，避免传入 execute() 时签名不匹配
        kwargs.pop("reason", None)

        # 执行 Action
        try:
            result = await action_instance.execute(**kwargs)
            return result
        except Exception as e:
            logger.error(
                f"执行 Action 失败 ({signature}): {e}",
                exc_info=True,
            )
            raise RuntimeError(f"Action 执行失败: {e}") from e

    def clear_schema_cache(self, signature: str | None = None) -> None:
        """清除 schema 缓存。

        Args:
            signature: 要清除的 Action 签名，None 表示清除全部

        Examples:
            >>> # 清除特定 Action 的缓存
            >>> manager.clear_schema_cache("my_plugin:action:send_message")
            >>> # 清除全部缓存
            >>> manager.clear_schema_cache()
        """
        if signature:
            self._schema_cache.pop(signature, None)
        else:
            self._schema_cache.clear()

    async def modify_actions(
        self,
        stream_id: str,
        message_content: str = "",
    ) -> list[str]:
        """修改动作列表，根据上下文过滤和激活动作。

        这是主方法，协调多个阶段的动作过滤和激活判定：
        1. 检查动作的关联类型（associated_types）
        2. 调用 go_activate 方法进行激活判定

        Args:
            stream_id: 聊天流 ID
            message_content: 当前消息内容，用于激活判定

        Returns:
            list[str]: 最终可用的动作签名列表

        Examples:
            >>> available_actions = await manager.modify_actions(
            ...     stream_id=stream.stream_id,
            ...     message_content="你好"
            ... )
        """
        logger.debug(f"[{stream_id}] 开始动作修改流程")

        from src.core.managers.stream_manager import get_stream_manager

        chat_stream = await get_stream_manager().get_or_create_stream(
            stream_id=stream_id
        )
        # 获取所有动作类
        all_actions = self.get_all_actions()
        removals: list[tuple[str, str]] = []

        # 第二阶段：检查关联类型
        type_mismatched = self._check_action_associated_types(
            all_actions, chat_stream.context
        )
        removals.extend(type_mismatched)

        # 第三阶段：激活判定
        deactivated = await self._get_deactivated_actions_by_type(
            all_actions, chat_stream, message_content
        )
        removals.extend(deactivated)

        # 构建最终可用动作列表
        available_actions = []
        for signature in all_actions.keys():
            if not any(r[0] == signature for r in removals):
                available_actions.append(signature)

        # 日志记录
        if removals:
            removals_summary = " | ".join(
                [f"{name}({reason})" for name, reason in removals]
            )
            logger.info(f"[{stream_id}] 移除动作: {removals_summary}")

        available_text = "、".join(available_actions) if available_actions else "无"
        logger.info(f"[{chat_stream.stream_id}] 可用动作: {available_text}")

        return available_actions

    def _check_action_associated_types(
        self,
        all_actions: dict[str, type["BaseAction"]],
        chat_context: "StreamContext",
    ) -> list[tuple[str, str]]:
        """检查动作的关联类型。

        Args:
            all_actions: 所有动作类字典
            chat_context: 聊天流上下文

        Returns:
            list[tuple[str, str]]: 需要移除的 (动作签名, 原因) 列表

        Examples:
            >>> removals = manager._check_action_associated_types(
            ...     actions, context
            ... )
        """
        type_mismatched: list[tuple[str, str]] = []

        for signature, action_cls in all_actions.items():
            if action_cls.associated_types:
                if not chat_context.check_types(action_cls.associated_types):
                    types_str = ", ".join(action_cls.associated_types)
                    reason = f"适配器不支持（需要: {types_str}）"
                    type_mismatched.append((signature, reason))
                    logger.debug(f"[移除动作] {signature}：{reason}")

        return type_mismatched

    async def _get_deactivated_actions_by_type(
        self,
        actions_dict: dict[str, type["BaseAction"]],
        chat_stream: "ChatStream",
        message_content: str = "",
    ) -> list[tuple[str, str]]:
        """根据激活类型判定返回需要停用的动作列表。

        并行调用每个 Action 的 go_activate 方法进行激活判定。

        Args:
            actions_dict: 动作字典
            chat_stream: 聊天流实例
            message_content: 消息内容

        Returns:
            list[tuple[str, str]]: 需要停用的 (动作签名, 原因) 列表

        Examples:
            >>> deactivated = await manager._get_deactivated_actions_by_type(
            ...     actions, stream, "你好"
            ... )
        """
        from src.core.managers import get_plugin_manager

        deactivated_actions: list[tuple[str, str]] = []
        plugin_manager = get_plugin_manager()

        # 创建并行任务列表
        tasks = []
        signatures = []

        for signature, action_cls in actions_dict.items():
            # 从签名中提取 plugin_name（格式：plugin_name:component_type:component_name）
            parts = signature.split(":")
            if len(parts) < 3:
                logger.warning(f"无效的 Action 签名格式: {signature}，跳过")
                continue

            plugin_name = parts[0]

            # 获取真实的 plugin 实例
            plugin = plugin_manager.get_plugin(plugin_name)
            if not plugin:
                logger.warning(
                    f"未找到 Plugin 实例: {plugin_name}，跳过 Action: {signature}"
                )
                deactivated_actions.append(
                    (signature, f"未找到 Plugin 实例: {plugin_name}")
                )
                continue

            # 创建 Action 实例
            try:
                action_instance = action_cls(chat_stream=chat_stream, plugin=plugin)
                # 设置消息内容供 go_activate 使用
                action_instance._last_message = message_content

                # 创建 go_activate 任务
                task = action_instance.go_activate()
                tasks.append(task)
                signatures.append(signature)

            except Exception as e:
                logger.error(f"创建 Action 实例 {signature} 失败: {e}")
                deactivated_actions.append((signature, f"创建实例失败: {e}"))

        # 并行执行所有激活判断
        if tasks:
            logger.debug(
                f"[{chat_stream.stream_id}] 并行执行激活判断，任务数: {len(tasks)}"
            )
            try:
                results = await get_task_manager().gather(
                    *tasks, return_exceptions=True
                )

                # 处理结果
                for signature, result in zip(signatures, results, strict=False):
                    if isinstance(result, Exception):
                        logger.error(
                            f"[{chat_stream.stream_id}] 激活判断 {signature} 时出错: {result}"
                        )
                        deactivated_actions.append(
                            (signature, f"激活判断出错: {result}")
                        )
                    elif not result:
                        # go_activate 返回 False，不激活
                        deactivated_actions.append(
                            (signature, "go_activate 返回 False")
                        )
                        logger.debug(
                            f"[{chat_stream.stream_id}] 未激活动作: {signature}"
                        )
                    else:
                        # go_activate 返回 True，激活
                        logger.debug(f"[{chat_stream.stream_id}] 激活动作: {signature}")

            except Exception as e:
                logger.error(f"[{chat_stream.stream_id}] 并行激活判断失败: {e}")
                # 如果并行执行失败，将所有动作标记为不激活
                deactivated_actions.extend(
                    (sig, f"并行判断失败: {e}") for sig in signatures
                )

        return deactivated_actions

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

    Examples:
        >>> manager = get_action_manager()
        >>> actions = manager.get_actions_for_chat(ChatType.PRIVATE)
    """
    global _global_action_manager
    if _global_action_manager is None:
        _global_action_manager = ActionManager()
    return _global_action_manager
