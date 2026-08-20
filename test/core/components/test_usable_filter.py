"""测试 LLMUsable 过滤引擎与作用域评估功能。"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.components.types import ChatType
from src.core.components.usable_filter import (
    UsableFilterContext,
    build_filter_context_from_stream,
    evaluate_usable_filter,
)


class DummyToolDefault:
    """默认配置工具类。"""

    name = "dummy_default"


class DummyToolWithChatterScope:
    """指定 Chatter 作用域的工具类。"""

    name = "dummy_chatter_scope"
    chatter_allow = ["main_chatter", "test_plugin:chatter:custom"]
    chatter_deny = ["blocked_chatter"]


class DummyToolWithChatTypeScope:
    """指定 ChatType 作用域的工具类。"""

    name = "dummy_chat_type_scope"
    chat_type = [ChatType.GROUP]


class DummyToolWithPlatformScope:
    """指定 Platform 作用域的工具类。"""

    name = "dummy_platform_scope"
    platform_allow = ["qq"]
    platform_deny = ["discord"]


class DummyToolWithLegacyPlatform:
    """指定 associated_platforms 平台白名单的工具类。"""

    name = "dummy_legacy_platform"
    associated_platforms = ["telegram"]


class DummyToolWithStreamScope:
    """指定 Stream ID 作用域的工具类。"""

    name = "dummy_stream_scope"
    stream_allow = ["stream_123"]
    stream_deny = ["stream_blocked"]


class DummyActionWithFormats:
    """指定内容格式作用域的动作类。"""

    name = "dummy_formats"
    associated_types = ["text", "image"]


def test_evaluate_usable_filter_default_allow() -> None:
    """全默认配置组件在任意上下文中均应通过过滤。"""
    ctx = UsableFilterContext(
        stream_id="stream_abc",
        chat_type="private",
        platform="qq",
        user_id="10001",
    )
    is_ok, reason = evaluate_usable_filter(DummyToolDefault, ctx)
    assert is_ok is True
    assert reason is None


def test_evaluate_usable_filter_chatter_deny() -> None:
    """命中 chatter_deny 黑名单时应拒绝。"""
    ctx = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        chatter_name="blocked_chatter",
    )
    is_ok, reason = evaluate_usable_filter(DummyToolWithChatterScope, ctx)
    assert is_ok is False
    assert "chatter 在黑名单中" in str(reason)


def test_evaluate_usable_filter_chatter_allow_match_name_or_sig() -> None:
    """命中 chatter_allow 白名单（按 name 或 signature）时应放行。"""
    ctx1 = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        chatter_name="main_chatter",
    )
    is_ok1, _ = evaluate_usable_filter(DummyToolWithChatterScope, ctx1)
    assert is_ok1 is True

    ctx2 = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        chatter_name="other_name",
        chatter_signature="test_plugin:chatter:custom",
    )
    is_ok2, _ = evaluate_usable_filter(DummyToolWithChatterScope, ctx2)
    assert is_ok2 is True

    ctx3 = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        chatter_name="unauthorized_chatter",
        chatter_signature="unauthorized:sig",
    )
    is_ok3, reason3 = evaluate_usable_filter(DummyToolWithChatterScope, ctx3)
    assert is_ok3 is False
    assert "chatter 不匹配" in str(reason3)


def test_evaluate_usable_filter_chat_type() -> None:
    """聊天类型不匹配时应拒绝。"""
    ctx_private = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
    )
    is_ok1, reason1 = evaluate_usable_filter(
        DummyToolWithChatTypeScope, ctx_private
    )
    assert is_ok1 is False
    assert "聊天类型不匹配" in str(reason1)

    ctx_group = UsableFilterContext(
        stream_id="s1",
        chat_type="group",
        platform="qq",
    )
    is_ok2, _ = evaluate_usable_filter(DummyToolWithChatTypeScope, ctx_group)
    assert is_ok2 is True


def test_evaluate_usable_filter_platform() -> None:
    """平台黑白名单及 associated_platforms 属性测试。"""
    ctx_discord = UsableFilterContext(
        stream_id="s1", chat_type="private", platform="discord"
    )
    is_ok1, reason1 = evaluate_usable_filter(
        DummyToolWithPlatformScope, ctx_discord
    )
    assert is_ok1 is False
    assert "平台在黑名单中" in str(reason1)

    ctx_telegram = UsableFilterContext(
        stream_id="s1", chat_type="private", platform="telegram"
    )
    is_ok2, reason2 = evaluate_usable_filter(
        DummyToolWithPlatformScope, ctx_telegram
    )
    assert is_ok2 is False
    assert "平台不匹配" in str(reason2)

    is_ok3, _ = evaluate_usable_filter(
        DummyToolWithLegacyPlatform, ctx_telegram
    )
    assert is_ok3 is True


def test_evaluate_usable_filter_stream() -> None:
    """Stream ID 黑白名单校验。"""
    ctx_blocked = UsableFilterContext(
        stream_id="stream_blocked", chat_type="private", platform="qq"
    )
    is_ok1, reason1 = evaluate_usable_filter(
        DummyToolWithStreamScope, ctx_blocked
    )
    assert is_ok1 is False
    assert "聊天流在黑名单中" in str(reason1)

    ctx_allowed = UsableFilterContext(
        stream_id="stream_123", chat_type="private", platform="qq"
    )
    is_ok2, _ = evaluate_usable_filter(DummyToolWithStreamScope, ctx_allowed)
    assert is_ok2 is True

    ctx_other = UsableFilterContext(
        stream_id="stream_999", chat_type="private", platform="qq"
    )
    is_ok3, reason3 = evaluate_usable_filter(
        DummyToolWithStreamScope, ctx_other
    )
    assert is_ok3 is False
    assert "聊天流不匹配" in str(reason3)


def test_evaluate_usable_filter_does_not_evaluate_entity_lists() -> None:
    """Group / User 实体名单不应由引擎评估（运行时交给事件钩子/go_activate）。"""

    class PlainGroupTool:
        name = "plain_group_tool"

    # 任意群上下文都不做名单过滤
    ctx_group = UsableFilterContext(
        stream_id="s1", chat_type="group", platform="qq", group_id="any_group"
    )
    is_ok, reason = evaluate_usable_filter(PlainGroupTool, ctx_group)
    assert is_ok is True
    assert reason is None

    # 任意用户上下文都不做名单过滤
    ctx_user = UsableFilterContext(
        stream_id="s1", chat_type="private", platform="qq", user_id="any_user"
    )
    is_ok2, reason2 = evaluate_usable_filter(PlainGroupTool, ctx_user)
    assert is_ok2 is True
    assert reason2 is None


def test_evaluate_usable_filter_content_types() -> None:
    """内容段格式支持校验。"""
    ctx_supported = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        accept_formats=["text", "image", "emoji"],
    )
    is_ok1, _ = evaluate_usable_filter(DummyActionWithFormats, ctx_supported)
    assert is_ok1 is True

    ctx_unsupported = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        accept_formats=["text"],  # 缺少 image
    )
    is_ok2, reason2 = evaluate_usable_filter(
        DummyActionWithFormats, ctx_unsupported
    )
    assert is_ok2 is False
    assert "适配器不支持内容格式" in str(reason2)


def test_build_filter_context_from_stream() -> None:
    """从 ChatStream 和 Chatter 实例提取上下文快照。"""
    mock_stream = MagicMock()
    mock_stream.stream_id = "stream_demo_1"
    mock_stream.chat_type = "group"
    mock_stream.platform = "qq"

    mock_msg = MagicMock()
    mock_msg.sender_id = "12345"
    mock_msg.extra = {
        "group_id": "888888",
        "format_info": {"accept_format": ["text", "image"]},
    }

    mock_stream.context.current_message = mock_msg
    mock_stream.context.unread_messages = []

    mock_chatter = MagicMock()
    mock_chatter.name = "demo_chatter"
    mock_chatter.get_signature.return_value = "plugin:chatter:demo"

    ctx = build_filter_context_from_stream(mock_stream, mock_chatter)
    assert ctx.stream_id == "stream_demo_1"
    assert ctx.chat_type == "group"
    assert ctx.platform == "qq"
    assert ctx.group_id == "888888"
    assert ctx.user_id == "12345"
    assert ctx.chatter_name == "demo_chatter"
    assert ctx.chatter_signature == "plugin:chatter:demo"
    assert ctx.accept_formats == ["text", "image"]


def test_normalize_str_list_none() -> None:
    """None 输入应安全返回空列表。"""
    from src.core.components.usable_filter import _normalize_str_list

    assert _normalize_str_list(None) == []


def test_resolve_allowed_chat_types_all() -> None:
    """包含 ChatType.ALL 或 'all' 的列表声明应返回空集合。"""
    from src.core.components.usable_filter import _resolve_allowed_chat_types

    assert _resolve_allowed_chat_types([ChatType.ALL]) == set()
    assert _resolve_allowed_chat_types(["all"]) == set()


class DummyToolSingleValues:
    """属性为非列表标量值的组件。"""

    name = "dummy_single_values"
    chatter_allow = "main_chatter"
    chatter_deny = "blocked_chatter"
    platform_allow = "qq"
    platform_deny = "discord"
    stream_allow = "s1"
    stream_deny = "s_blocked"
    chat_type = "group"


class DummyToolInvalidCollection:
    """属性为非列表/标量无效类型的组件。"""

    name = "dummy_invalid"
    chatter_allow = {"invalid": True}  # type: ignore


class DummyToolValidateAssociatedTypesRaises:
    """validate_associated_types 抛出异常的组件。"""

    name = "dummy_raises"
    associated_types = ["text"]

    @classmethod
    def validate_associated_types(cls) -> list[str]:
        raise ValueError("模拟校验异常")


def test_evaluate_usable_filter_scalar_attributes() -> None:
    """测试单个标量属性（如单个字符串/数字）的自动规范化解析。"""
    ctx = UsableFilterContext(
        stream_id="s1",
        chat_type="group",
        platform="qq",
        group_id="123456",
        user_id="10001",
        chatter_name="main_chatter",
    )
    is_ok, _ = evaluate_usable_filter(DummyToolSingleValues, ctx)
    assert is_ok is True


def test_evaluate_usable_filter_invalid_attribute_type() -> None:
    """非合法集合类型属性应安全退化为空列表。"""
    ctx = UsableFilterContext(stream_id="s1", chat_type="private", platform="qq")
    is_ok, _ = evaluate_usable_filter(DummyToolInvalidCollection, ctx)
    assert is_ok is True


def test_evaluate_usable_filter_validate_types_exception_fallback() -> None:
    """validate_associated_types 抛出异常时回退到 associated_types 属性。"""
    ctx = UsableFilterContext(
        stream_id="s1",
        chat_type="private",
        platform="qq",
        accept_formats=["text"],
    )
    is_ok, _ = evaluate_usable_filter(
        DummyToolValidateAssociatedTypesRaises, ctx
    )
    assert is_ok is True


def test_build_filter_context_from_unread_messages_and_stream_group_id() -> None:
    """current_message 为空时回退到 unread_messages，且 group_id 从 stream 属性提取。"""
    mock_stream = MagicMock()
    mock_stream.stream_id = "stream_unread"
    mock_stream.chat_type = "group"
    mock_stream.platform = "qq"
    mock_stream.group_id = "55555"

    mock_unread_msg = MagicMock()
    mock_unread_msg.sender_id = "sender_unread"
    mock_unread_msg.group_id = None
    mock_unread_msg.extra = {
        "format_info": {"accept_format": "text"},
    }

    mock_stream.context.current_message = None
    mock_stream.context.unread_messages = [mock_unread_msg]

    mock_chatter = MagicMock()
    mock_chatter.name = "demo"
    mock_chatter.get_signature.return_value = None

    ctx = build_filter_context_from_stream(mock_stream, mock_chatter)
    assert ctx.stream_id == "stream_unread"
    assert ctx.user_id == "sender_unread"
    assert ctx.group_id == "55555"
    assert ctx.accept_formats == ["text"]
