"""测试 CoreConfig 配置模块。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config.core_config import CoreConfig, get_core_config, init_core_config
from src.kernel.llm.policy import RoundRobinPolicy, create_default_policy, set_default_policy_factory


class TestChatSection:
    """测试聊天配置节。"""

    def test_default_chat_config(self):
        """测试默认聊天配置。"""
        config = CoreConfig.ChatSection()

        assert config.default_chat_mode == "normal"
        assert config.max_history_messages == 20

    def test_custom_chat_config(self):
        """测试自定义聊天配置。"""
        config = CoreConfig.ChatSection(
            default_chat_mode="focus",
            max_history_messages=200,
        )

        assert config.default_chat_mode == "focus"
        assert config.max_history_messages == 200


class TestLLMSection:
    """测试 LLM 配置节。"""

    def test_default_llm_config(self):
        """测试默认 LLM 配置。"""
        config = CoreConfig.LLMSection()

        assert config.default_policy == "load_balanced"

    def test_custom_llm_config(self):
        """测试自定义 LLM 配置。"""
        config = CoreConfig.LLMSection(default_policy="round_robin")

        assert config.default_policy == "round_robin"


class TestDatabaseSection:
    """测试数据库配置节。"""

    def test_default_database_config(self):
        """测试默认数据库配置。"""
        config = CoreConfig.DatabaseSection()

        assert config.database_type == "sqlite"

    def test_postgresql_config(self):
        """测试 PostgreSQL 配置。"""
        config = CoreConfig.DatabaseSection(database_type="postgresql")

        assert config.database_type == "postgresql"


class TestPermissionSection:
    """测试权限配置节。"""

    def test_default_permission_config(self):
        """测试默认权限配置。"""
        config = CoreConfig.PermissionSection()

        assert config.owner_list == []
        assert config.default_permission_level == "user"
        assert config.allow_operator_promotion is False
        assert config.allow_operator_demotion is False
        assert config.max_operator_promotion_level == "operator"
        assert config.allow_command_override is True
        assert config.override_requires_owner_approval is False
        assert config.enable_permission_cache is True
        assert config.permission_cache_ttl == 300
        assert config.strict_mode is True
        assert config.log_permission_denied is True
        assert config.log_permission_granted is False

    def test_custom_owner_list(self):
        """测试自定义所有者列表。"""
        config = CoreConfig.PermissionSection(
            owner_list=["qq:123456", "telegram:789012"],
        )

        assert len(config.owner_list) == 2
        assert "qq:123456" in config.owner_list

    def test_enable_operator_promotion(self):
        """测试启用 operator 提升权限。"""
        config = CoreConfig.PermissionSection(
            allow_operator_promotion=True,
            max_operator_promotion_level="user",
        )

        assert config.allow_operator_promotion is True
        assert config.max_operator_promotion_level == "user"

    def test_permission_cache_settings(self):
        """测试权限缓存设置。"""
        config = CoreConfig.PermissionSection(
            enable_permission_cache=True,
            permission_cache_ttl=600,
        )

        assert config.enable_permission_cache is True
        assert config.permission_cache_ttl == 600

    def test_permission_logging_settings(self):
        """测试权限日志设置。"""
        config = CoreConfig.PermissionSection(
            log_permission_denied=False,
            log_permission_granted=True,
        )

        assert config.log_permission_denied is False
        assert config.log_permission_granted is True



class TestChatSectionLegacyKeys:
    """测试 ChatSection 的旧字段兼容（通过 auto_update 剔除）。"""

    def test_init_core_config_strips_legacy_context_validation_mode(self, temp_dir: Path) -> None:
        """旧配置里残留 context_validation_mode 不应导致加载失败，并应被自动移除。"""
        import src.core.config.core_config as core_config_module

        original_config = core_config_module._global_config
        core_config_module._global_config = None

        try:
            config_file = temp_dir / "core.toml"
            config_file.write_text(
                """
