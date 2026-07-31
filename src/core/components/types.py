"""组件相关类型和枚举。

本模块定义了组件模块中使用的所有核心类型和枚举，包括聊天类型、组件类型
以及用于解析组件签名的实用函数。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


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
    BEFORE_MESSAGE_RECEIVED = "before_message_received"
    ON_MESSAGE_RECEIVED = "on_message_received"
    ON_MESSAGE_SENT = "on_message_sent"
    AFTER_MESSAGE_SENT = "after_message_sent"
    ON_CHATTER_STEP = "on_chatter_step"
    AFTER_CHATTER_STEP = "after_chatter_step"
    ON_INTERNAL_CONTEXT_REQUESTED = "on_internal_context_requested"
    ON_NOTICE_RECEIVED = "on_notice_received"
    ON_RECEIVED_OTHER_MESSAGE = "on_received_other_message"
    ON_ALL_PLUGIN_LOADED = "on_all_plugin_loaded"
    ON_PLUGIN_UNLOADED = "on_plugin_unloaded"
    ON_COMPONENT_LOADED = "on_component_loaded"
    ON_COMPONENT_UNLOADED = "on_component_unloaded"

    # 提示词构建事件
    ON_PROMPT_BUILD = "on_prompt_build"

    # LLM 请求生命周期事件
    BEFORE_LLM_REQUEST = "before_llm_request"
    AFTER_LLM_REQUEST = "after_llm_request"
    ON_LLM_REQUEST_FAILED = "on_llm_request_failed"

    # 工具调用生命周期事件
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TOOL_CALL_FAILED = "on_tool_call_failed"

    # 动作调用生命周期事件
    BEFORE_ACTION_CALL = "before_action_call"
    AFTER_ACTION_CALL = "after_action_call"
    ON_ACTION_CALL_FAILED = "on_action_call_failed"

    # 命令执行生命周期事件
    BEFORE_COMMAND_EXECUTE = "before_command_execute"
    AFTER_COMMAND_EXECUTE = "after_command_execute"
    ON_COMMAND_EXECUTE_FAILED = "on_command_execute_failed"

    # 媒体识别事件（落盘入库后触发，处理器可回写 description）
    ON_MEDIA_RECOGNIZE = "on_media_recognize"

    CUSTOM = "custom"  # 用于自定义事件


class MediaEngine(str, Enum):
    """媒体识别引擎类型。

    用于 ``ON_MEDIA_RECOGNIZE`` 事件的 ``engine`` 字段，
    供处理器判断应调用 VLM 还是 ASR。

    Attributes:
        VLM: 图片/表情包视觉识别引擎
        ASR: 语音转文字引擎
    """

    VLM = "vlm"
    ASR = "asr"


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


@dataclass(slots=True)
class PlatformSendResult:
    """适配器向平台发送消息的结果。

    与异常不同，此对象同时携带"成功/失败"状态与平台消息 ID，
    供调用方（如 MessageSender）精确区分以下三种情况：
    - 发送成功且平台返回消息 ID：``success=True, message_id=ID``
    - 发送成功但平台未返回 ID：``success=True, message_id=None``
    - 发送失败：``success=False``（``error`` 描述原因）

    Attributes:
        success: 是否发送成功
        message_id: 平台返回的消息 ID（如平台未返回则为 None）
        error: 失败原因描述（成功时为 None）
        response: 平台返回的原始响应（如有），便于排查
    """

    success: bool
    message_id: str | None = None
    error: str | None = None
    response: Any = None


@dataclass
class Wait:
    """等待结果。

    表示 Chatter 需要等待一段时间。

    Attributes:
        time: 等待时间（秒），如果为 None 则表示无限等待直到有新消息；
            如果为数字，则表示到期后由框架主动恢复生成器，不依赖新消息
        step_data: 可选的步骤元数据，供框架在步进完成后发布通知事件
    """

    time: float | int | None = None
    step_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class WaitResumeEvent:
    """Wait/Stop 结束后由框架送回生成器的恢复事件。

    框架内置 source 约定值（不是硬性限制）：
    - ``"message"`` 新消息唤醒
    - ``"timer"`` 定时器到期
    - ``"sub_agent"`` 子代理完成
    - ``"internal_context"`` 内部上下文到达

    外部插件可以通过 ``trigger_external_resume()`` 注入任意 source 的事件，
    通过 ``extra`` 字段传递自定义数据。
    对未知 source 的处理由各 Chatter 自行决定。
    """

    source: str
    wait_time: float | int | None = None
    unread_count: int = 0
    context_key: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Success:
    """成功结果。

    表示 Chatter 成功完成执行。

    Attributes:
        message: 成功消息
        data: 可选的附加数据
        step_data: 可选的步骤元数据，供框架在步进完成后发布通知事件
    """

    message: str
    data: dict[str, Any] | None = None
    step_data: dict[str, Any] | None = None


@dataclass
class Failure:
    """失败结果。

    表示 Chatter 执行失败。

    Attributes:
        error: 错误消息
        exception: 可选的异常对象
        step_data: 可选的步骤元数据，供框架在步进完成后发布通知事件
    """

    error: str
    exception: Exception | None = None
    step_data: dict[str, Any] | None = None


@dataclass
class Stop:
    """停止结果。

    表示 Chatter 将在一段时间后重新开始对话。

    Attributes:
        time: 停止时间（秒）
        step_data: 可选的步骤元数据，供框架在步进完成后发布通知事件
    """

    time: float | int
    direct_message_wake_enabled: bool = False
    direct_message_wake_probability: float = 0.0
    step_data: dict[str, Any] | None = None


# 类型别名
ChatterResult = Wait | Success | Failure | Stop
