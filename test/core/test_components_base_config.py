"""测试 src.core.components.base.config 模块。"""

from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from src.core.components.base.config import BaseConfig
from src.kernel.config import Field, SectionBase, config_section


class TestSection(SectionBase):
    """测试用的配置节。"""

    enabled: bool = Field(default=True, description="启用状态")
    value: int = Field(default=42, description="数值")
    name: str = Field(default="test", description="名称")


class TestConfig(BaseConfig):
    """测试用的配置类。"""

    # 使用 ClassVar 标注类变量
    config_name: ClassVar[str] = "test_config"
    config_description: ClassVar[str] = "Test configuration"
    plugin_name: ClassVar[str] = "test_plugin"

    test_section: TestSection = Field(default_factory=TestSection)


class RequiredSection(SectionBase):
    """包含必填字段的测试配置节。"""

    qq_id: str = Field(description="Bot 的 QQ 账号 ID")
    qq_nickname: str = Field(description="Bot 的 QQ 昵称")


class RequiredConfig(BaseConfig):
    """包含必填字段的测试配置类。"""

    config_name: ClassVar[str] = "required_config"
    config_description: ClassVar[str] = "Required fields config"

    @config_section("bot")
    class BotSection(RequiredSection):
        pass

    bot: BotSection


class TestBaseConfig:
    """测试 BaseConfig 类。"""

    def test_config_initialization(self):
        """测试配置初始化。"""
        config = TestConfig()
        assert config.config_name == "test_config"
        assert config.config_description == "Test configuration"
        assert config.plugin_name == "test_plugin"

    def test_get_default_path(self):
        """测试获取默认路径。"""
        TestConfig._plugin_ = "test_plugin"
        path = TestConfig.get_default_path()
        assert isinstance(path, Path)
        assert path == Path("config/plugins/test_plugin/test_config.toml")
        assert "test_plugin" in str(path)
        assert "test_config.toml" in str(path)
        assert path == Path("config/plugins/test_plugin/test_config.toml")

    def test_get_signature(self):
        """测试获取签名。"""
        # 默认 plugin_name 是 unknown_plugin
        class UnknownConfig(BaseConfig):
            config_name: ClassVar[str] = "unknown"

        assert UnknownConfig.get_signature() is None

        # 设置 _plugin_ 后
        TestConfig._plugin_ = "test_plugin"
        assert TestConfig.get_signature() == "test_plugin:config:test_config"

    def test_get_default_path_different_plugin(self):
        """测试不同插件的默认路径。"""
        class OtherConfig(BaseConfig):
            config_name: ClassVar[str] = "other_config"
            plugin_name: ClassVar[str] = "other_plugin"

        OtherConfig._plugin_ = "other_plugin"
        path = OtherConfig.get_default_path()
        assert path == Path("config/plugins/other_plugin/other_config.toml")

    @patch("src.core.components.base.config.Path.write_text")
    @patch("src.core.components.base.config.Path.mkdir")
    def test_generate_default(self, mock_mkdir, mock_write):
        """测试生成默认配置。"""
        TestConfig._plugin_ = "test_plugin"
        TestConfig.generate_default()

        # 检查是否创建了目录
        mock_mkdir.assert_called_once()

        # 检查是否写入了文件
        mock_write.assert_called_once()

    @patch("src.core.components.base.config.Path.write_text")
    @patch("src.core.components.base.config.Path.mkdir")
    def test_generate_default_custom_path(self, mock_mkdir, mock_write, temp_dir):
        """测试生成默认配置（自定义路径）。"""
        custom_path = temp_dir / "custom_config.toml"
        TestConfig.generate_default(custom_path)

        # 检查是否使用了自定义路径
        mock_mkdir.assert_called_once()
        args = mock_write.call_args
        assert args is not None

    @patch("src.core.components.base.config.Path.write_text")
    @patch("src.core.components.base.config.Path.mkdir")
    def test_generate_default_with_required_fields(self, mock_mkdir, mock_write):
        """测试含必填字段配置也能生成默认配置。"""
        RequiredConfig._plugin_ = "required_plugin"

        RequiredConfig.generate_default()

        mock_mkdir.assert_called_once()
        mock_write.assert_called_once()

        call_args = mock_write.call_args
        assert call_args is not None
        toml_text = call_args.args[0]
        assert "qq_id = \"\"" in toml_text
        assert "qq_nickname = \"\"" in toml_text

    def test_generate_default_no_config_name(self):
        """测试没有 config_name 时生成默认配置。"""
        class NoNameConfig(BaseConfig):
            config_name: ClassVar[str] = ""  # 空名称

        with pytest.raises(RuntimeError, match="必须定义 config_name"):
            NoNameConfig.generate_default()

    @patch("src.core.components.base.config.Path.exists")
    @patch("src.core.components.base.config.BaseConfig.generate_default")
    @patch("src.core.components.base.config.BaseConfig.load")
    def test_load_for_plugin_auto_generate(
        self, mock_load, mock_generate, mock_exists
    ):
        """测试加载插件配置（自动生成）。"""
        mock_exists.return_value = False
        mock_load.return_value = MagicMock()

        result = TestConfig.load_for_plugin("test_plugin", auto_generate=True)

        # 应该先生成默认配置
        mock_generate.assert_called_once()
        # 然后加载
        mock_load.assert_called_once()

    @patch("src.core.components.base.config.Path.exists")
    def test_load_for_plugin_no_auto_generate(self, mock_exists):
        """测试加载插件配置（不自动生成）。"""
        mock_exists.return_value = False

        with pytest.raises(FileNotFoundError, match="配置文件未找到"):
            TestConfig.load_for_plugin("test_plugin", auto_generate=False)

    @patch("src.core.components.base.config.Path.exists")
    @patch("src.core.components.base.config.BaseConfig.load")
    def test_load_for_plugin_file_exists(self, mock_load, mock_exists):
        """测试加载插件配置（文件存在）。"""
        mock_exists.return_value = True
        mock_load.return_value = MagicMock()

        result = TestConfig.load_for_plugin("test_plugin")

        # 不应该生成，应该直接加载
        mock_load.assert_called_once()

    @patch("src.core.components.base.config.Path.exists")
    @patch("src.core.components.base.config.BaseConfig.load")
    def test_reload(self, mock_load, mock_exists):
        """测试重新加载配置。"""
        TestConfig._plugin_ = "test_plugin"
        mock_exists.return_value = True
        mock_load.return_value = MagicMock()

        result = TestConfig.reload()

        mock_load.assert_called_once()
        # 检查是否传递了 auto_update=True
        call_kwargs = mock_load.call_args[1]
        assert call_kwargs.get("auto_update") is True

    @patch("src.core.components.base.config.Path.exists")
    def test_reload_file_not_found(self, mock_exists):
        """测试重新加载（文件不存在）。"""
        TestConfig._plugin_ = "test_plugin"
        mock_exists.return_value = False

        with pytest.raises(FileNotFoundError, match="配置文件未找到"):
            TestConfig.reload()


