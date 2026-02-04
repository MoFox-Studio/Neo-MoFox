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
        from src.core.components.types import ComponentType

        usables: list[type["LLMUsable"]] = []

        # 获取所有组件
        components = self.plugin.get_components()

        for component_cls in components:
            # 检查是否是 LLMUsable（Action、Tool、Collection）
            sig = getattr(component_cls, "__signature__", None)
            if sig:
                sig_parts = sig.split(":")
                if len(sig_parts) == 3:
                    comp_type = sig_parts[1]
                    if comp_type in (
                        ComponentType.ACTION.value,
                        ComponentType.TOOL.value,
                        ComponentType.COLLECTION.value,
                    ):
                        usables.append(component_cls)

        return usables

    async def modify_llm_usables(
        self, llm_usables: list[type["LLMUsable"]]
    ) -> list[type["LLMUsable"]]:
        """修改 LLMUsable 组件列表。

        子类可以重写此方法来过滤、排序或添加组件。

        Args:
            llm_usables: 原始 LLMUsable 组件列表

        Returns:
            list[type[LLMUsable]]: 修改后的组件列表

        Examples:
            >>> async def modify_llm_usables(self, llm_usables):
            ...     # 只保留特定组件
            ...     return [u for u in llm_usables if u.action_name != "blocked"]
        """
        return llm_usables

    async def pre_exec_llm_usables(
        self, llm_usables: list[type["LLMUsable"]], allow_primary_action: bool = True
    ) -> dict[str, Any]:
        """预执行 LLMUsable 组件（sub_actor 阶段）。

        在主要 LLM 调用之前，执行一些预处理的 LLM 调用。
        例如：激活判定、参数验证等。

        Args:
            llm_usables: LLMUsable 组件列表
            allow_primary_action: 是否允许主要动作

        Returns:
            dict[str, Any]: 预执行结果

        Examples:
            >>> async def pre_exec_llm_usables(self, llm_usables, allow_primary_action=True):
            ...     results = {}
            ...     for usable in llm_usables:
            ...         # 执行激活判定
            ...         if hasattr(usable, 'go_activate'):
            ...             instance = usable(self.plugin)
            ...             activated = await instance.go_activate()
            ...             results[usable.__name__] = activated
            ...     return results
        """
        return {}

    async def exec_llm_usables(
        self, llm_usables: list[type["LLMUsable"]]
    ) -> dict[str, Any]:
        """执行 LLMUsable 组件（actor 阶段）。

        执行主要的 LLM 调用和工具调用。

        Args:
            llm_usables: LLMUsable 组件列表

        Returns:
            dict[str, Any]: 执行结果

        Examples:
            >>> async def exec_llm_usables(self, llm_usables):
            ...     # 生成 schemas
            ...     schemas = [u.to_schema() for u in llm_usables]
            ...     # 调用 LLM
            ...     response = await self._call_llm_with_tools(schemas)
            ...     return {"response": response}
        """
        return {}
