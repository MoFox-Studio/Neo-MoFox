"""命令组件基类。

本模块提供 BaseCommand 类，定义命令组件的基本行为。
Command 使用 Trie 树路由系统，支持多级命令和类型提示参数解析。
"""

import inspect
import shlex
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from src.core.components.types import ChatType, PermissionLevel

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin


@dataclass
class CommandNode:
    """命令树节点。

    Attributes:
        name: 节点名称（命令片段）
        handler: 处理函数（如果是叶子节点）
        children: 子节点字典
        description: 节点描述（用于帮助文档）
    """

    name: str
    handler: Callable | None = None
    children: dict[str, "CommandNode"] = field(default_factory=dict)
    description: str = ""


class BaseCommand(ABC):
    """命令组件基类。

    Command 使用 Trie 树路由系统，支持多级命令和类型提示参数解析。
    通过 @cmd_route 装饰器注册命令路由。

    Class Attributes:
        plugin_name: 所属插件名称（由插件管理器在注册时注入，插件开发者无需填写）
        command_name: 命令名称
        command_description: 命令描述
        permission_level: 权限级别（默认 USER）
        associated_platforms: 关联的平台列表
        chat_type: 支持的聊天类型
        command_prefix: 命令前缀（如 "/"、"!"）

    Examples:
        >>> class MyCommand(BaseCommand):
        ...     command_name = "my_command"
        ...     command_prefix = "/"
        ...
        ...     @cmd_route("set", "seconds")
        ...     async def handle_set_seconds(self, value: int) -> tuple[bool, str]:
        ...         return True, f"设置秒数: {value}"
        ...
        ...     @cmd_route("get")
        ...     async def handle_get(self) -> tuple[bool, str]:
        ...         return True, "获取值"
    """
    _plugin_: str
    _signature_: str

    # 命令元数据
    command_name: str = ""
    command_description: str = ""

    # 权限级别（默认为 USER）
    permission_level: PermissionLevel = PermissionLevel.USER

    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL
    command_prefix: str = "/"

    # 组件级依赖（精确到组件签名）
    dependencies: list[str] = []  # 例如 ["other_plugin:service:config"]

    def __init__(self, plugin: "BasePlugin",stream_id: str ) -> None:
        """初始化命令组件。

        Args:
            plugin: 所属插件实例
        """
        self.plugin = plugin
        self.stream_id = stream_id
        self._root = CommandNode(name="root")
        self._build_command_tree()

    @classmethod
    def get_signature(cls) -> str | None:
        """获取命令组件的唯一签名。

        Returns:
            str | None: 组件签名，格式为 "plugin_name:command:command_name"，如果还未注入插件名称则返回 None

        Examples:
            >>> signature = MyCommand.get_signature()
            >>> "my_plugin:command:my_command"
        """
        if hasattr(cls, "_signature_") and cls._signature_:
            return cls._signature_
        if hasattr(cls, "_plugin_") and cls._plugin_ and cls.command_name:
            return f"{cls._plugin_}:command:{cls.command_name}"
        return None

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """匹配命令。

        检查给定的命令片段列表是否匹配该 Command 组件。
        这是 command_manager 用来查找匹配命令的核心方法。

        Args:
            parts: 命令分割后的片段列表（例如 ["time", "set", "30"]）

        Returns:
            int: 匹配的命令长度，如果不匹配返回 0

        Examples:
            >>> class TimeCommand(BaseCommand):
            ...     command_name = "time"
            >>> TimeCommand.match(["time", "set"])
            1
            >>> TimeCommand.match(["other", "command"])
            0
        """
        if not parts or not cls.command_name:
            return 0

        # 检查第一个片段是否匹配 command_name
        if parts[0] == cls.command_name:
            return 1

        return 0

    def _build_command_tree(self) -> None:
        """构建命令树。

        扫描所有被 @cmd_route 装饰的方法，构建 Trie 树路由。
        """
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, "_cmd_route"):
                route_path = method._cmd_route  # type: ignore[attr-defined]
                self._register_route(route_path, method)

    def _register_route(self, path: list[str], handler: Callable) -> None:
        """注册命令路由。

        Args:
            path: 命令路径（如 ["set", "seconds"]）
            handler: 处理函数
        """
        current = self._root

        for segment in path:
            if segment not in current.children:
                current.children[segment] = CommandNode(name=segment)
            current = current.children[segment]

        current.handler = handler
        current.description = handler.__doc__ or ""

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """执行命令的入口方法。

        解析消息文本，通过 Trie 树路由到对应的处理函数。
        插件开发者通常不需要重写此方法，而是使用 @cmd_route 装饰器定义处理函数。

        Args:
            message_text: 完整的消息文本 (例如 "/time set seconds 30")

        Returns:
            tuple[bool, str]: (是否成功, 返回结果/错误信息)
        """
        # 移除命令前缀
        if message_text.startswith(self.command_prefix):
            message_text = message_text[len(self.command_prefix):].strip()

        return await self._route_and_execute(message_text)

    async def _route_and_execute(self, command_text: str) -> tuple[bool, str]:
        """路由并执行命令。

        Args:
            command_text: 命令文本

        Returns:
            tuple[bool, str]: (是否成功, 响应消息)
        """
        try:
            # 使用 shlex 解析参数（支持引号）
            parts = shlex.split(command_text)
        except ValueError as e:
            return False, f"参数解析错误: {e}"

        if not parts:
            return False, "空命令"

        # 遍历 Trie 树
        current = self._root
        consumed = 0

        for part in parts:
            if part in current.children:
                current = current.children[part]
                consumed += 1
            else:
                break

        if current.handler is None:
            # 未找到处理器
            return await self._generate_help(current, parts[consumed:])

        # 提取参数
        args = parts[consumed:]

        # 调用处理函数
        try:
            return await self._call_handler(current.handler, args)
        except Exception as e:
            return False, f"执行错误: {e}"

    async def _call_handler(
        self, handler: Callable, args: list[str]
    ) -> tuple[bool, str]:
        """调用处理函数。

        根据类型注解自动转换参数类型。

        Args:
            handler: 处理函数
            args: 参数列表（字符串）

        Returns:
            tuple[bool, str]: (是否成功, 响应消息)
        """
        # 获取函数签名
        sig = inspect.signature(handler)

        # 过滤掉 'self' 参数
        parameters = [
            (name, param)
            for name, param in sig.parameters.items()
            if name != "self"
        ]

        converted_args = []

        for i, (arg_name, param) in enumerate(parameters):
            if i >= len(args):
                if param.default == inspect.Parameter.empty:
                    return False, f"缺少参数: {arg_name}"
                break

            arg_value = args[i]

            # 类型转换
            if param.annotation != inspect.Parameter.empty:
                try:
                    converted_value = self._convert_type(
                        arg_value, param.annotation
                    )
                except ValueError as e:
                    return False, f"参数类型错误: {arg_name} - {e}"
            else:
                converted_value = arg_value

            converted_args.append(converted_value)

        # 调用处理函数
        result = await handler(*converted_args)

        # 检查返回值类型
        if isinstance(result, tuple) and len(result) == 2:
            return result
        else:
            return True, str(result)

    def _convert_type(self, value: str, target_type: type) -> Any:
        """转换参数类型。

        Args:
            value: 字符串值
            target_type: 目标类型

        Returns:
            Any: 转换后的值

        Raises:
            ValueError: 如果类型转换失败
        """
        from typing import get_origin, get_args

        # 处理泛型类型（如 list[int]）
        origin = get_origin(target_type)
        args = get_args(target_type)

        if origin is list:
            # 处理 list 类型
            if not args:
                return [value]
            inner_type = args[0]
            return [self._convert_type(v.strip(), inner_type) for v in value.split(",")]

        # 基本类型转换
        type_map = {
            int: int,
            str: str,
            float: float,
            bool: lambda x: x.lower() in ("true", "1", "yes", "on"),
        }

        if target_type in type_map:
            return type_map[target_type](value)

        # 尝试直接调用构造函数
        try:
            return target_type(value)
        except Exception:
            raise ValueError(f"无法转换为 {target_type}")

    async def _generate_help(
        self, node: CommandNode, remaining: list[str]
    ) -> tuple[bool, str]:
        """生成帮助文档。

        Args:
            node: 当前节点
            remaining: 剩余的命令片段

        Returns:
            tuple[bool, str]: (是否成功, 帮助文档)
        """
        help_lines = [f"命令: {self.command_name}", f"描述: {self.command_description}"]

        if node.handler:
            help_lines.append(f"\n当前命令: {'/'.join(self._get_path_to_node(node))}")
            if node.description:
                help_lines.append(f"说明: {node.description}")

            # 生成参数说明
            sig = inspect.signature(node.handler)
            params = [
                (name, param)
                for name, param in sig.parameters.items()
                if name != "self"
            ]

            if params:
                help_lines.append("\n参数:")
                for name, param in params:
                    param_type = param.annotation or "Any"
                    default = param.default
                    if default != inspect.Parameter.empty:
                        help_lines.append(f"  {name}: {param_type} (默认: {default})")
                    else:
                        help_lines.append(f"  {name}: {param_type} (必需)")
        else:
            # 显示子命令
            if node.children:
                help_lines.append("\n子命令:")
                for child_name, child in node.children.items():
                    desc = child.description or "无描述"
                    help_lines.append(f"  {child_name} - {desc}")

            if remaining:
                help_lines.append(f"\n未知命令: {' '.join(remaining)}")

        return True, "\n".join(help_lines)

    def _get_path_to_node(self, node: CommandNode) -> list[str]:
        """获取到节点的路径。

        Args:
            node: 目标节点

        Returns:
            list[str]: 路径片段列表
        """
        # 简化版本：通过 BFS 找到路径
        # 实际实现应该维护父指针
        path = []
        current = node

        while current.name != "root":
            path.append(current.name)
            # 需要向上遍历，这里简化处理
            break

        return list(reversed(path))


def cmd_route(*path: str) -> Callable:
    """命令路由装饰器。

    用于标记命令处理函数的路由路径。

    Args:
        *path: 命令路径片段

    Returns:
        Callable: 装饰器函数

    Examples:
        >>> @cmd_route("set", "seconds")
        ... async def handle_set_seconds(self, value: int) -> tuple[bool, str]:
        ...     return True, f"设置秒数: {value}"
    """

    def decorator(func: Callable) -> Callable:
        func._cmd_route = list(path)
        return func

    return decorator
