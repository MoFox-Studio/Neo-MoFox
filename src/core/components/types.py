"""组件相关类型和枚举。

本模块定义了组件模块中使用的所有核心类型和枚举，包括聊天类型、组件类型
以及用于解析组件签名的实用函数。
"""

from enum import Enum
from typing import TypedDict


class ChatType(Enum):
    """聊天类型枚举。

    定义组件可以在其中活动的不同聊天上下文类型。
    """

    PRIVATE = "private"
    GROUP = "group"
    DISCUSS = "discuss"
    ALL = "all"


class ComponentType(Enum):
    """组件类型枚举。

    插件系统中所有可能的组件类型。
    """

    ACTION = "action"
    AGENT = "agent"
    TOOL = "tool"
    ADAPTER = "adapter"
    CHATTER = "chatter"
    COMMAND = "command"
    CONFIG = "config"
    EVENT_HANDLER = "event_handler"
    SERVICE = "service"
    ROUTER = "router"
    PLUGIN = "plugin"


class EventType(str, Enum):
    """事件类型枚举。

    定义事件处理器可以订阅的系统事件。

    该枚举继承自 ``str``，以便可直接作为内核事件总线的事件名使用。
    """

    ON_START = "on_start"
    ON_STOP = "on_stop"
    ON_MESSAGE_RECEIVED = "on_message_received"
    ON_MESSAGE_SENT = "on_message_sent"
    ON_NOTICE_RECEIVED = "on_notice_received"
    ON_RECEIVED_OTHER_MESSAGE = "on_received_other_message"
    ON_ALL_PLUGIN_LOADED = "on_all_plugin_loaded"
    ON_PLUGIN_UNLOADED = "on_plugin_unloaded"
    ON_COMPONENT_LOADED = "on_component_loaded"
    ON_COMPONENT_UNLOADED = "on_component_unloaded"
    CUSTOM = "custom"  # 用于自定义事件


class ComponentState(Enum):
    """组件状态枚举。

    跟踪组件的生命周期状态。
    """

    UNLOADED = "unloaded"
    LOADED = "loaded"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class PermissionLevel(int, Enum):
    """权限级别枚举。

    定义命令执行的层级权限系统。
    数值越高表示权限越大。

    Attributes:
        GUEST: 访客级别 (Level 1) - 有限访问
        USER: 用户级别 (Level 2) - 普通用户（默认）
        OPERATOR: 操作员级别 (Level 3) - 管理操作
        OWNER: 所有者级别 (Level 4) - 完全控制

    Examples:
        >>> level = PermissionLevel.OPERATOR
        >>> level > PermissionLevel.USER
        True
        >>> PermissionLevel.from_string("owner")
        PermissionLevel.OWNER
    """

    GUEST = 1
    USER = 2
    OPERATOR = 3
    OWNER = 4

    def __lt__(self, other: object) -> bool:
        """比较权限级别（小于）。

        Args:
            other: 另一个 PermissionLevel 对象

        Returns:
            bool: 如果自身权限低于 other，返回 True
        """
        if isinstance(other, PermissionLevel):
            return self.value < other.value
        return NotImplemented

    def __le__(self, other: object) -> bool:
        """比较权限级别（小于等于）。

        Args:
            other: 另一个 PermissionLevel 对象

        Returns:
            bool: 如果自身权限低于或等于 other，返回 True
        """
        if isinstance(other, PermissionLevel):
            return self.value <= other.value
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        """比较权限级别（大于）。

        Args:
            other: 另一个 PermissionLevel 对象

        Returns:
            bool: 如果自身权限高于 other，返回 True
        """
        if isinstance(other, PermissionLevel):
            return self.value > other.value
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        """比较权限级别（大于等于）。

        Args:
            other: 另一个 PermissionLevel 对象

        Returns:
            bool: 如果自身权限高于或等于 other，返回 True
        """
        if isinstance(other, PermissionLevel):
            return self.value >= other.value
        return NotImplemented

    @classmethod
    def from_string(cls, level_str: str) -> "PermissionLevel":
        """从字符串转换为 PermissionLevel。

        Args:
            level_str: 权限级别字符串（不区分大小写）

        Returns:
            PermissionLevel: 对应的权限级别枚举值

        Raises:
            ValueError: 如果字符串不匹配任何级别

        Examples:
            >>> PermissionLevel.from_string("owner")
            PermissionLevel.OWNER
            >>> PermissionLevel.from_string("GUEST")
            PermissionLevel.GUEST
        """
        try:
            return cls[level_str.upper()]
        except KeyError:
            valid = [lvl.name for lvl in cls]
            raise ValueError(
                f"无效的权限级别: '{level_str}'。"
                f"有效级别为: {', '.join(valid)}"
            )

    def to_string(self) -> str:
        """转换为小写字符串。

        Returns:
            str: 权限级别的小写字符串表示

        Examples:
            >>> PermissionLevel.OPERATOR.to_string()
            'operator'
        """
        return self.name.lower()


class ComponentMeta(TypedDict, total=False):
    """组件元数据。

    组件的标准化元数据结构。
    """

    name: str
    version: str
    description: str
    author: str


class ComponentSignature(TypedDict):
    """组件签名类型字典。

    表示已解析的组件签名，格式为 'plugin_name:component_type:component_name'。
    """

    plugin_name: str
    component_type: ComponentType
    component_name: str


def parse_signature(signature: str) -> ComponentSignature:
    """解析组件签名字符串。

    解析格式为 'plugin_name:component_type:component_name' 的组件签名，
    并返回 ComponentSignature 类型字典。

    Args:
        signature: 组件签名字符串，例如 'my_plugin:action:send_message'

    Returns:
        ComponentSignature: 解析后的签名组件

    Raises:
        ValueError: 如果签名格式无效

    Examples:
        >>> parse_signature("my_plugin:action:send_message")
        {'plugin_name': 'my_plugin', 'component_type': ComponentType.ACTION, 'component_name': 'send_message'}

        >>> parse_signature("other_plugin:tool:calculator")
        {'plugin_name': 'other_plugin', 'component_type': ComponentType.TOOL, 'component_name': 'calculator'}
    """
    parts = signature.split(":")

    if len(parts) != 3:
        raise ValueError(
            f"无效的签名格式: '{signature}'。"
            f"期望格式为 'plugin_name:component_type:component_name'，但得到 {len(parts)} 个部分"
        )

    plugin_name, component_type_str, component_name = parts

    # 验证并转换组件类型
    try:
        component_type = ComponentType(component_type_str.lower())
    except ValueError:
        valid_types = [ct.value for ct in ComponentType]
        raise ValueError(
            f"未知的组件类型: '{component_type_str}'。"
            f"有效类型为: {', '.join(valid_types)}"
        )

    if not plugin_name:
        raise ValueError("插件名称不能为空")

    if not component_name:
        raise ValueError("组件名称不能为空")

    return ComponentSignature(
        plugin_name=plugin_name,
        component_type=component_type,
        component_name=component_name,
    )


def build_signature(
    plugin_name: str, component_type: ComponentType, component_name: str
) -> str:
    """构建组件签名字符串。

    从各个部分构建组件签名。

    Args:
        plugin_name: 插件名称
        component_type: 组件类型
        component_name: 组件名称

    Returns:
        str: 组件签名字符串

    Examples:
        >>> build_signature("my_plugin", ComponentType.ACTION, "send_message")
        'my_plugin:action:send_message'
    """
    return f"{plugin_name}:{component_type.value}:{component_name}"
