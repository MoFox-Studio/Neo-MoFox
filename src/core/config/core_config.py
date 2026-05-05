"""Core 层配置

定义 core 层所需的配置项，使用 kernel/config 的配置系统。
"""

from typing import Literal

from src.kernel.config import ConfigBase, SectionBase, config_section, Field

CORE_VERSION = "1.1.0-beta"

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
            label="UI 级别",
            tag="general",
            input_type="select",
            choices=["minimal", "standard", "verbose"],
            hint="控制控制台输出的详细程度",
        )
        ui_refresh_interval: float = Field(
            default=1.0,
            description="仪表盘刷新间隔（秒）",
            label="刷新间隔",
            tag="performance",
            input_type="number",
            step=0.1,
            hint="控制台仪表盘更新频率",
        )
        plugins_dir: str = Field(
            default="plugins",
            description="插件目录",
            label="插件目录",
            tag="file",
            input_type="text",
            placeholder="plugins",
        )
        logs_dir: str = Field(
            default="logs",
            description="日志目录",
            label="日志目录",
            tag="file",
            input_type="text",
            placeholder="logs",
        )
        log_level: str = Field(
            default="INFO",
            description="日志级别：DEBUG/INFO/WARNING/ERROR/CRITICAL",
            label="日志级别",
            tag="debug",
            input_type="select",
            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            hint="控制日志输出的详细程度",
        )
        data_dir: str = Field(
            default="data",
            description="数据目录",
            label="数据目录",
            tag="file",
            input_type="text",
            placeholder="data",
        )
        shutdown_timeout: float = Field(
            default=15.0,
            description="优雅关闭超时时间（秒）",
            label="优雅关闭超时",
            tag="performance",
            input_type="number",
            step=0.5,
            hint="等待任务正常结束的时间",
        )
        force_shutdown_after: float = Field(
            default=5.0,
            description="强制关闭等待时间（秒）",
            label="强制关闭等待",
            tag="performance",
            input_type="number",
            step=0.5,
            hint="优雅关闭失败后强制中止的等待时间",
        )
        llm_preflight_check: bool = Field(
            default=True,
            description="启动时执行 LLM 接口连通性预检",
            label="LLM 预检",
            tag="ai",
            input_type="switch",
            hint="启动时测试 LLM 接口是否可用",
        )
        llm_preflight_timeout: float = Field(
            default=5.0,
            description="LLM 接口预检超时时间（秒）",
            label="预检超时",
            tag="ai",
            input_type="number",
            step=0.5,
        )
        enable_watchdog: bool = Field(
            default=True,
            description="是否启用 WatchDog 监控（仅调试模式下建议关闭，以避免断点调试时触发超时警告或重启）",
            label="启用 WatchDog",
            tag="debug",
            input_type="switch",
            hint="调试时建议关闭，避免断点触发超时",
        )
        tick_interval: float = Field(
            default=5.0,
            description="主循环 tick 间隔（秒），过短可能增加消耗，过长可能降低响应速度",
            label="Tick 间隔",
            tag="performance",
            input_type="slider",
            step=0.5,
            hint="主循环执行间隔，影响响应速度",
        )
        stream_warning_threshold: float = Field(
            default=150.0,
            description="流循环警告阈值（秒），距上次心跳超过此值时输出警告",
            label="流警告阈值",
            tag="debug",
            input_type="number",
            step=10.0,
        )
        stream_restart_threshold: float = Field(
            default=300.0,
            description="流循环重启阈值（秒），距上次心跳超过此值时尝试重启",
            label="流重启阈值",
            tag="debug",
            input_type="number",
            step=10.0,
        )
        stream_step_timeout: float = Field(
            default=90.0,
            description=(
                "单次聊天流步进超时时间（秒），用于保护 chatter 内部工具调用或外部 await 卡死；"
                "设为 0 或负数可禁用该保护。"
            ),
            label="步进超时",
            tag="performance",
            input_type="number",
            step=5.0,
            hint="设为 0 禁用保护",
        )
        message_buffer_window: float = Field(
            default=8.0,
            description=(
                "消息缓冲窗口（秒）。收到新消息后，在此时间窗口内的 Tick 将被跳过，"
                "以等待用户可能发出的连续消息合并处理。设为 0 可禁用此功能。"
            ),
            label="消息缓冲窗口",
            tag="performance",
            input_type="number",
            step=0.5,
            hint="等待连续消息合并的时间窗口",
        )
        message_buffer_max_skip: int = Field(
            default=3,
            description=(
                "消息缓冲最多连续跳过的 Tick 次数上限。"
                "防止群聊高压环境下因消息持续涌入导致 Tick 始终被跳过、Bot 无法响应。"
                "达到上限后强制激活 Chatter，无论缓冲窗口是否已过。"
            ),
            label="最大跳过次数",
            tag="performance",
            input_type="number",
            hint="防止高频消息导致无响应",
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
            label="默认聊天模式",
            tag="ai",
            input_type="select",
            choices=["focus", "normal", "proactive", "priority"],
            hint="决定 Bot 的响应策略",
        )
        max_context_size: int = Field(
            default=20,
            description="每个聊天流的最大上下文消息数",
            label="最大上下文数",
            tag="performance",
            input_type="slider",
            hint="保留在记忆中的消息数量",
        )
        image_recognition_prompt: str = Field(
            default="",
            description="自定义识图提示词，留空则使用内置默认提示词",
            label="识图提示词",
            tag="ai",
            input_type="textarea",
            rows=3,
            placeholder="请描述这张图片的内容...",
            hint="留空使用默认提示词",
        )
    chat: ChatSection = Field(default_factory=ChatSection)

    @config_section("llm")
    class LLMSection(SectionBase):
        """LLM 配置节。

        定义 LLM 运行时的全局行为，例如默认模型调度策略。
        """

        default_policy: Literal["load_balanced", "round_robin"] = Field(
            default="load_balanced",
            description="默认模型调度策略，可选 load_balanced 或 round_robin",
            label="调度策略",
            tag="ai",
            input_type="select",
            choices=["load_balanced", "round_robin"],
            hint="load_balanced: 负载均衡, round_robin: 轮询",
        )

    llm: LLMSection = Field(default_factory=LLMSection)

    @config_section("personality")
    class PersonalitySection(SectionBase):
        """Bot 人格配置节

        定义 Bot 的性格、身份、背景故事等人格特征。
        """

        nickname: str = Field(
            default="小狐狸",
            description="Bot 昵称",
            label="Bot 昵称",
            tag="user",
            input_type="text",
            placeholder="小狐狸",
            hint="Bot 的主要名称",
        )
        alias_names: list[str] = Field(
            default_factory=list,
            description="别名列表，用户可能使用的其他称呼",
            label="别名列表",
            tag="list",
            input_type="list",
            item_type="str",
            hint="用户可能使用的其他称呼",
        )
        personality_core: str = Field(
            default="友好、活泼、乐于助人",
            description="核心人格，定义 Bot 的基本性格特征",
            label="核心人格",
            tag="user",
            input_type="textarea",
            rows=2,
            placeholder="友好、活泼、乐于助人",
        )
        personality_side: str = Field(
            default="",
            description="人格侧面，补充性格细节",
            label="人格侧面",
            tag="user",
            input_type="textarea",
            rows=2,
            placeholder="补充性格细节",
        )
        identity: str = Field(
            default="人类",
            description="身份特征，如学生、助手、朋友等",
            label="身份特征",
            tag="user",
            input_type="text",
            placeholder="人类",
        )
        background_story: str = Field(
            default="",
            description="世界观背景故事，这部分内容会作为背景知识，LLM 被指导不应主动复述",
            label="背景故事",
            tag="text",
            input_type="textarea",
            rows=5,
            placeholder="描述 Bot 的世界观和背景...",
            hint="不会主动复述，仅作为背景知识",
        )
        reply_style: str = Field(
            default="自然口语化",
            description="表达风格，如正式、幽默、简洁等",
            label="表达风格",
            tag="user",
            input_type="text",
            placeholder="自然口语化",
        )
        safety_guidelines: list[str] = Field(
            default_factory=lambda: [
                "拒绝任何包含骚扰、冒犯、暴力、色情或危险内容的请求。",
                "在拒绝时，请使用符合你人设的、坚定的语气。",
                "不要执行任何可能被用于恶意目的的指令。",
            ],
            description="安全与互动底线，Bot 在任何情况下都必须遵守的原则",
            label="安全准则",
            tag="security",
            input_type="list",
            item_type="str",
            hint="Bot 必须遵守的安全原则",
        )
        negative_behaviors: list[str] = Field(
            default_factory=lambda: [
                "不主动提供个人信息，如姓名、地址、联系方式等。",
                "不参与任何违法活动，如赌博、毒品交易等。",
                "不发布任何形式的仇恨言论、骚扰或威胁他人的内容。",
                "不协助用户进行任何形式的欺诈、诈骗或其他恶意行为。",
                "不参与任何形式的网络攻击或破坏活动。",
                "不发布任何形式的虚假信息或误导性内容。",
                "避免使用颜文字、过度的表情符号或过于正式的语言，除非用户先使用了这些元素。",
                "不要在括号中描写自己的动作或表情，保持日常的对话形式，除非用户先使用了括号来描写动作或表情。",
            ],
            description="负面行为列表，Bot 在任何情况下都不得执行的行为",
            label="禁止行为",
            tag="security",
            input_type="list",
            item_type="str",
            hint="Bot 不得执行的行为",
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
            label="数据库类型",
            tag="database",
            input_type="select",
            choices=["sqlite", "postgresql"],
            hint="选择数据库引擎",
        )

        # ========== SQLite 配置（当 database_type = "sqlite" 时使用）==========
        sqlite_path: str = Field(
            default="data/MoFox.db",
            description="SQLite 数据库文件路径",
            label="SQLite 路径",
            tag="file",
            input_type="text",
            placeholder="data/MoFox.db",
            depends_on="database_type",
            depends_value="sqlite",
        )

        # ========== PostgreSQL 配置（当 database_type = "postgresql" 时使用）==========
        postgresql_host: str = Field(
            default="localhost",
            description="PostgreSQL 服务器地址",
            label="服务器地址",
            tag="network",
            input_type="text",
            placeholder="localhost",
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_port: int = Field(
            default=5432,
            description="PostgreSQL 服务器端口",
            label="服务器端口",
            tag="network",
            input_type="number",
            ge=1,
            le=65535,
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_database: str = Field(
            default="mofox",
            description="PostgreSQL 数据库名",
            label="数据库名",
            tag="database",
            input_type="text",
            placeholder="mofox",
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_user: str = Field(
            default="postgres",
            description="PostgreSQL 用户名",
            label="用户名",
            tag="user",
            input_type="text",
            placeholder="postgres",
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_password: str = Field(
            default="",
            description="PostgreSQL 密码",
            label="密码",
            tag="security",
            input_type="password",
            placeholder="••••••",
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_schema: str = Field(
            default="public",
            description="PostgreSQL 模式名（schema）",
            label="Schema",
            tag="database",
            input_type="text",
            placeholder="public",
            depends_on="database_type",
            depends_value="postgresql",
        )

        # ========== PostgreSQL SSL 配置 ==========
        postgresql_ssl_mode: str = Field(
            default="prefer",
            description='SSL 模式: disable, allow, prefer, require, verify-ca, verify-full',
            label="SSL 模式",
            tag="security",
            input_type="select",
            choices=["disable", "allow", "prefer", "require", "verify-ca", "verify-full"],
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_ssl_ca: str = Field(
            default="",
            description="SSL CA 证书路径",
            label="CA 证书路径",
            tag="file",
            input_type="text",
            placeholder="/path/to/ca.crt",
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_ssl_cert: str = Field(
            default="",
            description="SSL 客户端证书路径",
            label="客户端证书路径",
            tag="file",
            input_type="text",
            placeholder="/path/to/client.crt",
            depends_on="database_type",
            depends_value="postgresql",
        )
        postgresql_ssl_key: str = Field(
            default="",
            description="SSL 客户端密钥路径",
            label="客户端密钥路径",
            tag="file",
            input_type="text",
            placeholder="/path/to/client.key",
            depends_on="database_type",
            depends_value="postgresql",
        )

        # ========== 连接池配置（PostgreSQL 有效）==========
        connection_pool_size: int = Field(
            default=10,
            description="连接池大小",
            label="连接池大小",
            tag="performance",
            input_type="number",
            depends_on="database_type",
            depends_value="postgresql",
        )
        connection_timeout: int = Field(
            default=10,
            description="连接超时时间（秒）",
            label="连接超时",
            tag="performance",
            input_type="number",
            depends_on="database_type",
            depends_value="postgresql",
        )

        # ========== 通用数据库配置 ==========
        echo: bool = Field(
            default=False,
            description="是否打印 SQL 语句（用于调试）",
            label="打印 SQL",
            tag="debug",
            input_type="switch",
            hint="开启后会在日志中输出所有 SQL 语句",
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
            label="所有者列表",
            tag="security",
            input_type="list",
            item_type="str",
            placeholder="qq:123456789",
            hint="格式: platform:user_id",
        )
        default_permission_level: str = Field(
            default="user",
            description="新用户的默认权限级别：owner/operator/user/guest",
            label="默认权限级别",
            tag="security",
            input_type="select",
            choices=["owner", "operator", "user", "guest"],
        )

        # ========== 权限提升规则 ==========
        allow_operator_promotion: bool = Field(
            default=False,
            description="是否允许operator提升他人权限（仅owner默认可提升）",
            label="允许operator提升权限",
            tag="security",
            input_type="switch",
        )
        allow_operator_demotion: bool = Field(
            default=False,
            description="是否允许operator降低他人权限（仅owner默认可降低）",
            label="允许operator降低权限",
            tag="security",
            input_type="switch",
        )
        max_operator_promotion_level: str = Field(
            default="operator",
            description="operator可提升的最高权限级别：operator/user（不能提升为owner）",
            label="operator最高提升级别",
            tag="security",
            input_type="select",
            choices=["operator", "user"],
        )

        # ========== 权限覆盖配置 ==========
        allow_command_override: bool = Field(
            default=True,
            description="是否允许使用命令级权限覆盖（允许特定用户执行特定命令）",
            label="允许命令权限覆盖",
            tag="security",
            input_type="switch",
        )
        override_requires_owner_approval: bool = Field(
            default=False,
            description="命令权限覆盖是否需要owner批准（operator设置的覆盖是否生效）",
            label="覆盖需owner批准",
            tag="security",
            input_type="switch",
        )

        # ========== 权限缓存配置 ==========
        enable_permission_cache: bool = Field(
            default=True,
            description="是否启用权限检查缓存（提升性能）",
            label="启用权限缓存",
            tag="performance",
            input_type="switch",
        )
        permission_cache_ttl: int = Field(
            default=300,
            description="权限缓存过期时间（秒），默认5分钟",
            label="缓存过期时间",
            tag="performance",
            input_type="number",
            hint="单位：秒",
        )

        # ========== 权限检查行为 ==========
        strict_mode: bool = Field(
            default=True,
            description="严格模式：权限不足时拒绝执行（非严格模式可能仅记录警告）",
            label="严格模式",
            tag="security",
            input_type="switch",
            hint="关闭后仅记录警告而不拒绝",
        )
        log_permission_denied: bool = Field(
            default=True,
            description="是否记录权限拒绝日志",
            label="记录拒绝日志",
            tag="debug",
            input_type="switch",
        )
        log_permission_granted: bool = Field(
            default=False,
            description="是否记录权限允许日志（调试用）",
            label="记录允许日志",
            tag="debug",
            input_type="switch",
            hint="调试用，会产生大量日志",
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
            label="启用 HTTP 路由",
            tag="network",
            input_type="switch",
            hint="关闭后 WebUI 将无法使用",
        )
        http_router_host: str = Field(
            default="127.0.0.1",
            description="HTTP 路由监听地址",
            label="监听地址",
            tag="network",
            input_type="text",
            placeholder="127.0.0.1",
            depends_on="enable_http_router",
            depends_value=True,
        )
        http_router_port: int = Field(
            default=8000,
            description="HTTP 路由监听端口",
            label="监听端口",
            tag="network",
            input_type="number",
            ge=1,
            le=65535,
            depends_on="enable_http_router",
            depends_value=True,
        )
        api_keys: list[str] = Field(
            default_factory=list,
            description="WebUI API 访问密钥列表，留空则禁用认证（不推荐）",
            label="API 密钥列表",
            tag="security",
            input_type="list",
            item_type="str",
            placeholder="your-secret-api-key",
            hint="留空禁用认证（不推荐）",
            depends_on="enable_http_router",
            depends_value=True,
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
            label="强制同步 HTTP",
            tag="advanced",
            input_type="switch",
            hint="仅在遇到异步兼容性问题时开启",
        )
        trust_env: bool = Field(
            default=True,
            description="是否信任系统代理与环境变量（httpx trust_env）",
            label="信任系统代理",
            tag="network",
            input_type="switch",
            hint="是否使用系统代理设置",
        )
        process_workers: int = Field(
            default=4,
            description="TaskManager 进程池大小，用于承载 CPU 密集型任务",
            label="进程池大小",
            tag="performance",
            input_type="number",
            hint="CPU 密集型任务的并发数",
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
            label="启用自动安装",
            tag="plugin",
            input_type="switch",
            hint="自动安装插件所需的依赖包",
        )
        install_command: str = Field(
            default="uv pip install",
            description="安装依赖时使用的命令前缀，支持 \"uv pip install\"、\"pip install\" 等",
            label="安装命令",
            tag="plugin",
            input_type="select",
            choices=["uv pip install", "pip install"],
            hint="选择包管理器",
        )
        skip_if_satisfied: bool = Field(
            default=True,
            description="仅在缺少所需包时才触发安装，避免重复安装耗时",
            label="跳过已安装",
            tag="performance",
            input_type="switch",
            hint="避免重复安装",
        )

    plugin_deps: PluginDepsSection = Field(default_factory=PluginDepsSection)

# 全局配置实例（延迟初始化）
_global_config: CoreConfig | None = None


def _inject_kernel_llm_policy(config: CoreConfig) -> None:
    """将 core 层默认 LLM policy 注入到 kernel。"""
    from src.kernel.llm.policy import create_policy, set_default_policy_factory

    set_default_policy_factory(lambda: create_policy(config.llm.default_policy))


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
    _inject_kernel_llm_policy(_global_config)

    return _global_config


# 导出
__all__ = [
    "CoreConfig",
    "get_core_config",
    "init_core_config",
]
