"""测试 src.core.components.base.tool 模块。"""


import pytest

from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType


class ConcreteTool(BaseTool):
    """具体的 Tool 实现用于测试。"""

    tool_name = "test_tool"
    tool_description = "A test tool"
    chatter_allow = []
    chat_type = ChatType.ALL
    associated_platforms = []

    async def execute(self, query: str) -> tuple[bool, str]:
        """执行测试工具。"""
        return True, f"Result: {query}"


class TestBaseTool:
    """测试 BaseTool 类。"""

    @pytest.fixture(autouse=True)
    def reset_class_attributes(self):
        """在每个测试前重置类属性。"""
        # 备份原始值
        original_plugin_name = getattr(ConcreteTool, "_plugin_", None)
        yield
        # 恢复原始值
        if original_plugin_name:
            ConcreteTool._plugin_ = original_plugin_name
        elif hasattr(ConcreteTool, "_plugin_"):
            delattr(ConcreteTool, "_plugin_")

    def test_tool_initialization(self, mock_plugin):
        """测试 Tool 初始化。"""
        tool = ConcreteTool(mock_plugin)
        assert tool.plugin == mock_plugin
        assert tool.tool_name == "test_tool"
        assert tool.tool_description == "A test tool"

    def test_get_signature(self, mock_plugin):
        """测试获取签名。"""
        # 默认情况下 plugin_name 是 unknown_plugin
        tool = ConcreteTool(mock_plugin)
        assert tool.get_signature() is None

        # 设置 plugin_name 后应该返回签名
        ConcreteTool._plugin_ = "my_plugin"
        tool2 = ConcreteTool(mock_plugin)
        assert tool2.get_signature() == "my_plugin:tool:test_tool"

    def test_execute(self, mock_plugin):
        """测试 execute 方法。"""
        import asyncio

        tool = ConcreteTool(mock_plugin)
        success, result = asyncio.run(tool.execute("test query"))
        assert success is True
        assert result == "Result: test query"

    def test_execute_return_dict(self, mock_plugin):
        """测试 execute 返回字典。"""
        import asyncio

        class DictTool(BaseTool):
            tool_name = "dict_tool"
            tool_description = "Tool returning dict"

            async def execute(self, key: str) -> tuple[bool, dict]:
                return True, {"result": f"value_for_{key}"}

        tool = DictTool(mock_plugin)
        success, result = asyncio.run(tool.execute("my_key"))
        assert success is True
        assert result == {"result": "value_for_my_key"}

    def test_to_schema(self, mock_plugin):
        """测试生成 schema。"""
        tool = ConcreteTool(mock_plugin)
        schema = tool.to_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "tool:test_tool"
        assert schema["function"]["description"] == "A test tool"
        assert "parameters" in schema["function"]
        assert schema["function"]["parameters"]["type"] == "object"
        assert "properties" in schema["function"]["parameters"]
        assert "query" in schema["function"]["parameters"]["properties"]

    def test_to_schema_with_complex_params(self, mock_plugin):
        """测试复杂参数的 schema 生成。"""
        from typing import Annotated

        class ComplexTool(BaseTool):
            tool_name = "complex_tool"
            tool_description = "Tool with complex parameters"

            async def execute(
                self,
                name: Annotated[str, "用户名称"],
                age: Annotated[int, "用户年龄"],
                active: Annotated[bool, "是否激活"] = True,
            ) -> tuple[bool, str]:
                return True, f"User: {name}, Age: {age}, Active: {active}"

        tool = ComplexTool(mock_plugin)
        schema = tool.to_schema()

        params = schema["function"]["parameters"]
        assert "name" in params["properties"]
        assert "age" in params["properties"]
        assert "active" in params["properties"]
        assert "name" in params["required"]
        assert "age" in params["required"]
        assert "active" not in params["required"]


class TestToolAttributes:
    """测试 Tool 类属性。"""

    def test_tool_with_custom_attributes(self, mock_plugin):
        """测试自定义属性的 Tool。"""
        from src.core.components.types import ChatType

        class CustomTool(BaseTool):
            tool_name = "custom_tool"
            tool_description = "Custom tool description"
            chatter_allow = ["chatter1"]
            chat_type = ChatType.PRIVATE
            associated_platforms = ["telegram"]
            dependencies = ["other_plugin:tool:database"]

            async def execute(self, data: str) -> tuple[bool, str]:
                return True, "done"

        tool = CustomTool(mock_plugin)
        assert tool.tool_name == "custom_tool"
        assert tool.tool_description == "Custom tool description"
        assert tool.chatter_allow == ["chatter1"]
        assert tool.chat_type == ChatType.PRIVATE
        assert tool.associated_platforms == ["telegram"]
        assert tool.dependencies == ["other_plugin:tool:database"]

    def test_tool_with_chat_type_group(self, mock_plugin):
        """测试不同聊天类型的 Tool。"""
        from src.core.components.types import ChatType

        # 分别测试每种聊天类型
        class PrivateTool(BaseTool):
            tool_name = "tool_private"
            chat_type = ChatType.PRIVATE

            async def execute(self) -> tuple[bool, str]:
                return True, "done"

        class GroupTool(BaseTool):
            tool_name = "tool_group"
            chat_type = ChatType.GROUP

            async def execute(self) -> tuple[bool, str]:
                return True, "done"

        class DiscussTool(BaseTool):
            tool_name = "tool_discuss"
            chat_type = ChatType.DISCUSS

            async def execute(self) -> tuple[bool, str]:
                return True, "done"

        class AllTool(BaseTool):
            tool_name = "tool_all"
            chat_type = ChatType.ALL

            async def execute(self) -> tuple[bool, str]:
                return True, "done"

        # 测试每种类型
        assert PrivateTool(mock_plugin).chat_type == ChatType.PRIVATE
        assert GroupTool(mock_plugin).chat_type == ChatType.GROUP
        assert DiscussTool(mock_plugin).chat_type == ChatType.DISCUSS
        assert AllTool(mock_plugin).chat_type == ChatType.ALL
