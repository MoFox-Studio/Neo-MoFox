"""集合组件基类。

本模块提供 BaseCollection 类，定义集合组件的基本行为。
集合是 LLMUsable 的集合体，可包含多个 Action、Tool 或嵌套的 Collection。
"""

import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType
from src.kernel.llm.payload.tooling import LLMUsable

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


class BaseCollection(ABC, LLMUsable):
    """集合组件基类。

    集合是 LLMUsable 的集合体，可以包含多个 Action/Tool，甚至嵌套的 Collection。
    当 LLM 调用 Collection 时，会解包其内部组件。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        collection_name: 集合名称
        collection_description: 集合描述
        associated_platforms: 关联的平台列表
        chatter_allow: 支持的 Chatter 列表
        chat_type: 支持的聊天类型
        cover_go_activate: 是否覆盖内部组件的 go_activate 结果

    Examples:
        >>> class MyCollection(BaseCollection):
        ...     collection_name = "my_collection"
        ...     collection_description = "包含发送表情和时间命令的集合"
        ...
        ...     cover_go_activate = True
        ...
        ...     async def get_contents(self) -> list[str]:
        ...         return [
        ...             "my_plugin:action:send_emoji",
        ...             "my_plugin:command:time_command",
        ...         ]
    """

    # 所属插件名称（由 PluginManager 在注册时注入）
    plugin_name: str = "unknown_plugin"

    # 集合元数据
    collection_name: str = ""
    collection_description: str = ""

    associated_platforms: list[str] = []
    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL

    cover_go_activate: bool = True

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:tool:database"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化集合组件。

        Args:
            plugin: 所属插件实例
        """
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
        return f"{cls.plugin_name}:collection:{cls.collection_name}" if cls.plugin_name != "unknown_plugin" else None
    
    @abstractmethod
    async def get_contents(self) -> list[str]:
        """获取 Collection 内部包含的所有 LLMUsable 组件。

        Returns:
            list[str]: 包含的所有 LLMUsable 组件签名列表，格式："插件名:组件类型:组件名"

        Examples:
            >>> async def get_contents(self) -> list[str]:
            ...     return [
            ...         "my_plugin:action:send_emoji",
            ...         "my_plugin:command:time_command",
            ...     ]
        """
        ...

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """生成 LLM Tool Schema。

        Collection 的 schema 只是描述，不包含参数。

        Returns:
            dict[str, Any]: OpenAI Tool 格式的 schema

        Examples:
            >>> schema = MyCollection.to_schema()
            >>> {
            ...     "type": "function",
            ...     "function": {
            ...         "name": "my_collection",
            ...         "description": "包含发送表情和时间命令的集合",
            ...         "parameters": {
            ...             "type": "object",
            ...             "properties": {},
            ...             "required": []
            ...         }
            ...     }
            ... }
        """
        return {
            "type": "function",
            "function": {
                "name": cls.collection_name,
                "description": cls.collection_description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }

    async def go_activate(self) -> bool:
        """Collection 激活判定函数。

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

        Args:
            keywords: 关键词列表
            case_sensitive: 是否区分大小写

        Returns:
            bool: 是否匹配到关键词

        Examples:
            >>> if await self._keyword_match(["hello", "hi"]):
            ...     print("匹配到问候语")
        """
        # Collection 没有直接的 _last_message，需要从其他地方获取
        # 这里提供接口，子类可以自行实现
        return False

    async def _llm_judge_activation(
        self,
        judge_prompt: str = "",
        action_require: list[str] | None = None,
    ) -> bool:
        """LLM 判断激活工具函数。

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
