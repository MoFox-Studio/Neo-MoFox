"""Core 层配置

定义 core 层所需的配置项，使用 kernel/config 的配置系统。
"""

from src.kernel.config import ConfigBase, SectionBase, config_section, Field





class CoreConfig(ConfigBase):
    """Core 层配置类

    定义 Core 层的所有配置节。Core 层包含对话管理、用户管理、消息处理等业务逻辑。
    """

    @config_section("chat")
    class ChatSection(SectionBase):
        """聊天配置节

        定义聊天相关的配置参数。
        """

        default_chat_mode: str = Field(
            default="normal",
            description="默认聊天模式：focus/normal/proactive/priority",
        )
        max_context_size: int = Field(
            default=100,
            description="每个聊天流的最大上下文消息数",
        )
    chat: ChatSection = Field(default_factory=ChatSection)
    
    @config_section("database")
    class DatabaseSection(SectionBase):
        """数据库配置节

        配置数据库连接和类型相关的参数。
        """

        database_type: str = Field(
            default="sqlite",
            description="数据库类型：sqlite 或 postgresql"
        )

        # 其他数据库配置可以在这里添加
        # url: str = Field(default="", description="数据库连接 URL")
        # echo: bool = Field(default=False, description="是否打印 SQL 语句")
        # pool_size: int = Field(default=5, description="连接池大小")
        # max_overflow: int = Field(default=10, description="连接池最大溢出数")

    database: DatabaseSection = Field(default_factory=DatabaseSection)

    @config_section("permissions")
    class PermissionSection(SectionBase):
        """权限配置节

        定义权限系统相关配置，包括所有者列表、默认权限级别和权限继承规则。
        """

        # ========== 基础权限配置 ==========
        owner_list: list[str] = Field(
            default_factory=list,
            description="Bot所有者列表，格式：['platform:user_id', ...]",
        )
        default_permission_level: str = Field(
            default="user",
            description="新用户的默认权限级别：owner/operator/user/guest",
        )

        # ========== 权限提升规则 ==========
        allow_operator_promotion: bool = Field(
            default=False,
            description="是否允许operator提升他人权限（仅owner默认可提升）",
        )
        allow_operator_demotion: bool = Field(
            default=False,
            description="是否允许operator降低他人权限（仅owner默认可降低）",
        )
        max_operator_promotion_level: str = Field(
            default="operator",
            description="operator可提升的最高权限级别：operator/user（不能提升为owner）",
        )

        # ========== 权限覆盖配置 ==========
        allow_command_override: bool = Field(
            default=True,
            description="是否允许使用命令级权限覆盖（允许特定用户执行特定命令）",
        )
        override_requires_owner_approval: bool = Field(
            default=False,
            description="命令权限覆盖是否需要owner批准（operator设置的覆盖是否生效）",
        )

        # ========== 权限缓存配置 ==========
        enable_permission_cache: bool = Field(
            default=True,
            description="是否启用权限检查缓存（提升性能）",
        )
        permission_cache_ttl: int = Field(
            default=300,
            description="权限缓存过期时间（秒），默认5分钟",
        )

        # ========== 权限检查行为 ==========
        strict_mode: bool = Field(
            default=True,
            description="严格模式：权限不足时拒绝执行（非严格模式可能仅记录警告）",
        )
        log_permission_denied: bool = Field(
            default=True,
            description="是否记录权限拒绝日志",
        )
        log_permission_granted: bool = Field(
            default=False,
            description="是否记录权限允许日志（调试用）",
        )

        # ========== 群组权限配置 ==========
        enable_group_permissions: bool = Field(
            default=False,
            description="是否启用群组级权限（未来扩展）",
        )
        group_admin_permission_level: str = Field(
            default="operator",
            description="群组管理员的默认权限级别",
        )

    permissions: PermissionSection = Field(default_factory=PermissionSection)


# 全局配置实例（延迟初始化）
_global_config: CoreConfig | None = None


def get_core_config() -> CoreConfig:
    """获取全局 Core 配置实例

    Returns:
        CoreConfig: 配置实例

    Raises:
        RuntimeError: 如果配置未初始化
    """
    global _global_config
    if _global_config is None:
        raise RuntimeError(
            "Core config not initialized. "
            "Call init_core_config() first."
        )
    return _global_config


def init_core_config(config_path: str | None = None) -> CoreConfig:
    """初始化 Core 配置

    Args:
        config_path: 配置文件路径，为 None 时使用默认配置

    Returns:
        CoreConfig: 配置实例

    Examples:
        使用默认配置：
        ```python
        config = init_core_config()
        ```

        从文件加载：
        ```python
        config = init_core_config("config/core.toml")
        ```
    """
    global _global_config

    if config_path is None:
        # 使用默认配置
        _global_config = CoreConfig()
    else:
        # 从文件加载配置
        _global_config = CoreConfig.load(config_path)

    return _global_config


# 导出
__all__ = [
    "CoreConfig",
    "get_core_config",
    "init_core_config",
]
