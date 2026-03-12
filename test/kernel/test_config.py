"""
Config 模块单元测试

测试 ConfigBase、SectionBase、config_section 装饰器和 Field 的功能。
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import shutil

import pytest

from src.kernel.config import (
    ConfigBase,
    SectionBase,
    config_section,
    Field,
)
from pydantic import ValidationError


class TestConfigSection:
    """测试 config_section 装饰器"""

    def test_config_section_decorator_adds_metadata(self) -> None:
        """测试装饰器是否正确添加节名称元数据"""
        @config_section("test_section")
        class TestSection(SectionBase):
            pass

        assert hasattr(TestSection, "__config_section_name__")
        assert TestSection.__config_section_name__ == "test_section"  # type: ignore[attr-defined]

    def test_config_section_multiple_sections(self) -> None:
        """测试多个节使用不同名称"""
        @config_section("section_a")
        class SectionA(SectionBase):
            pass

        @config_section("section_b")
        class SectionB(SectionBase):
            pass

        assert SectionA.__config_section_name__ == "section_a"  # type: ignore[attr-defined]
        assert SectionB.__config_section_name__ == "section_b"  # type: ignore[attr-defined]


class TestSectionBase:
    """测试 SectionBase 基类"""

    def test_section_base_creation(self) -> None:
        """测试创建配置节实例"""
        @config_section("test")
        class TestSection(SectionBase):
            name: str = Field(default="test")

        section = TestSection()
        assert section.name == "test"

    def test_section_base_with_required_field(self) -> None:
        """测试带必需字段的配置节"""
        @config_section("test")
        class TestSection(SectionBase):
            required_field: str = Field(...)

        section = TestSection(required_field="value")
        assert section.required_field == "value"

    def test_section_base_forbids_extra_fields(self) -> None:
        """测试配置节禁止额外字段"""
        @config_section("test")
        class TestSection(SectionBase):
            name: str = Field(default="test")

        with pytest.raises(ValidationError):
            TestSection(name="test", extra_field="not_allowed")

    def test_section_base_with_validation(self) -> None:
        """测试字段类型验证"""
        @config_section("test")
        class TestSection(SectionBase):
            count: int = Field(...)

        section = TestSection(count=42)
        assert section.count == 42

        # 类型错误应该抛出 ValidationError
        with pytest.raises(ValidationError):
            TestSection(count="not_an_int")


class TestConfigBase:
    """测试 ConfigBase 基类"""

    def test_configbase_declares_sections_explicitly(self) -> None:
        """测试 ConfigBase 通过显式字段声明配置节（静态可见）"""
        class TestConfig(ConfigBase):
            @config_section("inner")
            class InnerSection(SectionBase):
                version: str = Field(default="1.0.0")

            @config_section("general")
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True)

            inner: InnerSection = Field(default_factory=InnerSection)
            general: GeneralSection = Field(default_factory=GeneralSection)

        config = TestConfig.from_dict({})
        assert config.inner.version == "1.0.0"
        assert config.general.enabled is True

    def test_configbase_empty_model_allows_empty_dict(self) -> None:
        """测试空配置模型可以从空字典加载（但禁止额外字段）"""
        class EmptyConfig(ConfigBase):
            pass

        config = EmptyConfig.from_dict({})
        assert isinstance(config, EmptyConfig)
        with pytest.raises(ValidationError):
            EmptyConfig.from_dict({"unknown": 1})

    def test_configbase_from_dict(self) -> None:
        """测试从字典加载配置"""
        class TestConfig(ConfigBase):
            @config_section("general")
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True)
                name: str = Field(default="test")

            general: GeneralSection = Field(default_factory=GeneralSection)

        data = {
            "general": {
                "enabled": False,
                "name": "custom"
            }
        }

        config = TestConfig.from_dict(data)
        assert config.general.enabled is False
        assert config.general.name == "custom"

    def test_configbase_from_dict_with_defaults(self) -> None:
        """测试从字典加载时使用默认值"""
        class TestConfig(ConfigBase):
            @config_section("general")
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True)
                name: str = Field(default="default_name")

            general: GeneralSection = Field(default_factory=GeneralSection)

        # 空字典应该使用所有默认值
        config = TestConfig.from_dict({})
        assert config.general.enabled is True
        assert config.general.name == "default_name"

    def test_configbase_from_dict_validation_error(self) -> None:
        """测试从字典加载时类型不匹配抛出错误"""
        class TestConfig(ConfigBase):
            @config_section("general")
            class GeneralSection(SectionBase):
                count: int = Field(...)

            general: GeneralSection = Field(default_factory=GeneralSection)

        data = {
            "general": {
                "count": "not_an_int"
            }
        }

        with pytest.raises(ValidationError):
            TestConfig.from_dict(data)

    def test_configbase_load_from_toml_file(self) -> None:
        """测试从 TOML 文件加载配置"""
        class TestConfig(ConfigBase):
            @config_section("inner")
            class InnerSection(SectionBase):
                version: str = Field(default="1.0.0")
                enabled: bool = Field(default=False)

            @config_section("general")
            class GeneralSection(SectionBase):
                option1: str = Field(default="default1")
                option2: int = Field(default=42)

            inner: InnerSection = Field(default_factory=InnerSection)
            general: GeneralSection = Field(default_factory=GeneralSection)

        # 创建临时 TOML 文件
        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "test_config.toml"
            config_content = """