[chat]
default_chat_mode = \"focus\"
max_context_size = 150
context_validation_mode = \"repair\"
""".lstrip(),
                encoding="utf-8",
            )

            config = init_core_config(str(config_file))
            assert config.chat.default_chat_mode == "focus"
            assert config.chat.max_history_messages == 150

            updated = config_file.read_text(encoding="utf-8")
            assert "context_validation_mode" not in updated
            assert "max_context_size" not in updated
        finally:
            core_config_module._global_config = original_config


class TestCoreConfig:
    """测试 CoreConfig 主配置类。"""

    def test_create_default_config(self):
        """测试创建默认配置。"""
        config = CoreConfig()

        assert isinstance(config.chat, CoreConfig.ChatSection)
        assert isinstance(config.llm, CoreConfig.LLMSection)
        assert isinstance(config.database, CoreConfig.DatabaseSection)
        assert isinstance(config.permissions, CoreConfig.PermissionSection)

    def test_chat_settings(self):
        """测试聊天配置设置。"""
        config = CoreConfig(
            chat=CoreConfig.ChatSection(
                default_chat_mode="proactive",
                max_history_messages=150,
            )
        )

        assert config.chat.default_chat_mode == "proactive"
        assert config.chat.max_history_messages == 150

    def test_database_settings(self):
        """测试数据库配置设置。"""
        config = CoreConfig(
            database=CoreConfig.DatabaseSection(database_type="postgresql"),
        )

        assert config.database.database_type == "postgresql"

    def test_permission_settings(self):
        """测试权限配置设置。"""
        config = CoreConfig(
            permissions=CoreConfig.PermissionSection(
                owner_list=["qq:123"],
                default_permission_level="operator",
            ),
        )

        assert len(config.permissions.owner_list) == 1
        assert config.permissions.default_permission_level == "operator"

    def test_full_config(self):
        """测试完整配置。"""
        config = CoreConfig(
            chat=CoreConfig.ChatSection(
                default_chat_mode="priority",
                max_history_messages=200,
            ),
            llm=CoreConfig.LLMSection(default_policy="round_robin"),
            database=CoreConfig.DatabaseSection(database_type="postgresql"),
            permissions=CoreConfig.PermissionSection(
                owner_list=["qq:123", "telegram:456"],
                default_permission_level="operator",
                allow_operator_promotion=True,
                strict_mode=False,
            ),
        )

        assert config.chat.default_chat_mode == "priority"
        assert config.chat.max_history_messages == 200
        assert config.llm.default_policy == "round_robin"
        assert config.database.database_type == "postgresql"
        assert len(config.permissions.owner_list) == 2


class TestGlobalCoreConfig:
    """测试全局 Core 配置管理。"""

    def test_init_core_config_default(self, temp_dir: Path):
        """测试使用默认配置初始化。"""
        import src.core.config.core_config as core_config_module
        original_config = core_config_module._global_config
        core_config_module._global_config = None

        try:
            config_path = temp_dir / "core.toml"
            config = init_core_config(str(config_path))
            assert config is not None
            assert isinstance(config, CoreConfig)
        finally:
            core_config_module._global_config = original_config

    def test_init_core_config_from_file(self, temp_dir: Path):
        """测试从文件加载配置。"""
        import src.core.config.core_config as core_config_module
        original_config = core_config_module._global_config
        core_config_module._global_config = None

        try:
            config_file = temp_dir / "core.toml"
            config_file.write_text(
                """
[chat]
default_chat_mode = "focus"
max_context_size = 150

[llm]
default_policy = "round_robin"

[database]
database_type = "postgresql"

