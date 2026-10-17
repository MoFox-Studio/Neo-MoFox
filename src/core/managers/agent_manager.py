"""Agent 管理器。

本模块提供 AgentManager，负责 Agent 组件的注册查询、筛选与执行。
Agent 是 Chatter 的任务协助者，拥有专属的私有 usables 套件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType
from src.core.components.utils import should_strip_auto_reason_argument
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.plugin import BasePlugin
    from src.kernel.llm import LLMUsable


logger = get_logger("agent_manager")


class AgentManager:
    """Agent 管理器。

    负责管理所有 Agent 组件，提供查询、筛选和执行接口。
    筛选仅针对传入的组件类列表进行，不从聊天流或全局注册表获取组件。
    """

    def get_all_agents(self) -> dict[str, type["BaseAgent"]]:
        """获取所有已注册的 Agent 组件。

        Returns:
            dict[str, type[BaseAgent]]: Agent 签名到类的映射
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.AGENT)

    def get_agents_for_plugin(self, plugin_name: str) -> dict[str, type["BaseAgent"]]:
        """获取指定插件的所有 Agent 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseAgent]]: Agent 签名到类的映射
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.AGENT)

    def get_agent_class(self, signature: str) -> type["BaseAgent"] | None:
        """通过签名获取 Agent 类。

        Args:
            signature: Agent 组件签名

        Returns:
            type[BaseAgent] | None: Agent 类，未找到则返回 None
        """
        registry = get_global_registry()
        return registry.get(signature)

    def get_agent_schema(self, signature: str) -> dict[str, Any] | None:
        """获取 Agent 的 Tool Schema。

        Args:
            signature: Agent 组件签名

        Returns:
            dict[str, Any] | None: Tool Schema，未找到则返回 None
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            return None
        return agent_cls.to_schema()

    def get_agent_schemas(
        self,
        component_classes: list[type["LLMUsable"]],
    ) -> list[dict[str, Any]]:
        """获取组件类列表对应的 Agent Schema 列表。

        Args:
            component_classes: 已筛选的 Agent 组件类列表

        Returns:
            list[dict[str, Any]]: Tool Schema 列表
        """
        schemas = []
        for agent_cls in component_classes:
            schema = agent_cls.to_schema()
            if schema:
                schemas.append(schema)
        return schemas

    async def filter_agents(
        self,
        component_classes: list[type["LLMUsable"]],
        *,
        stream_id: str = "",
        chatter_name: str = "",
        chatter_signature: str = "",
        chat_type: ChatType = ChatType.ALL,
        platform: str = "",
    ) -> list[type["LLMUsable"]]:
        """筛选传入的 Agent 组件类列表。

        先经统一入口（发布 ``BEFORE_AGENT_FILTER`` 事件 + 通用静态过滤），
        再执行 Agent 特有的 ``go_activate`` 激活判定。

        Args:
            component_classes: 待筛选的 Agent 组件类列表
            stream_id: 聊天流 ID
            chatter_name: 聊天器名称
            chatter_signature: 聊天器签名
            chat_type: 聊天类型
            platform: 平台名称

        Returns:
            筛选后的 Agent 组件类列表
        """
        from src.core.components.types import EventType
        from src.core.managers.utils.filtering import filter_component_classes

        filtered = await filter_component_classes(
            component_classes,
            event_type=EventType.BEFORE_AGENT_FILTER,
            stream_id=stream_id,
            chatter_name=chatter_name,
            chatter_signature=chatter_signature,
            chat_type=chat_type,
            platform=platform,
        )

        if not filtered or not stream_id:
            return filtered

        return await self._apply_agent_activation(filtered, stream_id=stream_id)

    async def _apply_agent_activation(
        self,
        component_classes: list[type["LLMUsable"]],
        *,
        stream_id: str,
    ) -> list[type["LLMUsable"]]:
        """执行 Agent 特有的 ``go_activate`` 激活判定。

        Args:
            component_classes: 已通过通用静态过滤的 Agent 组件类列表
            stream_id: 聊天流 ID

        Returns:
            激活判定后的 Agent 组件类列表
        """
        from src.core.managers import get_plugin_manager, get_stream_manager
        from src.core.managers.utils.filtering import (
            create_component_instance,
            resolve_component_plugin,
        )

        chat_stream = await get_stream_manager().get_or_create_stream(
            stream_id=stream_id
        )

        removals: list[tuple[str, str]] = []
        plugin_manager = get_plugin_manager()
        tasks = []
        task_signatures = []

        for usable_cls in component_classes:
            signature = self._component_signature(usable_cls)

            plugin = resolve_component_plugin(plugin_manager, usable_cls, signature)
            if plugin is None:
                logger.warning(f"未找到 Plugin 实例，跳过激活判定: {signature}")
                continue

            try:
                instance = create_component_instance(
                    usable_cls, plugin, chat_stream, stream_id
                )
                go_activate = getattr(instance, "go_activate", None)
                if not callable(go_activate):
                    continue
                tasks.append(go_activate())
                task_signatures.append(signature)
            except Exception as e:
                logger.error(f"创建 Agent 实例 {signature} 失败: {e}")
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
            logger.info(f"[{stream_id}] 移除 Agent: {summary}")

        removal_names = {name for name, _ in removals}
        available = [
            usable_cls
            for usable_cls in component_classes
            if self._component_signature(usable_cls) not in removal_names
        ]
        logger.info(
            f"[{stream_id}] 可用 Agent: {len(available)}/{len(component_classes)}"
        )

        return available

    @staticmethod
    def _component_signature(usable_cls: type["LLMUsable"]) -> str:
        """获取组件签名，无法获取时回退到类名。"""
        signature = getattr(usable_cls, "get_signature", None)
        if callable(signature):
            resolved = signature()
            if resolved:
                return str(resolved)
        return str(usable_cls.__name__)

    async def execute_agent(
        self,
        signature: str,
        plugin: "BasePlugin",
        stream_id: str,
        **kwargs: Any,
    ) -> tuple[bool, str | dict]:
        """执行 Agent。创建 Agent 实例并调用其 execute 方法。

        Args:
            signature: Agent 组件签名
            plugin: 插件实例
            stream_id: 聊天流 ID
            **kwargs: 传递给 Agent 的参数

        Returns:
            tuple[bool, str | dict]: 执行是否成功与结果描述

        Raises:
            ValueError: 参数非法或 Agent 类未找到
            RuntimeError: Agent 执行失败
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            raise ValueError(f"Agent 类未找到: {signature}")

        agent_instance = agent_cls(stream_id=stream_id, plugin=plugin)

        if should_strip_auto_reason_argument(agent_instance.execute, kwargs):
            kwargs.pop("reason", None)

        try:
            result = await agent_instance._wrap_execute(**kwargs).wait_done()
            return result
        except Exception as e:
            logger.error(
                f"执行 Agent 失败 ({signature}): {e}",
                exc_info=True,
            )
            raise RuntimeError(f"Agent 执行失败: {e}") from e

    def get_agent_usables(self, signature: str) -> list[type["LLMUsable"]]:
        """获取 Agent 的专属 usables 列表。

        Args:
            signature: Agent 组件签名

        Returns:
            Agent 专属的 usables 类列表
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            return []
        return agent_cls.get_local_usables()

    def get_agent_usable_schemas(self, signature: str) -> list[dict[str, Any]]:
        """获取 Agent 专属 usables 的 Schema 列表。

        Args:
            signature: Agent 组件签名

        Returns:
            usables 的 Tool Schema 列表
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            return []
        return agent_cls.get_local_usable_schemas()

    async def execute_agent_usable(
        self,
        signature: str,
        plugin: "BasePlugin",
        stream_id: str,
        usable_name: str,
        **kwargs: Any,
    ) -> tuple[bool, Any]:
        """执行 Agent 的专属 usable。

        Args:
            signature: Agent 组件签名
            plugin: 插件实例
            stream_id: 聊天流 ID
            usable_name: usable 名称
            **kwargs: 传递给 usable 的参数

        Returns:
            tuple[bool, Any]: 执行是否成功与结果

        Raises:
            ValueError: 参数非法或 Agent 类未找到
            RuntimeError: Agent usable 执行失败
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            raise ValueError(f"Agent 类未找到: {signature}")

        agent_instance = agent_cls(stream_id=stream_id, plugin=plugin)

        try:
            result = await agent_instance.execute_local_usable(
                usable_name=usable_name,
                **kwargs,
            )
            return result
        except Exception as e:
            logger.error(
                f"执行 Agent usable 失败 ({signature}.{usable_name}): {e}",
                exc_info=True,
            )
            raise RuntimeError(f"Agent usable 执行失败: {e}") from e


# 全局 Agent 管理器实例
_global_agent_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    """获取全局 Agent 管理器实例。

    Returns:
        AgentManager: 全局 Agent 管理器单例
    """
    global _global_agent_manager
    if _global_agent_manager is None:
        _global_agent_manager = AgentManager()
    return _global_agent_manager