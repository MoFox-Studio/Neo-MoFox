"""集合组件基类。

本模块提供 BaseCollection 类，定义集合组件的基本行为。
集合是 LLMUsable 的集合体，可包含多个 Action、Tool 或嵌套的 Collection。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType
from src.kernel.llm import LLMUsable

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
    _plugin_: str
    _signature_: str

    # 集合元数据
    collection_name: str = ""
    collection_description: str = ""

    associated_platforms: list[str] = []
    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:tool:database"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化集合组件。

        Args:
            plugin: 所属插件实例
        """
        self.plugin = plugin

    async def execute(self, stream_id: str) -> tuple[bool, dict[str, Any]]:
        """执行 Collection（解包并激活内部组件）。

        与 Action/Tool 一样，Collection 作为 LLMUsable 也可以被调用。
        默认行为是：解包自身，从而解除门控并激活其内部组件。

        Returns:
            tuple[bool, dict[str, Any]]: (是否成功, 结果详情)
        """
        signature = self.get_signature()
        if not signature:
            plugin_name = self.plugin.plugin_name
            if plugin_name:
                signature = f"{plugin_name}:collection:{self.collection_name}"

        if not signature:
            return False, {"error": "Collection signature 未就绪"}

        from src.core.managers.collection_manager import get_collection_manager

        manager = get_collection_manager()
        unpacked = await manager.unpack_collection(
            signature,
            recursive=True,
            plugin=self.plugin,
            stream_id=stream_id,
        )

        component_signatures: list[str] = []
        for component_cls in unpacked:
            sig = component_cls.get_signature()  # type: ignore[attr-defined]
            if isinstance(sig, str):
                component_signatures.append(sig)

        return True, {
            "collection": signature,
            "stream_id": stream_id or "__global__",
            "components_count": len(unpacked),
            "components": component_signatures,
        }

    @classmethod
    def get_signature(cls) -> str | None:
        """获取集合组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:collection:collection_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = MyCollection.get_signature()
            >>> "my_plugin:collection:my_collection"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.collection_name:
            return f"{cls._plugin_}:collection:{cls.collection_name}"
        return None

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