[permissions]
owner_list = ["qq:123", "telegram:456"]
default_permission_level = "operator"
allow_operator_promotion = true
"""
            )

            config = init_core_config(str(config_file))
            assert config.chat.default_chat_mode == "focus"
            assert config.chat.max_history_messages == 150
            assert config.llm.default_policy == "round_robin"
            assert config.database.database_type == "postgresql"
            assert len(config.permissions.owner_list) == 2
            assert isinstance(create_default_policy(), RoundRobinPolicy)
        finally:
            set_default_policy_factory(None)
            core_config_module._global_config = original_config

    def test_get_core_config_before_init_raises(self):
        """测试未初始化时获取配置抛出异常。"""
        import src.core.config.core_config as core_config_module
        original_config = core_config_module._global_config
        core_config_module._global_config = None

        try:
            with pytest.raises(RuntimeError, match="Core config not initialized"):
                get_core_config()
        finally:
            core_config_module._global_config = original_config

    def test_get_core_config_after_init(self, temp_dir: Path):
        """测试初始化后获取配置。"""
        import src.core.config.core_config as core_config_module
        original_config = core_config_module._global_config
        core_config_module._global_config = None

        try:
            config_path = temp_dir / "core.toml"
            init_core_config(str(config_path))
            config = get_core_config()

            assert isinstance(config, CoreConfig)
        finally:
            core_config_module._global_config = original_config

    def test_init_core_config_multiple_times(self, temp_dir: Path):
        """测试多次初始化更新配置。"""
        import src.core.config.core_config as core_config_module
        original_config = core_config_module._global_config
        core_config_module._global_config = None

        try:
            config_path = temp_dir / "core.toml"
            init_core_config(str(config_path))
            config2 = init_core_config(str(config_path))

            # 第二次应该返回新创建的实例（因为重新初始化了）
            assert config2 is not None
            assert isinstance(config2, CoreConfig)
            # get_core_config 应该返回第二次初始化的实例
            config3 = get_core_config()
            assert config3 is config2
        finally:
            core_config_module._global_config = original_config


class TestCoreConfigScenarios:
    """测试 Core 配置的实际使用场景。"""

    def test_minimal_config(self):
        """测试最小配置场景。"""
        config = CoreConfig()

        # 应该能使用所有默认值
        assert config.chat.default_chat_mode == "normal"
        assert config.database.database_type == "sqlite"
        assert config.permissions.default_permission_level == "user"

    def test_strict_permissions_config(self):
        """测试严格权限配置场景。"""
        config = CoreConfig(
            permissions=CoreConfig.PermissionSection(
                owner_list=["qq:123"],
                default_permission_level="guest",
                allow_operator_promotion=False,
                allow_command_override=False,
                strict_mode=True,
                log_permission_denied=True,
            ),
        )

        assert config.permissions.default_permission_level == "guest"
        assert config.permissions.strict_mode is True
        assert config.permissions.allow_command_override is False

    def test_development_config(self):
        """测试开发环境配置。"""
        config = CoreConfig(
            chat=CoreConfig.ChatSection(
                default_chat_mode="normal",
                max_history_messages=50,
            ),
            permissions=CoreConfig.PermissionSection(
                owner_list=["qq:123"],
                default_permission_level="owner",
                strict_mode=False,
                log_permission_granted=True,
            ),
        )

        assert config.chat.max_history_messages == 50
        assert config.permissions.strict_mode is False
        assert config.permissions.log_permission_granted is True

    def test_production_config(self):
        """测试生产环境配置。"""
        config = CoreConfig(
            database=CoreConfig.DatabaseSection(database_type="postgresql"),
            chat=CoreConfig.ChatSection(
                default_chat_mode="priority",
                max_history_messages=200,
            ),
            permissions=CoreConfig.PermissionSection(
                owner_list=["qq:123", "telegram:456"],
                default_permission_level="user",
                enable_permission_cache=True,
                permission_cache_ttl=300,
                strict_mode=True,
            ),
        )

        assert config.database.database_type == "postgresql"
        assert config.chat.max_history_messages == 200
        assert config.permissions.enable_permission_cache is True

    def test_multi_owner_config(self):
        """测试多所有者配置。"""
        config = CoreConfig(
            permissions=CoreConfig.PermissionSection(
                owner_list=[
                    "qq:123456",
                    "qq:789012",
                    "telegram:345678",
                    "discord:901234",
                ],
            ),
        )

        assert len(config.permissions.owner_list) == 4
        assert "qq:123456" in config.permissions.owner_list
