"""测试 src.core.components.types 模块。"""

import pytest

from src.core.components.types import (
    ChatType,
    ComponentMeta,
    ComponentSignature,
    ComponentState,
    ComponentType,
    EventType,
    PermissionLevel,
    build_signature,
    parse_signature,
)


class TestChatType:
    """测试 ChatType 枚举。"""

    def test_chat_type_values(self):
        """测试 ChatType 枚举值。"""
        assert ChatType.PRIVATE.value == "private"
        assert ChatType.GROUP.value == "group"
        assert ChatType.DISCUSS.value == "discuss"
        assert ChatType.ALL.value == "all"


class TestComponentType:
    """测试 ComponentType 枚举。"""

    def test_component_type_values(self):
        """测试 ComponentType 枚举值。"""
        assert ComponentType.ACTION.value == "action"
        assert ComponentType.AGENT.value == "agent"
        assert ComponentType.TOOL.value == "tool"
        assert ComponentType.ADAPTER.value == "adapter"
        assert ComponentType.CHATTER.value == "chatter"
        assert ComponentType.COMMAND.value == "command"
        assert ComponentType.CONFIG.value == "config"
        assert ComponentType.EVENT_HANDLER.value == "event_handler"
        assert ComponentType.SERVICE.value == "service"
        assert ComponentType.ROUTER.value == "router"
        assert ComponentType.PLUGIN.value == "plugin"


class TestEventType:
    """测试 EventType 枚举。"""

    def test_event_type_values(self):
        """测试 EventType 枚举值。"""
        assert EventType.ON_START.value == "on_start"
        assert EventType.ON_STOP.value == "on_stop"
        assert EventType.BEFORE_MESSAGE_RECEIVED.value == "before_message_received"
        assert EventType.ON_MESSAGE_RECEIVED.value == "on_message_received"
        assert EventType.ON_MESSAGE_SENT.value == "on_message_sent"
        assert EventType.BEFORE_CHATTER_DISPATCH.value == "before_chatter_dispatch"
        assert EventType.ON_CHATTER_STEP.value == "on_chatter_step"
        assert EventType.AFTER_CHATTER_STEP.value == "after_chatter_step"
        assert EventType.ON_ALL_PLUGIN_LOADED.value == "on_all_plugin_loaded"
        assert EventType.ON_PLUGIN_UNLOADED.value == "on_plugin_unloaded"
        assert EventType.ON_COMPONENT_LOADED.value == "on_component_loaded"
        assert EventType.ON_COMPONENT_UNLOADED.value == "on_component_unloaded"

        # 提示词构建事件
        assert EventType.ON_PROMPT_BUILD.value == "on_prompt_build"

        # LLM 请求生命周期事件
        assert EventType.BEFORE_LLM_REQUEST.value == "before_llm_request"
        assert EventType.AFTER_LLM_REQUEST.value == "after_llm_request"
        assert EventType.ON_LLM_REQUEST_FAILED.value == "on_llm_request_failed"

        # 工具调用生命周期事件
        assert EventType.BEFORE_TOOL_CALL.value == "before_tool_call"
        assert EventType.AFTER_TOOL_CALL.value == "after_tool_call"
        assert EventType.ON_TOOL_CALL_FAILED.value == "on_tool_call_failed"

        # 动作调用生命周期事件
        assert EventType.BEFORE_ACTION_CALL.value == "before_action_call"
        assert EventType.AFTER_ACTION_CALL.value == "after_action_call"
        assert EventType.ON_ACTION_CALL_FAILED.value == "on_action_call_failed"

        # 命令执行生命周期事件
        assert EventType.BEFORE_COMMAND_EXECUTE.value == "before_command_execute"
        assert EventType.AFTER_COMMAND_EXECUTE.value == "after_command_execute"
        assert EventType.ON_COMMAND_EXECUTE_FAILED.value == "on_command_execute_failed"

        assert EventType.CUSTOM.value == "custom"

    def test_new_event_types_are_str_subclass(self):
        """测试新增事件枚举可直接作为事件名称字符串使用。"""
        assert isinstance(EventType.BEFORE_MESSAGE_RECEIVED, str)
        assert EventType.BEFORE_MESSAGE_RECEIVED == "before_message_received"
        assert isinstance(EventType.ON_PROMPT_BUILD, str)
        assert EventType.ON_PROMPT_BUILD == "on_prompt_build"
        assert isinstance(EventType.BEFORE_LLM_REQUEST, str)
        assert EventType.BEFORE_LLM_REQUEST == "before_llm_request"
        assert isinstance(EventType.AFTER_LLM_REQUEST, str)
        assert EventType.AFTER_LLM_REQUEST == "after_llm_request"
        assert isinstance(EventType.ON_LLM_REQUEST_FAILED, str)
        assert EventType.ON_LLM_REQUEST_FAILED == "on_llm_request_failed"
        assert isinstance(EventType.BEFORE_TOOL_CALL, str)
        assert EventType.BEFORE_TOOL_CALL == "before_tool_call"
        assert isinstance(EventType.AFTER_TOOL_CALL, str)
        assert EventType.AFTER_TOOL_CALL == "after_tool_call"
        assert isinstance(EventType.ON_TOOL_CALL_FAILED, str)
        assert EventType.ON_TOOL_CALL_FAILED == "on_tool_call_failed"
        assert isinstance(EventType.BEFORE_ACTION_CALL, str)
        assert EventType.BEFORE_ACTION_CALL == "before_action_call"
        assert isinstance(EventType.AFTER_ACTION_CALL, str)
        assert EventType.AFTER_ACTION_CALL == "after_action_call"
        assert isinstance(EventType.ON_ACTION_CALL_FAILED, str)
        assert EventType.ON_ACTION_CALL_FAILED == "on_action_call_failed"
        assert isinstance(EventType.BEFORE_COMMAND_EXECUTE, str)
        assert EventType.BEFORE_COMMAND_EXECUTE == "before_command_execute"
        assert isinstance(EventType.AFTER_COMMAND_EXECUTE, str)
        assert EventType.AFTER_COMMAND_EXECUTE == "after_command_execute"
        assert isinstance(EventType.ON_COMMAND_EXECUTE_FAILED, str)
        assert EventType.ON_COMMAND_EXECUTE_FAILED == "on_command_execute_failed"


