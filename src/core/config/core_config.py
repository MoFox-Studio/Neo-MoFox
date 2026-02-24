"""Core 层配置

定义 core 层所需的配置项，使用 kernel/config 的配置系统。
"""

from src.kernel.config import ConfigBase, SectionBase, config_section, Field

CORE_VERSION = "1.0.0"

class CoreConfig(ConfigBase):
    """Core 层配置类

    定义 Core 层的所有配置节。Core 层包含对话管理、用户管理、消息处理等业务逻辑。
    """
    
    @config_section("bot")
    class BotSection(SectionBase):
        """Bot 配置节

        定义 Bot 基本配置、UI 配置和路径配置。
        """

        ui_level: str = Field(
            default="verbose",
            description="UI 级别：minimal|standard|verbose",
        )
        ui_refresh_interval: float = Field(
            default=1.0,
            description="仪表盘刷新间隔（秒）",
        )
        plugins_dir: str = Field(
            default="plugins",
            description="插件目录",
        )
        logs_dir: str = Field(
            default="logs",
            description="日志目录",
        )
        log_level: str = Field(
            default="INFO",
            description="日志级别：DEBUG/INFO/WARNING/ERROR/CRITICAL",
        )
        data_dir: str = Field(
            default="data",
            description="数据目录",
        )
        shutdown_timeout: float = Field(
            default=15.0,
            description="优雅关闭超时时间（秒）",
        )
        force_shutdown_after: float = Field(
            default=5.0,
            description="强制关闭等待时间（秒）",
        )
        llm_preflight_check: bool = Field(
            default=True,
            description="启动时执行 LLM 接口连通性预检",
        )
        llm_preflight_timeout: float = Field(
            default=5.0,
            description="LLM 接口预检超时时间（秒）",
        )
        tick_interval: float = Field(
            default=15,
            description="主循环 tick 间隔（秒），过短可能增加消耗，过长可能降低响应速度",
        )
        stream_warning_threshold: float = Field(
            default=4.0,
            description="流循环警告阈值（倍数），距上次心跳超过 tick_interval × 此值时输出警告",
        )
        stream_restart_threshold: float = Field(
            default=8.0,
            description="流循环重启阈值（倍数），距上次心跳超过 tick_interval × 此值时尝试重启",
        )

    bot: BotSection = Field(default_factory=BotSection)

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
            default=20,
            description="每个聊天流的最大上下文消息数",
        )
    chat: ChatSection = Field(default_factory=ChatSection)

    @config_section("personality")
    class PersonalitySection(SectionBase):
        """Bot 人格配置节

        定义 Bot 的性格、身份、背景故事等人格特征。
        """

        nickname: str = Field(
            default="小狐狸",
            description="Bot 昵称",
        )
        alias_names: list[str] = Field(
            default_factory=list,
            description="别名列表，用户可能使用的其他称呼",
        )
        personality_core: str = Field(
            default="友好、活泼、乐于助人",
            description="核心人格，定义 Bot 的基本性格特征",
        )
        personality_side: str = Field(
            default="",
            description="人格侧面，补充性格细节",
        )
        identity: str = Field(
            default="人类",
            description="身份特征，如学生、助手、朋友等",
        )
        background_story: str = Field(
            default="",
            description="世界观背景故事，这部分内容会作为背景知识，LLM 被指导不应主动复述",
        )
        reply_style: str = Field(
            default="自然口语化",
            description="表达风格，如正式、幽默、简洁等",
        )
        safety_guidelines: list[str] = Field(
            default_factory=lambda: [
                "拒绝任何包含骚扰、冒犯、暴力、色情或危险内容的请求。",
                "在拒绝时，请使用符合你人设的、坚定的语气。",
                "不要执行任何可能被用于恶意目的的指令。",
            ],
            description="安全与互动底线，Bot 在任何情况下都必须遵守的原则",
        )

    personality: PersonalitySection = Field(default_factory=PersonalitySection)

    @config_section("database")
    class DatabaseSection(SectionBase):
        """数据库配置节

        配置数据库连接和类型相关的参数。
        支持 SQLite 和 PostgreSQL 两种数据库类型。
        """

        # ========== 数据库类型配置 ==========
        database_type: str = Field(
            default="sqlite",
            description='数据库类型，支持 "sqlite" 或 "postgresql"',
        )

        # ========== SQLite 配置（当 database_type = "sqlite" 时使用）==========
        sqlite_path: str = Field(
            default="data/MoFox.db",
            description="SQLite 数据库文件路径",
        )

        # ========== PostgreSQL 配置（当 database_type = "postgresql" 时使用）==========
        postgresql_host: str = Field(
            default="localhost",
            description="PostgreSQL 服务器地址",
        )
        postgresql_port: int = Field(
            default=5432,
            description="PostgreSQL 服务器端口",
        )
        postgresql_database: str = Field(
            default="mofox",
            description="PostgreSQL 数据库名",
        )
        postgresql_user: str = Field(
            default="postgres",
            description="PostgreSQL 用户名",
        )
        postgresql_password: str = Field(
            default="",
            description="PostgreSQL 密码",
        )
        postgresql_schema: str = Field(
            default="public",
            description="PostgreSQL 模式名（schema）",
        )

        # ========== PostgreSQL SSL 配置 ==========
        postgresql_ssl_mode: str = Field(
            default="prefer",
            description='SSL 模式: disable, allow, prefer, require, verify-ca, verify-full',
        )
        postgresql_ssl_ca: str = Field(
            default="",
            description="SSL CA 证书路径",
        )
        postgresql_ssl_cert: str = Field(
            default="",
            description="SSL 客户端证书路径",
        )
        postgresql_ssl_key: str = Field(
            default="",
            description="SSL 客户端密钥路径",
        )

        # ========== 连接池配置（PostgreSQL 有效）==========
        connection_pool_size: int = Field(
            default=10,
            description="连接池大小",
        )
        connection_timeout: int = Field(
            default=10,
            description="连接超时时间（秒）",
        )

        # ========== 通用数据库配置 ==========
        echo: bool = Field(
            default=False,
            description="是否打印 SQL 语句（用于调试）",
        )

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

    permissions: PermissionSection = Field(default_factory=PermissionSection)

    @config_section("http_router")
    class HttpRouterSection(SectionBase):
        """HTTP 路由配置节

        定义 HTTP API 相关的配置参数。
        """

        enable_http_router: bool = Field(
            default=True,
            description="是否启用 HTTP 路由",
        )
        http_router_host: str = Field(
            default="127.0.0.1",
            description="HTTP 路由监听地址",
        )
        http_router_port: int = Field(
            default=8000,
            description="HTTP 路由监听端口",
        )
        api_keys: list[str] = Field(
            default_factory=list,
            description="WebUI API 访问密钥列表，留空则禁用认证（不推荐）",
        )
    http_router: HttpRouterSection = Field(default_factory=HttpRouterSection)

    @config_section("advanced")
    class AdvancedSection(SectionBase):
        """高级配置节

        定义全局请求相关的高级参数。
        """

        force_sync_http: bool = Field(
            default=False,
            description="全局强制使用同步 HTTP（OpenAI SDK 同步路径，仅非流式）",
        )
        trust_env: bool = Field(
            default=True,
            description="是否信任系统代理与环境变量（httpx trust_env）",
        )

    advanced: AdvancedSection = Field(default_factory=AdvancedSection)

    @config_section("plugin_deps")
    class PluginDepsSection(SectionBase):
        """插件依赖自动安装配置节

        控制插件在加载前自动安装所声明的 Python 包依赖。
        """

        enabled: bool = Field(
            default=True,
            description="是否启用插件依赖自动安装，设为 false 则完全跳过",
        )
        install_command: str = Field(
            default="uv pip install",
            description="安装依赖时使用的命令前缀，支持 \"uv pip install\"、\"pip install\" 等",
        )
        skip_if_satisfied: bool = Field(
            default=True,
            description="仅在缺少所需包时才触发安装，避免重复安装耗时",
        )

    plugin_deps: PluginDepsSection = Field(default_factory=PluginDepsSection)

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


def init_core_config(config_path: str) -> CoreConfig:
    """初始化 Core 配置

    Args:
        config_path: 配置文件路径

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

    from pathlib import Path

    path = Path(config_path)

    # 确保配置文件存在
    if not path.exists():
        # 确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)

        # 创建默认配置文件
        default_config = CoreConfig.default()
        _global_config = CoreConfig.model_validate(default_config)

        # 保存默认配置到文件
        from src.kernel.config.core import _render_toml_with_signature
        toml_content = _render_toml_with_signature(CoreConfig, default_config)
        path.write_text(toml_content, encoding="utf-8")

    _global_config = CoreConfig.load(config_path, auto_update=True)

    return _global_config


# 导出
__all__ = [
    "CoreConfig",
    "get_core_config",
    "init_core_config",
]