[inner]
version = "2.0.0"
enabled = true

[general]
option1 = "custom_value"
option2 = 100
"""
            config_file.write_text(config_content, encoding="utf-8")

            # 加载配置
            config = TestConfig.load(config_file)

            assert config.inner.version == "2.0.0"
            assert config.inner.enabled is True
            assert config.general.option1 == "custom_value"
            assert config.general.option2 == 100
        finally:
            shutil.rmtree(temp_dir)

    def test_configbase_load_nonexistent_file(self) -> None:
        """测试加载不存在的文件抛出错误"""
        class TestConfig(ConfigBase):
            @config_section("general")
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True)

            general: GeneralSection = Field(default_factory=GeneralSection)

        with pytest.raises(FileNotFoundError):
            TestConfig.load("/nonexistent/path/config.toml")

    def test_configbase_load_malformed_toml(self) -> None:
        """测试加载格式错误的 TOML 文件"""
        class TestConfig(ConfigBase):
            @config_section("general")
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True)

            general: GeneralSection = Field(default_factory=GeneralSection)

        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "malformed.toml"
            config_file.write_text("[invalid", encoding="utf-8")

            with pytest.raises(Exception):  # tomllib.TOMLDecodeError
                TestConfig.load(config_file)
        finally:
            shutil.rmtree(temp_dir)

    def test_configbase_default(self) -> None:
        """测试生成默认配置"""
        class TestConfig(ConfigBase):
            @config_section("inner")
            class InnerSection(SectionBase):
                version: str = Field(default="1.0.0")
                enabled: bool = Field(default=False)

            @config_section("general")
            class GeneralSection(SectionBase):
                option1: str = Field(default="default1")
                option2: int = Field(default=42)

            inner: InnerSection = Field(default_factory=InnerSection)
            general: GeneralSection = Field(default_factory=GeneralSection)

        defaults = TestConfig.default()

        assert defaults == {
            "inner": {
                "version": "1.0.0",
                "enabled": False,
            },
            "general": {
                "option1": "default1",
                "option2": 42,
            }
        }

    def test_configbase_default_with_required_fields(self) -> None:
        """测试存在必填字段时仍可生成默认配置（使用占位值）。"""

        class TestConfig(ConfigBase):
            @config_section("bot")
            class BotSection(SectionBase):
                qq_id: str = Field(description="Bot QQ ID")
                qq_nickname: str = Field(description="Bot QQ 昵称")

            bot: BotSection

        defaults = TestConfig.default()

        assert defaults == {
            "bot": {
                "qq_id": "",
                "qq_nickname": "",
            }
        }

    def test_configbase_multiple_inheritance(self) -> None:
        """测试多层继承时字段继承行为（Pydantic 语义）"""
        class BaseConfig(ConfigBase):
            @config_section("base")
            class BaseSection(SectionBase):
                base_field: str = Field(default="base")

            base: BaseSection = Field(default_factory=BaseSection)

        class DerivedConfig(BaseConfig):
            @config_section("derived")
            class DerivedSection(SectionBase):
                derived_field: str = Field(default="derived")

            derived: DerivedSection = Field(default_factory=DerivedSection)

        cfg = DerivedConfig.from_dict({})
        assert cfg.base.base_field == "base"
        assert cfg.derived.derived_field == "derived"


class TestField:
    """测试 Field 函数（实际上是 Pydantic 的 Field）"""

    def test_field_with_default(self) -> None:
        """测试带默认值的字段"""
        @config_section("test")
        class TestSection(SectionBase):
            value: str = Field(default="default_value")

        section = TestSection()
        assert section.value == "default_value"

    def test_field_with_description(self) -> None:
        """测试带描述的字段"""
        @config_section("test")
        class TestSection(SectionBase):
            value: str = Field(default="test", description="测试字段")

        section = TestSection()
        assert section.value == "test"

    def test_field_required(self) -> None:
        """测试必需字段"""
        @config_section("test")
        class TestSection(SectionBase):
            value: str = Field(...)

        # 缺少必需字段应该抛出错误
        with pytest.raises(ValidationError):
            TestSection()

        # 提供值应该成功
        section = TestSection(value="required")
        assert section.value == "required"

    def test_field_with_constraints(self) -> None:
        """测试带约束的字段"""
        @config_section("test")
        class TestSection(SectionBase):
            value: int = Field(..., ge=0, le=100)

        # 在范围内应该成功
        section = TestSection(value=50)
        assert section.value == 50

        # 超出范围应该抛出错误
        with pytest.raises(ValidationError):
            TestSection(value=150)

        with pytest.raises(ValidationError):
            TestSection(value=-10)


class TestIntegration:
    """集成测试"""

    def test_full_config_workflow(self) -> None:
        """测试完整的配置工作流程"""
        # 定义配置类
        class AppConfig(ConfigBase):
            @config_section("database")
            class DatabaseSection(SectionBase):
                """数据库配置"""
                host: str = Field(default="localhost", description="数据库主机")
                port: int = Field(default=5432, description="数据库端口")
                username: str = Field(default="user", description="用户名")
                password: str = Field(default="pass", description="密码")

            @config_section("features")
            class FeaturesSection(SectionBase):
                """功能开关配置"""
                enable_cache: bool = Field(default=True, description="启用缓存")
                enable_logging: bool = Field(default=False, description="启用日志")

            database: DatabaseSection = Field(default_factory=DatabaseSection)
            features: FeaturesSection = Field(default_factory=FeaturesSection)

        # 生成默认配置
        defaults = AppConfig.default()
        assert "database" in defaults
        assert "features" in defaults

        # 创建临时 TOML 文件
        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "app_config.toml"
            config_content = """
