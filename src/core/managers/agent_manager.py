"""Agent 组件管理器。

本模块提供 AgentManager，负责 Agent 组件类的查询、作用域筛选、动态激活判定、
筛选前事件发布、schema 查询与执行，是 agent_api 的底层实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType, EventType
from src.core.components.utils import should_strip_auto_reason_argument
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

from src.core.managers.utils import (
    build_usable_static_context,
    filter_by_associated_types,
    publish_before_filter_event,
    static_filter_usables,
)

if TYPE_CHECKING:
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.chatter import BaseChatter
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream, StreamContext
    from src.kernel.llm import LLMUsable

logger = get_logger("agent_manager")


class AgentManager:
    """Agent 组件管理器。

    管理 Agent 组件类的注册查询、作用域筛选、动态激活判定、schema 查询与执行。
    筛选前发布 ``BEFORE_AGENT_FILTER`` 事件，允许外部事件处理器改写组件类集合。

    Examples:
        >>> manager = AgentManager()
        >>> agents = await manager.get_agents_for_chat(
        ...     usables=[AssistantAgent],
        ...     chat_type="private",
        ...     chatter_name="my_chatter",
        ...     stream_id="",
        ... )
    """

    def __init__(self) -> None:
        """初始化 Agent 组件管理器。"""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    # ── 查询 ──

    def get_all_agents(self) -> dict[str, type["BaseAgent"]]:
        """获取所有已注册的 Agent 组件类。

        Returns:
            dict[str, type[BaseAgent]]: 签名到 Agent 类的映射
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.AGENT)

    def get_agents_for_plugin(self, plugin_name: str) -> dict[str, type["BaseAgent"]]:
        """获取指定插件的所有 Agent 组件类。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseAgent]]: 签名到 Agent 类的映射
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

    # ── 筛选与激活 ──

    async def get_agents_for_chat(
        self,
        usables: list[type["LLMUsable"]] | None = None,
        *,
        chat_type: "ChatType | str" = "all",
        chatter_name: str = "",
        platform: str = "",
        stream_id: str = "",
        chat_stream: "ChatStream | None" = None,
        stream_context: "StreamContext | None" = None,
        chatter: "BaseChatter | None" = None,
    ) -> list[type["LLMUsable"]]:
        """获取适用于指定聊天上下文的 Agent 组件类列表。

        若传入 ``usables`` 则直接对给定集合筛选（默认筛选不传 stream_id、
        不获取流，仅按静态维度）；否则从注册表拉取全部 Agent。

        Args:
            usables: 待筛选的组件类列表；不传则取全量注册 Agent
            chat_type: 聊天类型（private / group / all）
            chatter_name: Chatter 名称
            platform: 平台标识
            stream_id: 聊天流 ID（空表示不按流筛选）
            chat_stream: 候选聊天流实例（提供后由其派生完整上下文）
            stream_context: 聊天流上下文（提供后按内容类型过滤）

        Returns:
            list[type[LLMUsable]]: 筛选后的 Agent 组件类列表
        """
        if isinstance(chat_type, ChatType):
            chat_type = chat_type.value

        if usables is None:
            usables = list(self.get_all_agents().values())

        usables = await publish_before_filter_event(
            EventType.BEFORE_AGENT_FILTER,
            component_kind="agent",
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

        removal_names = {name for name, _ in removals}
        available = [
            cls for cls in filtered if (cls.get_signature() or cls.__name__) not in removal_names  # type: ignore[attr-defined]
        ]

        if removals:
            summary = " | ".join(f"{n}({r})" for n, r in removals)
            logger.debug(f"移除 Agent 组件: {summary}")

        return available

    async def activate_agents_for_chat(
        self,
        agents: list[type["LLMUsable"]],
        *,
        chat_stream: "ChatStream",
        plugin: "BasePlugin | None" = None,
    ) -> list[type["LLMUsable"]]:
        """对 Agent 组件类执行动态激活判定（go_activate）。

        Args:
            agents: 待判定的 Agent 组件类列表
            chat_stream: 当前聊天流实例
            plugin: 所属插件实例（缺省时按组件签名解析）

        Returns:
            list[type[LLMUsable]]: 激活通过的组件类列表
        """
        tasks = []
        signatures = []
        kept = []

        for agent_cls in agents:
            signature = agent_cls.get_signature() or agent_cls.__name__  # type: ignore[attr-defined]
            # 优先按组件签名解析所属插件；无法解析时回退到传入的 plugin
            agent_plugin = self._resolve_agent_plugin(signature)
            if agent_plugin is None:
                agent_plugin = plugin
            if agent_plugin is None:
                logger.warning(f"未找到 Agent 所属插件实例: {signature}，跳过激活判定")
                continue
            try:
                instance = agent_cls(stream_id=chat_stream.stream_id, plugin=agent_plugin)  # type: ignore[call-arg]
                go_activate = getattr(instance, "go_activate", None)
                if not callable(go_activate):
                    kept.append(agent_cls)
                    continue
                tasks.append(go_activate())
                signatures.append(signature)
            except Exception as e:
                logger.error(f"创建 Agent 实例 {signature} 失败: {e}")
                continue

        if tasks:
            results = await get_task_manager().gather(*tasks, return_exceptions=True)
            for signature, result in zip(signatures, results, strict=False):
                if isinstance(result, Exception) or not result:
                    continue
                agent_cls = next(
                    (
                        c
                        for c in agents
                        if (c.get_signature() or c.__name__) == signature  # type: ignore[attr-defined]
                    ),
                    None,
                )
                if agent_cls is not None:
                    kept.append(agent_cls)

        return kept

    # ── Schema ──

    def get_agent_schema(self, signature: str) -> dict[str, Any] | None:
        """获取 Agent 的 Tool Schema。

        Args:
            signature: Agent 组件签名

        Returns:
            dict[str, Any] | None: Tool Schema，未找到则返回 None
        """
        if signature in self._schema_cache:
            return self._schema_cache[signature]

        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            return None
        schema = agent_cls.to_schema()
        self._schema_cache[signature] = schema
        return schema

    def get_agent_usables(self, signature: str) -> list[type["LLMUsable"]]:
        """获取 Agent 的专属 usables 列表。

        Args:
            signature: Agent 组件签名

        Returns:
            list[type[LLMUsable]]: Agent 专属的 usables 类列表
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
            list[dict[str, Any]]: usables 的 Tool Schema 列表
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            return []
        return agent_cls.get_local_usable_schemas()

    def clear_schema_cache(self, signature: str | None = None) -> None:
        """清除 schema 缓存。

        Args:
            signature: 要清除的 Agent 签名；None 表示清除全部
        """
        if signature:
            self._schema_cache.pop(signature, None)
        else:
            self._schema_cache.clear()

    # ── 执行 ──

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
            tuple[bool, str | dict]: (是否成功, 结果) 或 (是否成功, 错误消息)

        Raises:
            ValueError: Agent 类未找到时抛出
            RuntimeError: Agent 执行失败时抛出
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
            logger.error(f"执行 Agent 失败 ({signature}): {e}", exc_info=True)
            raise RuntimeError(f"Agent 执行失败: {e}") from e

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
            ValueError: Agent 类未找到时抛出
            RuntimeError: Agent usable 执行失败时抛出
        """
        agent_cls = self.get_agent_class(signature)
        if not agent_cls:
            raise ValueError(f"Agent 类未找到: {signature}")

        agent_instance = agent_cls(stream_id=stream_id, plugin=plugin)

        try:
            result = await agent_instance.execute_local_usable(usable_name=usable_name, **kwargs)
            return result
        except Exception as e:
            logger.error(f"执行 Agent usable 失败 ({signature}.{usable_name}): {e}", exc_info=True)
            raise RuntimeError(f"Agent usable 执行失败: {e}") from e

    # ── 内部 ──

    def _resolve_agent_plugin(self, signature: str) -> "BasePlugin | None":
        """根据组件签名解析其所属插件实例。

        Args:
            signature: Agent 组件签名

        Returns:
            BasePlugin | None: 解析到的插件实例；无法解析时返回 None
        """
        try:
            from src.core.managers import get_plugin_manager
            from src.core.components.types import parse_signature

            plugin_name = parse_signature(signature)["plugin_name"]
            return get_plugin_manager().get_plugin(plugin_name)
        except Exception:
            return None


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


__all__ = ["AgentManager", "get_agent_manager"]