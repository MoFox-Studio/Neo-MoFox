"""工具组件基类。

本模块提供 BaseTool 类，定义工具组件的基本行为。
工具是"查询"功能，供 LLM 调用以获取信息，与动作不同。
"""

from abc import ABC, abstractmethod
from typing import Annotated, Any, TYPE_CHECKING

from src.core.components.types import ChatType
from src.core.components.utils import parse_function_signature
from src.kernel.llm.payload.tooling import LLMUsable, LLMUsableExecution

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin

class BaseTool(ABC, LLMUsable):
    """工具组件基类。

    工具提供特定的功能接口供 LLM 调用，例如计算器、翻译器等。
    与 Action 不同，Tool 侧重于"查询"功能而非"响应"动作。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        tool_name: 工具名称
        tool_description: 工具描述
        chatter_allow: 支持的 Chatter 列表
        chat_type: 支持的聊天类型
        associated_platforms: 关联的平台列表

    Examples:
        >>> class CalculatorTool(BaseTool):
        ...     tool_name = "calculator"
        ...     tool_description = "数学计算器"
        ...
        ...     async def execute(self, expression: str) -> tuple[bool, str]:
        ...         try:
        ...             result = eval(expression)
        ...         return True, str(result)
        ...         except Exception as e:
        ...         return False, f"计算错误: {e}"
    """
    _plugin_: str
    _signature_: str

    # 工具元数据
    tool_name: str = ""
    tool_description: str = ""

    chatter_allow: list[str] = []
    chat_type: ChatType = ChatType.ALL

    associated_platforms: list[str] = []

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:tool:database"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化工具组件。

        Args:
            plugin: 所属插件实例
        """
        self.plugin = plugin

    @classmethod
    def get_signature(cls) -> str | None:
        """获取工具组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:tool:tool_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = CalculatorTool.get_signature()
            >>> "my_plugin:tool:calculator"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.tool_name:
            return f"{cls._plugin_}:tool:{cls.tool_name}"
        return None
    
    @abstractmethod
    async def execute(
        self, *args: Any, **kwargs: Any
    ) -> tuple[Annotated[bool, "是否成功"], Annotated[str | dict, "返回结果"]]:
        """执行工具的主要逻辑。

        所有 Tool 组件都必须重写此方法。
        必须编写参数文档来告诉 LLM 每个参数的作用。

        Returns:
            tuple[bool, str | dict]: (是否成功, 返回结果)。结果可以是字符串或字典

        Examples:
            >>> async def execute(
            ...     self,
            ...     expression: Annotated[str, "要计算的数学表达式"]
            ... ) -> tuple[bool, str]:
            ...     try:
            ...         result = eval(expression)
            ...         return True, str(result)
            ...     except Exception as e:
            ...         return False, f"计算错误: {e}"

        Note:
            tool 管理器会自动识别类型提示和文档字符串并生成对应的 Tool Schema。
        """
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
        """生成 LLM Tool Schema。

        通过 inspect 解析 execute 方法生成 OpenAI Tool Calling 格式的 schema。

        Returns:
            dict[str, Any]: OpenAI Tool 格式的 schema

        Examples:
            >>> schema = CalculatorTool.to_schema()
            >>> {
            ...     "type": "function",
            ...     "function": {
            ...         "name": "calculator",
            ...         "description": "数学计算器",
            ...         "parameters": {
            ...             "type": "object",
            ...             "properties": {
            ...                 "expression": {"type": "string", "description": "..."}
            ...             },
            ...             "required": ["expression"]
            ...         }
            ...     }
            ... }
        """
        # 使用 utils 中的共同方法生成 schema，name 前缀加上组件类型
        return parse_function_signature(cls.execute, f"tool-{cls.tool_name}", cls.tool_description)