[database]
host = "db.example.com"
port = 3306
username = "admin"
password = "secret123"

[features]
enable_cache = false
enable_logging = true
"""
            config_file.write_text(config_content, encoding="utf-8")

            # 加载配置
            config = AppConfig.load(config_file)

            # 验证加载的值
            assert config.database.host == "db.example.com"
            assert config.database.port == 3306
            assert config.database.username == "admin"
            assert config.database.password == "secret123"
            assert config.features.enable_cache is False
            assert config.features.enable_logging is True

            # 测试从字典创建
            custom_data = {
                "database": {
                    "host": "custom.host",
                    "port": 9999,
                    "username": "custom_user",
                    "password": "custom_pass",
                },
                "features": {
                    "enable_cache": True,
                    "enable_logging": True,
                }
            }
            custom_config = AppConfig.from_dict(custom_data)
            assert custom_config.database.host == "custom.host"
            assert custom_config.database.port == 9999

        finally:
            shutil.rmtree(temp_dir)

    def test_multiple_config_classes(self) -> None:
        """测试多个独立的配置类"""
        class ConfigA(ConfigBase):
            @config_section("section_a")
            class SectionA(SectionBase):
                value_a: str = Field(default="a")

            section_a: SectionA = Field(default_factory=SectionA)

        class ConfigB(ConfigBase):
            @config_section("section_b")
            class SectionB(SectionBase):
                value_b: str = Field(default="b")

            section_b: SectionB = Field(default_factory=SectionB)

        a = ConfigA.from_dict({})
        b = ConfigB.from_dict({})
        assert a.section_a.value_a == "a"
        assert b.section_b.value_b == "b"

    def test_config_with_complex_types(self) -> None:
        """测试包含复杂类型的配置"""
        class ComplexConfig(ConfigBase):
            @config_section("settings")
            class SettingsSection(SectionBase):
                """复杂配置节"""
                string_list: list[str] = Field(default_factory=list)
                int_dict: dict[str, int] = Field(default_factory=dict)
                optional_value: str | None = Field(default=None)

            settings: SettingsSection = Field(default_factory=SettingsSection)

        config = ComplexConfig.from_dict({
            "settings": {
                "string_list": ["a", "b", "c"],
                "int_dict": {"key1": 1, "key2": 2},
                "optional_value": "present",
            }
        })

        assert config.settings.string_list == ["a", "b", "c"]
        assert config.settings.int_dict == {"key1": 1, "key2": 2}
        assert config.settings.optional_value == "present"

        # 测试默认值
        default_config = ComplexConfig.from_dict({})
        assert default_config.settings.string_list == []
        assert default_config.settings.int_dict == {}
        assert default_config.settings.optional_value is None


class TestAutoUpdate:
    """测试 load(auto_update=True) 的自动签名更新能力。"""

    def test_load_auto_update_rewrites_signature_and_preserves_values(self) -> None:
        class AppConfig(ConfigBase):
            @config_section("database")
            class DatabaseSection(SectionBase):
                """数据库配置"""

                host: str = Field(default="localhost", description="数据库主机")
                port: int = Field(default=5432, description="数据库端口")

            @config_section("features")
            class FeaturesSection(SectionBase):
                """功能开关配置"""

                enable_cache: bool = Field(default=True, description="启用缓存")
                enable_logging: bool = Field(default=False, description="启用日志")

            database: DatabaseSection = Field(default_factory=DatabaseSection)
            features: FeaturesSection = Field(default_factory=FeaturesSection)

        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "app_config.toml"
            # - port 在文件中是字符串（类型不一致）应被规范化为 int
            # - 额外字段/额外节应被移除
            # - enable_cache 的用户值应被保留（false）
            config_file.write_text(
                """
