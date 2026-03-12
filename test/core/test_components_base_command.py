"""测试 src.core.components.base.command 模块。"""



from src.core.components.base.command import BaseCommand, CommandNode
from src.core.components.types import ChatType, PermissionLevel


def cmd_route(*path: str):
    """命令路由装饰器。"""

    def decorator(func):
        func._cmd_route = list(path)
        return func

    return decorator


class ConcreteCommand(BaseCommand):
    """具体的 Command 实现用于测试。"""

    command_name = "test"
    command_description = "Test command"
    command_prefix = "/"
    permission_level = PermissionLevel.USER
    associated_platforms = []
    chat_type = ChatType.ALL

    @cmd_route("set", "value")
    async def handle_set_value(self, value: int) -> tuple[bool, str]:
        """设置值。"""
        return True, f"Value set to {value}"

    @cmd_route("get")
    async def handle_get(self) -> tuple[bool, str]:
        """获取值。"""
        return True, "Value: 42"

    @cmd_route("delete", "confirm")
    async def handle_delete(self) -> tuple[bool, str]:
        """删除确认。"""
        return True, "Deleted"


class TestCommandNode:
    """测试 CommandNode 类。"""

    def test_command_node_creation(self):
        """测试创建命令节点。"""
        node = CommandNode(name="root")
        assert node.name == "root"
        assert node.handler is None
        assert node.children == {}
        assert node.description == ""

    def test_command_node_with_handler(self):
        """测试带处理函数的命令节点。"""
        async def dummy_handler():
            return True, "ok"

        node = CommandNode(name="leaf", handler=dummy_handler, description="Test handler")
        assert node.name == "leaf"
        assert node.handler == dummy_handler
        assert node.description == "Test handler"

    def test_command_node_with_children(self):
        """测试带子节点的命令节点。"""
        parent = CommandNode(name="parent")
        child = CommandNode(name="child")
        parent.children["child"] = child

        assert "child" in parent.children
        assert parent.children["child"] == child


class TestBaseCommand:
    """测试 BaseCommand 类。"""

    def test_command_initialization(self, mock_plugin):
        """测试 Command 初始化。"""
        command = ConcreteCommand(mock_plugin, stream_id="")
        assert command.plugin == mock_plugin
        assert command.command_name == "test"
        assert command.command_prefix == "/"
        assert command.permission_level == PermissionLevel.USER
        assert command._root.name == "root"

    def test_get_signature(self, mock_plugin):
        """测试获取签名。"""
        command = ConcreteCommand(mock_plugin, stream_id="")
        assert command.get_signature() is None

        ConcreteCommand._plugin_ = "my_plugin"
        command2 = ConcreteCommand(mock_plugin, stream_id="")
        assert command2.get_signature() == "my_plugin:command:test"

    def test_match(self, mock_plugin):
        """测试命令匹配。"""
        command = ConcreteCommand(mock_plugin, stream_id="")

        # 匹配 command_name
        assert command.match(["test"]) == 1
        assert command.match(["test", "set"]) == 1

        # 不匹配
        assert command.match(["other"]) == 0
        assert command.match(["test", "get"]) == 1  # 仍然匹配第一个词

    def test_match_empty_parts(self, mock_plugin):
        """测试空片段列表。"""
        command = ConcreteCommand(mock_plugin, stream_id="")
        assert command.match([]) == 0

    def test_build_command_tree(self, mock_plugin):
        """测试构建命令树。"""
        command = ConcreteCommand(mock_plugin, stream_id="")

        # 检查根节点的子节点
        assert "set" in command._root.children
        assert "get" in command._root.children
        assert "delete" in command._root.children

        # 检查 set 子节点
        set_node = command._root.children["set"]
        assert "value" in set_node.children
        assert set_node.children["value"].handler is not None

        # 检查 get 节点
        get_node = command._root.children["get"]
        assert get_node.handler is not None

    def test_register_route(self, mock_plugin):
        """测试注册路由。"""
        command = ConcreteCommand(mock_plugin, stream_id="")

        async def test_handler():
            return True, "test"

        command._register_route(["new", "route"], test_handler)

        # 检查路由是否注册
        assert "new" in command._root.children
        assert "route" in command._root.children["new"].children
        assert command._root.children["new"].children["route"].handler == test_handler

    def test_execute_simple_command(self, mock_plugin):
        """测试执行简单命令。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute("get"))
        assert success is True
        assert result == "Value: 42"

    def test_execute_rejects_prefixed_text(self, mock_plugin):
        """测试 execute 拒绝带前缀的原始命令文本。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute("/get"))
        assert success is False
        assert "去掉前缀后" in result

    def test_execute_nested_command(self, mock_plugin):
        """测试执行嵌套命令。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        # 命令直接是子路由，不需要包含 command_name
        success, result = asyncio.run(command.execute("set value 42"))
        assert success is True
        assert "Value set to 42" in result

    def test_execute_rejects_command_name_text(self, mock_plugin):
        """测试 execute 拒绝仍包含 command_name 的文本。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute("test set value 42"))
        assert success is False
        assert "去掉 command_name 后" in result

    def test_execute_without_prefix(self, mock_plugin):
        """测试执行子路由文本。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute("get"))
        assert success is True
        assert result == "Value: 42"

    def test_execute_empty_command(self, mock_plugin):
        """测试执行空命令。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute(""))
        assert success is False

    def test_execute_invalid_command(self, mock_plugin):
        """测试执行无效命令。"""
        import asyncio

        command = ConcreteCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute("invalid"))
        # 无效命令会生成帮助信息，返回 True
        assert success is True
        assert "未知" in result

    def test_command_with_custom_prefix(self, mock_plugin):
        """测试自定义命令前缀。"""
        class CustomPrefixCommand(BaseCommand):
            command_name = "custom"
            command_prefix = "!"

            @cmd_route("test")
            async def handle_test(self) -> tuple[bool, str]:
                return True, "test result"

        import asyncio

        command = CustomPrefixCommand(mock_plugin, stream_id="")
        success, result = asyncio.run(command.execute("test"))
        assert success is True
        assert result == "test result"

    def test_command_with_permission_levels(self, mock_plugin):
        """测试不同权限级别。"""
        for level in [PermissionLevel.GUEST, PermissionLevel.USER, PermissionLevel.OPERATOR, PermissionLevel.OWNER]:
            class PermCommand(BaseCommand):
                command_name = f"cmd_{level.value}"
                permission_level = level

                @cmd_route("test")
                async def handle(self) -> tuple[bool, str]:
                    return True, "ok"

            command = PermCommand(mock_plugin, stream_id="")
            assert command.permission_level == level


class TestCommandAttributes:
    """测试 Command 类属性。"""

    def test_command_with_all_attributes(self, mock_plugin):
        """测试带有所有属性的命令。"""
        from src.core.components.types import ChatType

        class FullCommand(BaseCommand):
            command_name = "full_command"
            command_description = "Full command description"
            command_prefix = "."
            permission_level = PermissionLevel.OPERATOR
            associated_platforms = ["telegram", "discord"]
            chat_type = ChatType.GROUP
            dependencies = ["other_plugin:service:helper"]

            @cmd_route("test")
            async def handle(self) -> tuple[bool, str]:
                return True, "ok"

        command = FullCommand(mock_plugin, stream_id="")
        assert command.command_name == "full_command"
        assert command.command_description == "Full command description"
        assert command.command_prefix == "."
        assert command.permission_level == PermissionLevel.OPERATOR
        assert command.associated_platforms == ["telegram", "discord"]
        assert command.chat_type == ChatType.GROUP
        assert command.dependencies == ["other_plugin:service:helper"]