class TestConfigSection:
    """测试配置节功能。"""

    def test_section_initialization(self):
        """测试配置节初始化。"""
        section = TestSection()
        assert section.enabled is True
        assert section.value == 42
        assert section.name == "test"

    def test_section_custom_values(self):
        """测试配置节自定义值。"""
        section = TestSection(enabled=False, value=100, name="custom")
        assert section.enabled is False
        assert section.value == 100
        assert section.name == "custom"


class TestConfigWithMultipleSections:
    """测试多配置节的配置。"""

    def test_multiple_sections(self):
        """测试多个配置节。"""
        @config_section("section1")
        class Section1(SectionBase):
            value1: str = Field(default="default1", description="Section 1 value")

        @config_section("section2")
        class Section2(SectionBase):
            value2: int = Field(default=2, description="Section 2 value")

        class MultiSectionConfig(BaseConfig):
            config_name: ClassVar[str] = "multi_section"
            plugin_name: ClassVar[str] = "test_plugin"

            section1: Section1 = Field(default_factory=Section1)
            section2: Section2 = Field(default_factory=Section2)

        config = MultiSectionConfig()
        assert config.section1.value1 == "default1"
        assert config.section2.value2 == 2

    def test_nested_sections(self):
        """测试嵌套配置节。"""
        @config_section("inner")
        class InnerSection(SectionBase):
            inner_value: str = Field(default="inner", description="Inner value")

        @config_section("outer")
        class OuterSection(SectionBase):
            outer_value: str = Field(default="outer", description="Outer value")
            inner: InnerSection = Field(default_factory=InnerSection)

        class NestedConfig(BaseConfig):
            config_name: ClassVar[str] = "nested"
            plugin_name: ClassVar[str] = "test_plugin"

            outer: OuterSection = Field(default_factory=OuterSection)

        config = NestedConfig()
        assert config.outer.outer_value == "outer"
        assert config.outer.inner.inner_value == "inner"
