"""Agent 组件基类。

本模块提供 BaseAgent 类，定义 Agent 组件的基础行为。
Agent 是 Chatter 的任务协助者，拥有专属的私有 usables 套件。
Agent 只能调用自身 usables 中声明的组件，不可访问全局组件注册表。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Any, TYPE_CHECKING, cast

from src.core.components.types import ChatType
from src.core.components.utils import parse_function_signature
from src.kernel.llm import LLMUsable, LLMRequest, LLMPayload, LLMUsableExecution, ROLE
from src.kernel.logger import get_logger

logger = get_logger("agent")

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.prompt import SystemReminderBucket
    from src.core.models.message import Message
    from src.kernel.llm import LLMContextManager, ModelSet

# 类型别名：支持直接传类或传组件签名字符串
UsableReference = type[LLMUsable] | str


def _strip_usable_prefix(name: str) -> str:
    """去除 usable schema 常见前缀，返回可用于别名匹配的名称。

    支持 ``tool-`` / ``action-`` / ``agent-`` 三类前缀。

    Args:
        name: 原始 schema 名称。

    Returns:
        去除已知前缀后的名称；若无已知前缀则原样返回。
    """
    for prefix in ("tool-", "action-", "agent-"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


class BaseAgent(ABC, LLMUsable):
    """Agent 组件基类。

    Agent 是 Chatter 的任务协助者，具备更强的任务执行能力。
    与 Tool 不同，Agent 可以编排自身专属 usables 来完成复杂任务，
    但其可调用范围严格限制为类属性 usables 中声明的组件。

    Class Attributes:
        agent_name: Agent 名称
        agent_description: Agent 描述
        chatter_allow: 允许调用的 Chatter 名称列表
        chat_type: 支持的聊天类型
        associated_platforms: 关联的平台列表
        associated_types: 需要的内容类型列表
        dependencies: 组件级依赖（签名列表）
        usables: Agent 专属可调用组件类列表（私有，不进入全局注册表）
    """

    _plugin_: str
    _signature_: str

    agent_name: str = ""
    agent_description: str = ""

    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL

    associated_platforms: list[str] = []
    associated_types: list[str] = []

    dependencies: list[str] = []
    usables: list[UsableReference] = []  # 支持类或组件签名字符串

    def __init__(self, stream_id: str, plugin: "BasePlugin") -> None:
        """初始化 Agent 组件。

        Args:
            stream_id: 聊天流 ID
            plugin: 所属插件实例
        """
        self.stream_id = stream_id
        self.plugin = plugin

    @classmethod
    def get_signature(cls) -> str | None:
        """获取 Agent 组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:agent:agent_name"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.agent_name:
            return f"{cls._plugin_}:agent:{cls.agent_name}"
        return None

    @abstractmethod
    async def execute(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]:
        """执行 Agent 的核心逻辑。"""
        ...

    def _wrap_execute(self, *args: Any, **kwargs: Any) -> LLMUsableExecution:
        """包装 ``execute``，供统一 tool call 调度器使用。

        ``execute`` 可以保持普通 coroutine 写法并返回 ``(success, result)``；
        也可以写成异步生成器，在准备完成后 ``yield None`` 暂停，最后一次
        非空 ``yield`` 作为返回结果。

        Args:
            *args: 传给 ``execute`` 的位置参数。
            **kwargs: 传给 ``execute`` 的关键字参数。

        Returns:
            LLMUsableExecution: 已启动的执行包装对象。
        """
        return LLMUsableExecution(self.execute(*args, **kwargs))

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """生成 LLM Tool Schema。"""
        return parse_function_signature(cls.execute, f"agent-{cls.agent_name}", cls.agent_description)

    async def go_activate(self) -> bool:
        """Agent 激活判定函数。"""
        return True

    @classmethod
    def get_local_usables(cls) -> list[type[LLMUsable]]:
        """获取 Agent 私有 usables。

        自动解析 usables 中的组件签名字符串，从全局注册表获取对应的类。

        Returns:
            list[type[LLMUsable]]: Agent 私有组件类列表
        """
        from src.core.components.registry import get_global_registry

        resolved_usables: list[type[LLMUsable]] = []
        registry = get_global_registry()

        for usable_ref in cls.usables:
            if isinstance(usable_ref, str):
                # 字符串签名：从注册表解析
                component_cls = registry.get(usable_ref)
                if component_cls is None:
                    logger.warning(
                        f"Agent '{cls.agent_name}' 引用的组件签名 '{usable_ref}' "
                        f"未在注册表中找到，跳过该 usable"
                    )
                    continue
                if not issubclass(component_cls, LLMUsable):
                    logger.warning(
                        f"Agent '{cls.agent_name}' 引用的组件 '{usable_ref}' "
                        f"不是 LLMUsable 子类，跳过该 usable"
                    )
                    continue
                resolved_usables.append(component_cls)  # type: ignore
            else:
                # 直接传入的类
                resolved_usables.append(usable_ref)

        return resolved_usables

    @classmethod
    def get_local_usable_schemas(cls) -> list[dict[str, Any]]:
        """获取 Agent 私有 usables 的 schema 列表。"""
        schemas: list[dict[str, Any]] = []
        for usable_cls in cls.get_local_usables():
            schemas.append(usable_cls.to_schema())
        return schemas

    def create_llm_request(
        self,
        model_set: "ModelSet",
        request_name: str = "",
        context_manager: "LLMContextManager | None" = None,
        with_usables: bool = False,
        with_reminder: str | SystemReminderBucket | None = None,
    ) -> LLMRequest:
        """快速创建 LLMRequest 对象。

        Args:
            model_set: 模型配置集（调用方必须显式传入）
            request_name: 请求名称
            context_manager: 上下文管理器
            with_usables: 是否自动注入 Agent 私有 usables 到 TOOL payload
            with_reminder: 可选的 system reminder bucket；传入后会自动登记到上下文管理器

        Returns:
            LLMRequest: LLM 请求对象
        """
        request = LLMRequest(
            model_set=model_set,
            request_name=request_name,
            context_manager=context_manager,
        )

        if with_reminder is not None and request.context_manager is not None:
            from src.core.prompt import get_system_reminder_store

            reminder_items = get_system_reminder_store().get_items(with_reminder)
            for reminder_item in reminder_items:
                request.context_manager.reminder(
                    reminder_item.render(),
                    insert_type=reminder_item.insert_type,
                    wrap_with_system_tag=True,
                )

        if with_usables:
            request.add_payload(LLMPayload(ROLE.TOOL, cast(list[Any], self.get_local_usables())))

        return request

    async def execute_local_usable(
        self,
        usable_name: str,
        message: "Message | None" = None,
        **kwargs: Any,
    ) -> tuple[bool, Any]:
        """执行 Agent 私有 usable。

        仅在当前 Agent 的私有 usables 范围内查找，不访问全局注册表。
        找到组件后会委托统一执行器，因此同时支持 coroutine execute 和
        “最后一次非空 yield 为返回值”的异步生成器 execute。

        Args:
            usable_name: usable 名称，可传 schema.function.name 或去掉前缀后的短名。
            message: 当前消息；Action 会用它恢复发送上下文。
            **kwargs: 传递给 usable ``execute`` 的关键字参数。

        Returns:
            tuple[bool, Any]: ``(是否执行成功, 返回结果)``。

        Raises:
            ValueError: ``usable_name`` 不在私有 usables 中，或组件类型不支持。
        """
        local_index: dict[str, type[LLMUsable]] = {}
        for usable_cls in self.get_local_usables():
            schema = usable_cls.to_schema()
            function_schema = schema.get("function", {})
            name = function_schema.get("name")
            if isinstance(name, str) and name:
                local_index[name] = usable_cls
                local_index[_strip_usable_prefix(name)] = usable_cls

        usable_cls = local_index.get(usable_name)
        if not usable_cls:
            raise ValueError(f"Agent 私有 usable 不存在: {usable_name}")

        from src.core.utils.llm_tool_call import exec_llm_usable

        return await exec_llm_usable(
            usable_cls,
            plugin=self.plugin,
            stream_id=self.stream_id,
            message=message,
            kwargs=kwargs,
        )
