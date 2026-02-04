"""动作组件基类。

本模块提供 BaseAction 类，定义动作组件的基本行为。
动作是"主动的响应"，通过 LLM Tool Calling 调用。
"""

import random
from abc import ABC, abstractmethod
from typing import Annotated, Any, TYPE_CHECKING

from src.core.components.types import ChatType
from src.core.components.utils import parse_function_signature

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream
    from src.kernel.llm.payload.tooling import LLMUsable


class BaseAction(ABC, LLMUsable):
    """动作组件基类。

    动作定义了一个"动作"的行为，例如"发送消息"、"发送表情包"等。
    它是决策后的"结果"，LLM 并不会从中获得信息。
    动作是主动的"响应"。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        action_name: 动作名称
        action_description: 动作的功能描述
        primary_action: 是否为主动作
        chatter_allow: 支持的 Chatter 列表
        chat_type: 支持的聊天类型
        associated_platforms: 关联的平台列表
        associated_types: 需要的内容类型列表

    Examples:
        >>> class SendEmoji(BaseAction):
        ...     action_name = "send_emoji"
        ...     action_description = "发送一个表情"
        ...     primary_action = False
        ...
        ...     async def execute(self, emoji_tag: str) -> tuple[bool, str]:
        ...         # 实现逻辑
        ...         return True, "发送成功"
    """

    # 所属插件名称（由 PluginManager 在注册时注入）
    plugin_name: str = "unknown_plugin"

    # 动作元数据
    action_name: str = ""
    action_description: str = ""

    primary_action: bool = False
    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL

    associated_platforms: list[str] = []
    associated_types: list[str] = []

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:tool:calculator"]

    def __init__(self, chat_stream: "ChatStream", plugin: "BasePlugin") -> None:
        """初始化动作组件。

        Args:
            chat_stream: 聊天流实例
            plugin: 所属插件实例
        """
        self.chat_stream = chat_stream
        self.plugin = plugin
        self._last_message: str | None = None
    
    @classmethod
    def get_signature(cls) -> str | None:
        """获取动作组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:action:action_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = SendEmoji.get_signature()
            >>> "my_plugin:action:send_emoji"
        """
        return f"{cls.plugin_name}:action:{cls.action_name}" if cls.plugin_name != "unknown_plugin" else None
    
    @abstractmethod
    async def execute(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str, "结果详情"]]:
        """执行动作的主要逻辑。

        所有 Action 组件都必须重写此方法。
        必须编写参数文档来告诉 LLM 每个参数的作用。

        Returns:
            tuple[bool, str]: (是否成功, 结果详情)

        Examples:
            >>> async def execute(
            ...     self,
            ...     emoji_tag: Annotated[str, "表情的情感标签"]
            ... ) -> tuple[bool, str]:
            ...     # 实现发送表情逻辑
            ...     return True, "发送成功"

        Note:
            action 管理器会自动识别类型提示和文档字符串并生成对应的 Tool Schema。
        """
        ...

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """生成 LLM Tool Schema。

        通过 inspect 解析 execute 方法生成 OpenAI Tool Calling 格式的 schema。

        Returns:
            dict[str, Any]: OpenAI Tool 格式的 schema

        Examples:
            >>> schema = SendEmoji.to_schema()
            >>> {
            ...     "type": "function",
            ...     "function": {
            ...         "name": "send_emoji",
            ...         "description": "发送一个表情",
            ...         "parameters": {
            ...             "type": "object",
            ...             "properties": {
            ...                 "emoji_tag": {"type": "string", "description": "..."}
            ...             },
            ...             "required": ["emoji_tag"]
            ...         }
            ...     }
            ... }
        """
        # 使用 utils 中的共同方法生成 schema
        return parse_function_signature(cls.execute, cls.action_name, cls.action_description)

    async def go_activate(self) -> bool:
        """动作激活判定函数。

        子类可重写此方法以实现自定义的激活逻辑。

        Returns:
            bool: 是否激活

        Examples:
            >>> async def go_activate(self) -> bool:
            ...     return await self._random_activation(0.5)
        """
        return True

    async def _random_activation(self, probability: float) -> bool:
        """随机激活工具函数。

        Args:
            probability: 激活概率，范围 0.0 到 1.0

        Returns:
            bool: 是否激活

        Examples:
            >>> if await self._random_activation(0.5):
            ...     print("有50%概率激活")
        """
        return random.random() < probability

    async def _keyword_match(
        self,
        keywords: list[str],
        case_sensitive: bool = False,
    ) -> bool:
        """关键词匹配工具函数。

        聊天内容会自动从实例属性中获取。

        Args:
            keywords: 关键词列表
            case_sensitive: 是否区分大小写

        Returns:
            bool: 是否匹配到关键词

        Examples:
            >>> if await self._keyword_match(["hello", "hi"]):
            ...     print("匹配到问候语")
        """
        if not self._last_message:
            return False

        message = self._last_message if case_sensitive else self._last_message.lower()
        keywords = keywords if case_sensitive else [k.lower() for k in keywords]

        return any(kw in message for kw in keywords)

    async def _llm_judge_activation(
        self,
        judge_prompt: str = "",
        action_require: list[str] | None = None,
    ) -> bool:
        """LLM 判断激活工具函数。

        使用 action manager 中的 action modifier 来统一判断是否应该激活此 Action。

        Args:
            judge_prompt: 判断用提示词
            action_require: 强调的激活需求列表

        Returns:
            bool: LLM 判定是否激活

        Note:
            此方法需要 action_manager 支持，当前返回 False
        """
        # TODO: 实现与 action_manager 的集成
        return False

    async def _send_to_stream(
        self,
        content: Any,
        stream_id: str | None = None,
    ) -> bool:
        """发送任意内容到指定聊天流。

        Args:
            content: 要发送的内容
            stream_id: 要发送的聊天流 ID，留空则使用当前聊天流

        Returns:
            bool: 发送是否成功

        Examples:
            >>> from src.core.models.message import TextContent
            >>> await self._send_to_stream(TextContent("Hello"))
            >>> True

        Note:
            此方法需要 transport 层支持，当前为占位实现
        """
        # TODO: 实现与 transport 层的集成
        return False