class TestComponentState:
    """测试 ComponentState 枚举。"""

    def test_component_state_values(self):
        """测试 ComponentState 枚举值。"""
        assert ComponentState.UNLOADED.value == "unloaded"
        assert ComponentState.LOADED.value == "loaded"
        assert ComponentState.ACTIVE.value == "active"
        assert ComponentState.INACTIVE.value == "inactive"
        assert ComponentState.ERROR.value == "error"


class TestPermissionLevel:
    """测试 PermissionLevel 枚举。"""

    def test_permission_level_values(self):
        """测试 PermissionLevel 枚举值。"""
        assert PermissionLevel.GUEST == 1
        assert PermissionLevel.USER == 2
        assert PermissionLevel.OPERATOR == 3
        assert PermissionLevel.OWNER == 4

    def test_comparison_operators(self):
        """测试权限级别比较运算符。"""
        assert PermissionLevel.GUEST < PermissionLevel.USER
        assert PermissionLevel.USER < PermissionLevel.OPERATOR
        assert PermissionLevel.OPERATOR < PermissionLevel.OWNER

        assert PermissionLevel.USER <= PermissionLevel.USER
        assert PermissionLevel.OPERATOR >= PermissionLevel.USER
        assert PermissionLevel.OWNER > PermissionLevel.GUEST

    def test_from_string(self):
        """测试从字符串转换。"""
        assert PermissionLevel.from_string("guest") == PermissionLevel.GUEST
        assert PermissionLevel.from_string("user") == PermissionLevel.USER
        assert PermissionLevel.from_string("operator") == PermissionLevel.OPERATOR
        assert PermissionLevel.from_string("owner") == PermissionLevel.OWNER

        # 测试大小写不敏感
        assert PermissionLevel.from_string("GUEST") == PermissionLevel.GUEST
        assert PermissionLevel.from_string("User") == PermissionLevel.USER

    def test_from_string_invalid(self):
        """测试无效字符串转换。"""
        with pytest.raises(ValueError, match="无效的权限级别"):
            PermissionLevel.from_string("invalid")

    def test_to_string(self):
        """测试转换为字符串。"""
        assert PermissionLevel.GUEST.to_string() == "guest"
        assert PermissionLevel.USER.to_string() == "user"
        assert PermissionLevel.OPERATOR.to_string() == "operator"
        assert PermissionLevel.OWNER.to_string() == "owner"