[database]
host = \"db.example.com\"
port = \"3306\"
legacy = 1

[features]
enable_cache = false

[unused]
foo = \"bar\"
""".lstrip(),
                encoding="utf-8",
            )

            cfg = AppConfig.load(config_file, auto_update=True)

            assert cfg.database.host == "db.example.com"
            assert cfg.database.port == 3306
            assert cfg.features.enable_cache is False
            assert cfg.features.enable_logging is False

            updated = config_file.read_text(encoding="utf-8")

            # 文档注释应存在
            assert "# 数据库配置" in updated
            assert "# 数据库主机" in updated
            assert "# 数据库端口" in updated
            assert "# 功能开关配置" in updated

            # 签名注释应存在
            assert "# signature:" in updated

            # 多余节/字段被移除
            assert "[unused]" not in updated
            assert "legacy" not in updated

            # 值已被修正/保留
            assert 'host = "db.example.com"' in updated
            assert "port = 3306" in updated
            assert "enable_cache = false" in updated
        finally:
            shutil.rmtree(temp_dir)


class TestListSectionDefaultFactory:
    """测试 list[SectionBase] 字段和嵌套 SectionBase 字段的 default_factory 在首次生成时的行为。"""

    def _build_config_with_list_and_nested(self) -> type[ConfigBase]:
        """构建包含 list[SectionBase] 和嵌套 SectionBase（带预填 default_factory）的配置类。"""

        @config_section("items")
        class ItemSection(SectionBase):
            """列表项配置"""
            name: str = Field(..., description="项名称")
            value: int = Field(default=0, description="项值")

        @config_section("sub_task")
        class SubTaskSection(SectionBase):
            """子任务配置"""
            model_list: list[str] = Field(default_factory=list, description="模型列表")
            max_tokens: int = Field(default=100, description="最大 token 数")

        class TasksSection(SectionBase):
            """任务集合"""
            alpha: SubTaskSection = Field(
                default_factory=lambda: SubTaskSection(model_list=["model-a", "model-b"]),
                description="Alpha 任务",
            )
            beta: SubTaskSection = Field(
                default_factory=lambda: SubTaskSection(model_list=["model-c"], max_tokens=200),
                description="Beta 任务",
            )

        class TestConfig(ConfigBase):
            items: list[ItemSection] = Field(
                default_factory=lambda: [
                    ItemSection(name="first", value=10),
                    ItemSection(name="second", value=20),
                ],
                description="项列表",
            )

            @config_section("tasks")
            class TasksConfig(TasksSection):
                """任务配置"""
                pass

            tasks: TasksConfig = Field(default_factory=TasksConfig)

        return TestConfig

    def test_scenario_a_first_time_empty_raw(self) -> None:
        """场景 A：首次启动，raw 为空字典 → list 应包含 default_factory 的默认项，
        嵌套 SectionBase 应使用 default_factory 的预填值。"""

        TestConfig = self._build_config_with_list_and_nested()
        defaults = TestConfig.default()

        # list[SectionBase] 应包含 2 个默认项
        assert len(defaults["items"]) == 2
        assert defaults["items"][0]["name"] == "first"
        assert defaults["items"][0]["value"] == 10
        assert defaults["items"][1]["name"] == "second"
        assert defaults["items"][1]["value"] == 20

        # 嵌套 SectionBase 应使用 default_factory 预填值，而非子字段的空默认值
        assert defaults["tasks"]["alpha"]["model_list"] == ["model-a", "model-b"]
        assert defaults["tasks"]["alpha"]["max_tokens"] == 100  # SubTaskSection 默认值
        assert defaults["tasks"]["beta"]["model_list"] == ["model-c"]
        assert defaults["tasks"]["beta"]["max_tokens"] == 200  # factory 的预填值

    def test_scenario_b_existing_user_data_preserved(self) -> None:
        """场景 B：用户已有配置数据 → 用户的值不被默认值覆盖。"""

        TestConfig = self._build_config_with_list_and_nested()

        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "test.toml"
            config_file.write_text(
                """
