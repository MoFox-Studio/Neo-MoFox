"""聊天器组件基类。

本模块提供 BaseChatter 类，定义聊天器组件的基本行为。
Chatter 是 Bot 的智能核心，定义对话逻辑和流程。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator

from src.core.components.types import ChatType

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.components.base.action import BaseAction
    from src.core.components.base.tool import BaseTool
    from src.core.components.base.collection import BaseCollection
    from src.core.models.message import Message
    from src.kernel.llm.payload.tooling import LLMUsable


@dataclass
class Wait:
    """等待结果。

    表示 Chatter 需要等待某些条件（如 LLM 响应）才能继续。

    Attributes:
        reason: 等待原因的描述
    """

    reason: str


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


# 类型别名
ChatterResult = Wait | Success | Failure


class BaseChatter(ABC):
    """聊天器组件基类。

    Chatter 定义 Bot 的对话逻辑和流程。
    使用生成器模式，通过 yield 返回 Wait/Success/Failure 结果。

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
        ...     async def execute(self, unreads: list[Message]) -> Generator[ChatterResult, None, None]:
        ...         yield Wait("等待 LLM 响应")
        ...         # 执行逻辑...
        ...         yield Success("完成")
    """

    # 所属插件名称（由 PluginManager 在注册时注入）
    plugin_name: str = "unknown_plugin"

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
        """获取动作组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:action:action_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = SendEmoji.get_signature()
            >>> "my_plugin:action:send_emoji"
        """
        return f"{cls.plugin_name}:chatter:{cls.chatter_name}" if cls.plugin_name != "unknown_plugin" else None
    
    @abstractmethod
    async def execute(
        self, unreads: list["Message"]
    ) -> Generator[ChatterResult, None, None]:
        """执行聊天器的主要逻辑。

        使用生成器模式，通过 yield 返回执行结果。

        Args:
            unreads: 未读消息列表

        Yields:
            ChatterResult: Wait/Success/Failure 结果

        Examples:
            >>> async def execute(self, unreads: list[Message]) -> Generator[ChatterResult, None, None]:
            ...     if not unreads:
            ...         yield Failure("没有新消息")
            ...         return
            ...
            ...     yield Wait("处理消息中")
            ...
            ...     # 执行 LLM 调用等操作
            ...     response = await self._call_llm(unreads)
            ...
            ...     yield Success(f"处理完成: {response}")
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
        from src.core.managers.collection_manager import get_collection_manager

        usables: list[type["LLMUsable"]] = []

        state_manager = get_global_state_manager()
        collection_manager = get_collection_manager()

        # 获取所有组件
        components = self.plugin.get_components()

        for component_cls in components:
            # 检查是否是 LLMUsable（Action、Tool、Collection）
            sig = getattr(component_cls, "__signature__", None)
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
                        if comp_type in (ComponentType.ACTION.value, ComponentType.TOOL.value):
                            if not collection_manager.is_component_available(sig, self.stream_id):
                                continue
                        usables.append(component_cls)

        return usables

    async def modify_llm_usables(
        self, llm_usables: list[type["BaseTool | BaseAction | BaseCollection"]]
    ) -> list[type["BaseTool | BaseAction | BaseCollection"]]:
        """修改 LLMUsable 组件列表。

        子类可以重写此方法来过滤、排序或添加组件。

        Args:
            llm_usables: 原始 LLMUsable 组件列表

        Returns:
            list[type["BaseTool" | "BaseAction" | "BaseCollection"]]: 修改后的组件列表

        Examples:
            >>> async def modify_llm_usables(self, llm_usables):
            ...     # 只保留特定组件
            ...     return [u for u in llm_usables if u.action_name != "blocked"]
        """
        return llm_usables

    async def exec_llm_usable(
        self,
        usable_cls: type["BaseTool | BaseAction | BaseCollection"],
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
        from src.core.components.base.action import BaseAction
        from src.core.components.base.tool import BaseTool
        from src.core.components.base.collection import BaseCollection
        from src.core.managers.collection_manager import get_collection_manager
        from src.core.managers.tool_manager.tool_use import get_tool_use
        from src.core.managers.action_manager import get_action_manager

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