class TestParseSignature:
    """测试 parse_signature 函数。"""

    def test_parse_valid_signature(self):
        """测试解析有效签名。"""
        result = parse_signature("my_plugin:action:send_message")
        assert result["plugin_name"] == "my_plugin"
        assert result["component_type"] == ComponentType.ACTION
        assert result["component_name"] == "send_message"

    def test_parse_tool_signature(self):
        """测试解析 tool 类型签名。"""
        result = parse_signature("other_plugin:tool:calculator")
        assert result["plugin_name"] == "other_plugin"
        assert result["component_type"] == ComponentType.TOOL
        assert result["component_name"] == "calculator"

    def test_parse_agent_signature(self):
        """测试解析 agent 类型签名。"""
        result = parse_signature("other_plugin:agent:planner")
        assert result["plugin_name"] == "other_plugin"
        assert result["component_type"] == ComponentType.AGENT
        assert result["component_name"] == "planner"

    def test_parse_invalid_format_missing_parts(self):
        """测试缺少部分的无效格式。"""
        with pytest.raises(ValueError, match="无效的签名格式"):
            parse_signature("my_plugin:action")

        with pytest.raises(ValueError, match="无效的签名格式"):
            parse_signature("my_plugin")

    def test_parse_invalid_format_too_many_parts(self):
        """测试部分过多的无效格式。"""
        with pytest.raises(ValueError, match="无效的签名格式"):
            parse_signature("a:b:c:d")

    def test_parse_invalid_component_type(self):
        """测试无效的组件类型。"""
        with pytest.raises(ValueError, match="未知的组件类型"):
            parse_signature("my_plugin:invalid_type:name")

    def test_parse_empty_plugin_name(self):
        """测试空的插件名称。"""
        with pytest.raises(ValueError, match="插件名称不能为空"):
            parse_signature(":action:name")

    def test_parse_empty_component_name(self):
        """测试空的组件名称。"""
        with pytest.raises(ValueError, match="组件名称不能为空"):
            parse_signature("my_plugin:action:")


class TestBuildSignature:
    """测试 build_signature 函数。"""

    def test_build_signature(self):
        """测试构建签名。"""
        signature = build_signature("my_plugin", ComponentType.ACTION, "send_message")
        assert signature == "my_plugin:action:send_message"

    def test_build_signature_tool(self):
        """测试构建 tool 类型签名。"""
        signature = build_signature("test_plugin", ComponentType.TOOL, "calculator")
        assert signature == "test_plugin:tool:calculator"

    def test_build_signature_all_types(self):
        """测试构建所有类型的签名。"""
        for component_type in ComponentType:
            signature = build_signature("plugin", component_type, "component")
            assert signature == f"plugin:{component_type.value}:component"


class TestComponentMeta:
    """测试 ComponentMeta TypedDict。"""

    def test_component_meta_creation(self):
        """测试创建 ComponentMeta。"""
        meta: ComponentMeta = {
            "name": "TestComponent",
            "version": "1.0.0",
            "description": "Test description",
            "author": "Test Author",
        }
        assert meta["name"] == "TestComponent"
        assert meta["version"] == "1.0.0"

    def test_component_meta_partial(self):
        """测试部分字段的 ComponentMeta。"""
        meta: ComponentMeta = {"name": "TestComponent"}
        assert meta["name"] == "TestComponent"


class TestComponentSignature:
    """测试 ComponentSignature TypedDict。"""

    def test_component_signature_creation(self):
        """测试创建 ComponentSignature。"""
        sig: ComponentSignature = {
            "plugin_name": "my_plugin",
            "component_type": ComponentType.ACTION,
            "component_name": "my_action",
        }
        assert sig["plugin_name"] == "my_plugin"
        assert sig["component_type"] == ComponentType.ACTION
        assert sig["component_name"] == "my_action"