[[items]]
name = "user-item"
value = 99

[tasks]

[tasks.alpha]
model_list = ["user-model-x"]
max_tokens = 500

[tasks.beta]
model_list = ["user-model-y"]
max_tokens = 999
""".lstrip(),
                encoding="utf-8",
            )

            cfg = TestConfig.load(config_file, auto_update=True)

            # 用户的列表项应被保留（只有 1 项，不是默认的 2 项）
            assert len(cfg.items) == 1
            assert cfg.items[0].name == "user-item"
            assert cfg.items[0].value == 99

            # 用户的嵌套值应被保留
            assert cfg.tasks.alpha.model_list == ["user-model-x"]
            assert cfg.tasks.alpha.max_tokens == 500
            assert cfg.tasks.beta.model_list == ["user-model-y"]
            assert cfg.tasks.beta.max_tokens == 999
        finally:
            shutil.rmtree(temp_dir)

    def test_scenario_c_partial_data_new_tasks_get_defaults(self) -> None:
        """场景 C：已有配置但缺少某些新增 task → 新增 task 使用 default_factory 预填值。"""

        TestConfig = self._build_config_with_list_and_nested()

        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "test.toml"
            # 只有 alpha，缺少 beta
            config_file.write_text(
                """
[[items]]
name = "existing"
value = 42

[tasks]

[tasks.alpha]
model_list = ["my-alpha-model"]
max_tokens = 300
""".lstrip(),
                encoding="utf-8",
            )

            cfg = TestConfig.load(config_file, auto_update=True)

            # 已有的值保留
            assert len(cfg.items) == 1
            assert cfg.items[0].name == "existing"
            assert cfg.tasks.alpha.model_list == ["my-alpha-model"]
            assert cfg.tasks.alpha.max_tokens == 300

            # 缺少的 beta 应使用 default_factory 的预填值
            assert cfg.tasks.beta.model_list == ["model-c"]
            assert cfg.tasks.beta.max_tokens == 200
        finally:
            shutil.rmtree(temp_dir)

    def test_auto_update_empty_file_generates_defaults(self) -> None:
        """auto_update=True 对空文件应生成包含 default_factory 预填值的完整 TOML。"""

        TestConfig = self._build_config_with_list_and_nested()

        temp_dir = tempfile.mkdtemp()
        try:
            config_file = Path(temp_dir) / "test.toml"
            config_file.write_text("", encoding="utf-8")

            cfg = TestConfig.load(config_file, auto_update=True)

            # 验证加载的实例
            assert len(cfg.items) == 2
            assert cfg.items[0].name == "first"
            assert cfg.tasks.alpha.model_list == ["model-a", "model-b"]
            assert cfg.tasks.beta.model_list == ["model-c"]
            assert cfg.tasks.beta.max_tokens == 200

            # 验证写出的文件内容包含默认值
            content = config_file.read_text(encoding="utf-8")
            assert "[[items]]" in content
            assert '"first"' in content
            assert '"second"' in content
            assert '"model-a"' in content
            assert '"model-c"' in content
        finally:
            shutil.rmtree(temp_dir)
