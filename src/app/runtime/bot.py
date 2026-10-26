"""Bot 主类

Neo-MoFox 框架的核心协调器，负责系统初始化、插件加载和生命周期管理。
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlparse

from src.core.config import CORE_VERSION

from .console_ui import ConsoleUIManager
from .exceptions import BotInitializationError, BotRuntimeError, BotShutdownError
from .signal_handler import SignalHandler

if TYPE_CHECKING:
    from src.core.config import CoreConfig
    from src.core.components import PluginLoader, PluginManifest
    from src.core.managers import MCPManager, PluginManager
    from src.core.managers import MediaManager
    from src.core.transport import MessageReceiver, HTTPServer, SinkManager
    from src.kernel.concurrency import TaskManager, WatchDog
    from src.kernel.event import EventBus
    from src.kernel.logger import Logger
    from src.kernel.scheduler import UnifiedScheduler
    from src.kernel.storage import JSONStore
    from src.kernel.vector_db import VectorDBBase

    from .command_parser import CommandParser

class Bot:
    """Neo-MoFox Bot 主类

    管理完整的 Bot 生命周期，包括：
    - Kernel 层初始化
    - Core 层组件加载
    - 插件发现和加载
    - 运行时管理
    - 优雅关闭

    Attributes:
        bot_name: Bot 名称
        bot_version: Bot 版本
        config_path: 配置文件路径
        plugins_dir: 插件目录
        log_dir: 日志目录
    """

    bot_name: str = "Neo-MoFox"
    bot_version: str = CORE_VERSION
    def __init__(
        self,
        config_path: str = "config/core.toml",
        plugins_dir: str = "plugins",
        log_dir: str = "logs",
    ) -> None:
        """初始化 Bot

        Args:
            config_path: 配置文件路径
            plugins_dir: 插件目录
            log_dir: 日志目录
        """
        self.config_path = config_path
        self.plugins_dir = plugins_dir
        self.log_dir = log_dir

        # UI 管理器
        self.ui = ConsoleUIManager()

        # 状态标志
        self._initialized = False
        self._running = False
        self._shutdown_requested = False

        # Kernel 层组件（延迟初始化）
        self.config: CoreConfig | None = None
        self.logger: Logger | None = None
        self.event_bus: EventBus | None = None
        self.task_manager: TaskManager | None = None
        self.watchdog: WatchDog | None = None
        self.vector_db: VectorDBBase | None = None
        self.scheduler: UnifiedScheduler | None = None
        self.storage: JSONStore | None = None

        # Core 层组件（延迟初始化）
        self.message_receiver: MessageReceiver | None = None
        self.sink_manager: SinkManager | None = None
        self.plugin_loader: PluginLoader | None = None
        self.plugin_manager: PluginManager | None = None
        self.mcp_manager: MCPManager | None = None
        self.media_manager: MediaManager | None = None
        self.http_server: HTTPServer | None = None
        self.load_order: list[str] = []
        self.manifests: dict[str, PluginManifest] = {}
        self.load_results: dict[str, bool] = {}
        self._telemetry_log_unsubscribe: Any | None = None
        # 在初始化调用前登记，以覆盖“资源创建后初始化抛错”的情况。
        self._owned_resources: set[str] = set()
        self._stream_loop_manager: Any | None = None

        # DNS 专用线程池与原始 loop 方法（用于关闭时恢复）
        self._dns_executor: ThreadPoolExecutor | None = None
        self._default_executor: ThreadPoolExecutor | None = None
        self._previous_default_executor: ThreadPoolExecutor | None = None
        self._original_getaddrinfo: Any | None = None
        self._original_getnameinfo: Any | None = None

        # 日志清理启停函数引用（由 initialize() 中 initialize_logger_system 后赋值，
        # 由 run()/shutdown() 在调度器启停时调用）
        self._start_logger_cleanup: Callable[[], Awaitable[None]] | None = None
        self._stop_logger_cleanup: Callable[[], Awaitable[None]] | None = None

        # 信号处理器引用（由 run() 中创建，shutdown() 完成后卸下）
        # 保留到 shutdown 之后，让 shutdown 期间的第二次 Ctrl+C 仍能强制退出。
        self._signal_handler: SignalHandler | None = None

        # 命令解析器引用（由 run() 中创建，shutdown() 末尾关闭）
        # 保留到 shutdown 之后，让 shutdown 期间第二次 Ctrl+C 仍能被
        # input worker 读取并 raise_signal 触发强制退出。
        self._command_parser: CommandParser | None = None

        # 统计数据
        self._stats: dict[str, int | bool | dict] = {
            "plugins_loaded": 0,
            "plugins_failed": 0,
            "components_by_type": {},
        }

    async def initialize(self) -> None:
        """按依赖顺序准备内核、核心服务和插件。

        调度器在此阶段只接收任务注册，由 run() 启动执行循环。
        已完成初始化时重复调用无副作用；关闭后的 Bot 不可复用。

        Raises:
            BotInitializationError: 初始化失败
        """
        if self._shutdown_requested:
            raise BotInitializationError("Bot 已进入关闭流程", "initialize")
        if self._initialized:
            return
        try:
            # 步骤 1：展示启动信息，准备线程池和 DNS 运行环境。
            self.ui.show_banner(self.bot_version, self.bot_name)

            # 启动前优化 async 连接池/DNS 行为
            await self._optimize_async_network_runtime()

            # 单一总体进度条贯穿全部初始化阶段
            with self.ui.startup_progress(total_steps=21):
                # 步骤 2：准备配置、事件、并发和存储基础设施。
                await self._initialize_kernel()

                # 步骤 3：数据库就绪后显式准备核心服务。
                await self._initialize_core()

                # 步骤 4：同步、发现和加载插件，允许预注册定时任务。
                await self._initialize_plugins()
                # 步骤 5：校验必需依赖后才标记初始化成功。
                self._validate_startup_contract()

            self._initialized = True

            # 显示成功消息
            loaded = len([r for r in self.load_results.values() if r])
            total = len(self.load_results)
            failed = total - loaded

            if failed > 0:
                self.ui.display_warning(
                    f"Bot 已初始化，加载了 {loaded}/{total} 个插件（{failed} 个失败）"
                )
            else:
                self.ui.display_success(
                    f"Bot 初始化成功，加载了 {total} 个插件"
                )

            await self._record_telemetry_event(
                domain="runtime",
                event_name="initialized",
                summary="bot initialized",
                attributes={
                    "loaded_plugins": loaded,
                    "failed_plugins": failed,
                    "version": self.bot_version,
                },
            )
            await self._record_runtime_snapshot(event_name="initialize_complete")

        except Exception as e:
            await self._record_telemetry_event(
                domain="error",
                event_name="initialization_failed",
                severity="error",
                summary="bot initialization failed",
                attributes={
                    "error_type": type(e).__name__,
                    "phase": "initialize",
                },
                detail={"message": str(e)},
            )
            self.ui.display_error(f"Initialization failed: {e}", e)
            raise BotInitializationError(str(e), "unknown") from e

    async def _optimize_async_network_runtime(self) -> None:
        """优化异步网络运行时：线程池与 DNS 预解析。"""
        loop = asyncio.get_running_loop()

        # 保存原始方法，供 shutdown 时恢复
        self._original_getaddrinfo = loop.getaddrinfo
        self._original_getnameinfo = loop.getnameinfo

        # 默认线程池：承载 to_thread / run_in_executor(None, ...)
        # asyncio 没有公开的默认线程池 getter；用动态访问兼容抽象循环类型。
        previous_executor = getattr(loop, "_default_executor", None)
        self._previous_default_executor = (
            previous_executor if isinstance(previous_executor, ThreadPoolExecutor) else None
        )
        self._default_executor = ThreadPoolExecutor(max_workers=192)
        loop.set_default_executor(self._default_executor)

        # DNS 专用线程池：避免 getaddrinfo 被通用任务挤占
        self._dns_executor = ThreadPoolExecutor(max_workers=16)

        async def _patched_getaddrinfo(host, port, *args, **kwargs):
            func = partial(socket.getaddrinfo, host, port, *args, **kwargs)
            return await loop.run_in_executor(self._dns_executor, func)

        async def _patched_getnameinfo(sockaddr, flags=0):
            func = partial(socket.getnameinfo, sockaddr, flags)
            return await loop.run_in_executor(self._dns_executor, func)

        loop.getaddrinfo = _patched_getaddrinfo  # type: ignore[method-assign]
        loop.getnameinfo = _patched_getnameinfo  # type: ignore[method-assign]

        # 预解析 provider 域名，减少首包抖动
        targets = self._extract_provider_hosts_from_model_config("config/model.toml")
        if not targets:
            return

        async def _resolve(host: str, port: int) -> None:
            try:
                await asyncio.wait_for(
                    loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                    timeout=5.0,
                )
            except Exception:
                return

        await asyncio.gather(
            *(_resolve(host, port) for host, port in targets),
            return_exceptions=True,
        )

    @staticmethod
    def _extract_provider_hosts_from_model_config(
        model_config_path: str,
    ) -> list[tuple[str, int]]:
        """从模型配置提取 provider 的 (host, port) 列表。"""
        try:
            with open(model_config_path, "rb") as f:
                config = tomllib.load(f)
        except Exception:
            return []

        providers = config.get("api_providers", [])
        if not isinstance(providers, list):
            return []

        out: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for item in providers:
            if not isinstance(item, dict):
                continue
            base_url = item.get("base_url")
            if not isinstance(base_url, str) or not base_url:
                continue

            parsed = urlparse(base_url)
            host = parsed.hostname
            if not host:
                continue

            if parsed.port is not None:
                port = parsed.port
            elif parsed.scheme == "https":
                port = 443
            else:
                port = 80

            key = (host, int(port))
            if key not in seen:
                seen.add(key)
                out.append(key)

        return out

    async def _initialize_kernel(self) -> None:
        """按配置、事件、并发、存储的依赖顺序准备基础设施。"""
        self.ui.update_phase_status("初始化内核", "启动中...")
        # 步骤 1：加载配置与日志，执行可选连接预检。
        self._initialize_config()
        self._initialize_logger()
        await self._preflight_llm_providers()
        # 步骤 2：准备事件总线和本地遥测订阅。
        self._initialize_event_bus()
        await self._initialize_telemetry()
        # 步骤 3：先创建任务管理器，再创建调度器和看门狗。
        self._initialize_task_manager()
        self._initialize_scheduler()
        self._initialize_watchdog()
        # 步骤 4：准备数据库、向量库和文件存储。
        await self._initialize_database()
        self._initialize_vector_database()
        self._initialize_storage()
        # 步骤 5：准备统计与云端遥测；发送循环留到 run()。
        await self._initialize_llm_statistics()
        await self._initialize_cloud_telemetry()

    def _initialize_config(self) -> None:
        """加载 Core、模型和 MCP 配置，供后续阶段使用。"""
        from src.core.config import init_core_config, init_mcp_config, init_model_config

        self.config = init_core_config(self.config_path)
        init_model_config("config/model.toml")
        init_mcp_config("config/mcp.toml")
        self.ui.update_phase_status("配置", "已加载")

    def _initialize_logger(self) -> None:
        """初始化日志并保存清理启停函数，周期任务由运行阶段注册。"""
        assert self.config is not None
        from src.kernel.logger import (
            get_logger,
            initialize_logger_system,
            start_logger_cleanup,
            stop_logger_cleanup,
            COLOR,
        )

        # 初始化日志系统（含日志自动清理管理器的创建）
        # Bot 在运行阶段调用 start_logger_cleanup() 注册清理任务
        self._owned_resources.add("logger")
        initialize_logger_system(
            log_dir=self.log_dir,
            log_level=self.config.bot.log_level,
            log_cleanup_enabled=self.config.bot.log_cleanup_enabled,
            log_max_age_days=self.config.bot.log_max_age_days,
            log_max_files=self.config.bot.log_max_files,
            log_cleanup_interval_hours=self.config.bot.log_cleanup_interval_hours,
        )
        self.logger = get_logger(name="console", display="控制台", color=COLOR.BLUE)
        # 保存启停函数引用，供 run()/shutdown() 调用
        self._start_logger_cleanup = start_logger_cleanup
        self._stop_logger_cleanup = stop_logger_cleanup

        self.ui.update_phase_status("日志", "已初始化")

    def _initialize_event_bus(self) -> None:
        """设置事件处理超时并取得核心服务共享的事件总线。"""
        assert self.config is not None
        from src.kernel.event import get_event_bus, set_event_handler_timeout

        set_event_handler_timeout(self.config.advanced.event_handler_timeout)
        self.event_bus = get_event_bus()
        self.ui.update_phase_status("事件总线", "已初始化")

    async def _initialize_telemetry(self) -> None:
        """准备本地遥测存储并在已初始化的事件总线上订阅日志。"""
        assert self.config is not None
        from src.kernel.telemetry import TelemetryConfig, init_telemetry

        self._owned_resources.add("telemetry")
        await init_telemetry(
            config=TelemetryConfig(
                enabled=self.config.telemetry.enabled,
                max_records=self.config.telemetry.max_records,
                max_age_days=self.config.telemetry.max_age_days,
                detail_enabled=self.config.telemetry.detail_enabled,
                hash_salt=self.config.telemetry.hash_salt,
                slow_query_threshold_ms=self.config.telemetry.slow_query_threshold_ms,
                collect_error_events=self.config.telemetry.collect_error_events,
                collect_watchdog_events=self.config.telemetry.collect_watchdog_events,
                collect_db_metrics=self.config.telemetry.collect_db_metrics,
                collect_plugin_events=self.config.telemetry.collect_plugin_events,
                collect_tool_events=self.config.telemetry.collect_tool_events,
                collect_runtime_snapshots=self.config.telemetry.collect_runtime_snapshots,
            ),
        )
        self._install_telemetry_hooks()
        self.ui.update_phase_status("遥测", "已初始化")

    def _initialize_task_manager(self) -> None:
        """按配置创建任务管理器，必须先于调度器创建。"""
        assert self.config is not None
        from src.kernel.concurrency import get_task_manager

        self.task_manager = get_task_manager(
            process_workers=self.config.advanced.process_workers
        )
        self.ui.update_phase_status("任务管理器", "已初始化")

    def _initialize_scheduler(self) -> None:
        """取得统一调度器供插件预注册任务，暂不启动检查循环。"""
        from src.kernel.scheduler import get_unified_scheduler

        self.scheduler = get_unified_scheduler()
        self.ui.update_phase_status("调度器", "已初始化")

    def _initialize_watchdog(self) -> None:
        """创建并按配置启动看门狗，以监控后续初始化过程。"""
        assert self.config is not None
        assert self.logger is not None
        from src.kernel.concurrency import get_watchdog

        self.watchdog = get_watchdog()
        if self.config.bot.enable_watchdog:
            self.watchdog.start()
        else:
            self.logger.warning("WatchDog 已禁用 (调试模式)")
        self.ui.update_phase_status("看门狗", "已初始化")

    async def _initialize_database(self) -> None:
        """连接主数据库并完成结构同步，失败时阻止核心服务加载。"""
        assert self.config is not None
        from src.kernel.db import init_database_from_config

        db_cfg = self.config.database
        self._owned_resources.add("database")
        await init_database_from_config(
            database_type=db_cfg.database_type,
            sqlite_path=db_cfg.sqlite_path,
            postgresql_host=db_cfg.postgresql_host,
            postgresql_port=db_cfg.postgresql_port,
            postgresql_database=db_cfg.postgresql_database,
            postgresql_user=db_cfg.postgresql_user,
            postgresql_password=db_cfg.postgresql_password,
            postgresql_schema=db_cfg.postgresql_schema,
            postgresql_ssl_mode=db_cfg.postgresql_ssl_mode,
            postgresql_ssl_ca=db_cfg.postgresql_ssl_ca,
            postgresql_ssl_cert=db_cfg.postgresql_ssl_cert,
            postgresql_ssl_key=db_cfg.postgresql_ssl_key,
            connection_pool_size=db_cfg.connection_pool_size,
            connection_timeout=db_cfg.connection_timeout,
            echo=db_cfg.echo,
        )

        from src.core.utils.schema_sync import enforce_database_schema_consistency

        sync_stats = await enforce_database_schema_consistency()
        if self.logger:
            self.logger.info(
                "数据库结构已对齐: "
                f"tables={sync_stats.tables_checked}, "
                f"add={sync_stats.columns_added}, "
                f"drop={sync_stats.columns_removed}, "
                f"type={sync_stats.columns_type_altered}, "
                f"nullable={sync_stats.columns_nullability_altered}"
            )

        self._stats["db_connected"] = True
        self.ui.update_phase_status("数据库", "已连接")

    def _initialize_vector_database(self) -> None:
        """准备向量数据库目录并取得服务实例。"""
        from src.kernel.vector_db import get_vector_db_service

        # 确保 data 目录存在
        Path("data/chroma_db").mkdir(parents=True, exist_ok=True)
        self._owned_resources.add("vector_db")
        self.vector_db = get_vector_db_service("data/chroma_db")
        self.ui.update_phase_status("向量数据库", "已初始化")

    def _initialize_storage(self) -> None:
        """创建 JSON 存储目录与存储实例。"""
        from src.kernel.storage import JSONStore

        # 确保 data 目录存在
        Path("data/json_storage").mkdir(parents=True, exist_ok=True)
        self.storage = JSONStore("data/json_storage")
        self.ui.update_phase_status("存储", "已初始化")

    async def _initialize_llm_statistics(self) -> None:
        """按配置初始化独立的 LLM 统计存储。"""
        from src.kernel.llm.stats import LLMStatsConfig, init_llm_stats
        assert self.config is not None
        self._owned_resources.add("llm_stats")
        await init_llm_stats(
            config=LLMStatsConfig(
                db_path=self.config.llm_stats.db_path,
                enabled=self.config.llm_stats.enabled,
                max_records=self.config.llm_stats.max_records,
                window_hours=self.config.llm_stats.window_hours,
            ),
        )
        self.ui.update_phase_status("LLM统计", "已初始化")

    async def _initialize_cloud_telemetry(self) -> None:
        """准备云端遥测运行时，发送循环由运行阶段显式启动。"""
        assert self.config is not None
        from src.app.cloud_telemetry import initialize_cloud_telemetry_runtime

        self._owned_resources.add("cloud_telemetry")
        await initialize_cloud_telemetry_runtime(self.config)
        self.ui.update_phase_status("云端遥测", "已初始化")

    async def _preflight_llm_providers(self) -> None:
        """可选地探测模型提供商，记录连接情况；探测失败不阻止启动。"""
        assert self.config is not None
        if not self.config.bot.llm_preflight_check:
            self.ui.update_phase_status("LLM 预检", "已跳过")
            return

        assert self.logger is not None

        from src.core.config import get_model_config
        import httpx
        import time

        providers = get_model_config().api_providers
        if not providers:
            self.logger.warning("LLM 预检: 未配置任何 API 提供商")
            self.ui.update_phase_status("LLM 预检", "无配置")
            return

        timeout = float(self.config.bot.llm_preflight_timeout or 5.0)
        self.ui.update_phase_status("LLM 预检", "进行中...")
        logger = self.logger

        async with httpx.AsyncClient(timeout=timeout) as client:
            async def _check_provider(provider) -> None:
                base_url = str(provider.base_url).rstrip("/")
                url = f"{base_url}/models"
                headers: dict[str, str] = {}
                try:
                    api_key = provider.get_api_key()
                except Exception:
                    api_key = ""
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                start = time.perf_counter()
                try:
                    resp = await client.get(url, headers=headers)
                    elapsed = time.perf_counter() - start
                    logger.info(
                        f"LLM 预检: {provider.name} {resp.status_code} {elapsed:.2f}s ({url})"
                    )
                except Exception as e:
                    elapsed = time.perf_counter() - start
                    logger.warning(
                        f"LLM 预检失败: {provider.name} {elapsed:.2f}s ({url}) -> {e}"
                    )

            await asyncio.gather(
                *(_check_provider(provider) for provider in providers),
                return_exceptions=True,
            )

        self.ui.update_phase_status("LLM 预检", "已完成")

    def _check_http_security(self, host: str, api_keys: list[str]) -> None:
        """检查 HTTP 服务器安全配置

        检测以下不安全的配置组合并发出警告：
        1. 监听地址为 0.0.0.0（对外开放）
        2. 未配置有效的 API 密钥或使用示例密钥

        Args:
            host: HTTP 服务器监听地址
            api_keys: API 密钥列表

        Warnings:
            当检测到不安全配置时，在终端输出警告信息
        """
        assert self.logger is not None

        # 不安全的示例密钥列表
        INSECURE_KEYS = {
            "secret-key-1",
            "test-key",
            "example-key",
            "demo-key",
            "default-key",
            "changeme",
            "password",
            "123456",
        }

        # 检查是否对外开放
        is_public = host == "0.0.0.0"
        
        # 检查密钥是否不安全（空或包含示例密钥）
        has_insecure_keys = (
            not api_keys or any(key.lower() in INSECURE_KEYS for key in api_keys)
        )

        if is_public and has_insecure_keys:
            # 使用 logger 发出警告
            self.logger.warning("")
            self.logger.warning("=" * 80)
            self.logger.warning("⚠️  HTTP 服务器安全警告 ⚠️")
            self.logger.warning("=" * 80)
            self.logger.warning("")
            self.logger.warning(f"检测到 HTTP 服务器配置为对外开放（{host}），但未设置安全的 API 密钥！")
            self.logger.warning("")
            self.logger.warning("这将导致以下安全风险：")
            self.logger.warning("  • 任何人都可以访问您的 Bot API 端点")
            self.logger.warning("  • 可能被恶意利用进行未授权操作")
            self.logger.warning("  • 可能导致数据泄露或系统被攻击")
            self.logger.warning("")
            self.logger.warning("建议的解决方案：")
            self.logger.warning("  1. 在 config/core.toml 中设置强密钥：")
            self.logger.warning('     api_keys = ["your-strong-random-key-here"]')
            self.logger.warning("  2. 或将监听地址改为本地：")
            self.logger.warning('     http_router_host = "127.0.0.1"')
            self.logger.warning("")
            self.logger.warning("⚠️  我们不会承担因不安全配置导致系统被黑入的任何风险和责任！⚠️")
            self.logger.warning("")
            self.logger.warning("=" * 80)
            self.logger.warning("")
            from .console_input import prompt_console_input

            prompt_console_input("输入回车来继续:")

            # 同时在 UI 中显示警告状态
            self.ui.update_phase_status("HTTP服务器", "⚠️ 不安全配置")
            
    async def _initialize_core(self) -> None:
        """在数据库就绪后准备媒体、传输和管理器，再加载插件。"""
        # 步骤 1：显式准备媒体服务，避免插件首次访问触发后台初始化。
        await self._initialize_media_manager()
        # 步骤 2：先发布消息通道，再安装管理器的事件订阅。
        self._initialize_transport()
        self._initialize_builtin_managers()
        # 步骤 3：准备外部工具连接及插件路由所需的 HTTP 服务。
        await self._initialize_mcp_manager()
        await self._initialize_http_server()

    async def _initialize_media_manager(self) -> None:
        """显式准备媒体管理器，周期清理在运行阶段启动。"""
        from src.core.managers import initialize_media_manager

        self.media_manager = initialize_media_manager()
        await self.media_manager.initialize()
        self.ui.update_phase_status("媒体管理器", "已初始化")

    def _initialize_transport(self) -> None:
        """创建消息接收器并发布共享 SinkManager，供适配器使用。"""
        from src.core.transport import MessageReceiver, SinkManager
        from src.core.transport.sink import set_sink_manager
        
        self.message_receiver = MessageReceiver()
        self.sink_manager = SinkManager(self.message_receiver)
        set_sink_manager(self.sink_manager)
        self.ui.update_phase_status("消息接收器", "已初始化")
        
    def _initialize_builtin_managers(self) -> None:
        """初始化核心管理器及事件订阅，必须先于插件加载。"""
        from src.core.managers import (
            initialize_adapter_manager,
            initialize_router_manager,
            initialize_event_manager,
            initialize_distribution,
        )

        initialize_adapter_manager()
        initialize_router_manager()
        initialize_event_manager()
        from src.core.transport.distribution import get_stream_loop_manager

        self._stream_loop_manager = get_stream_loop_manager()
        initialize_distribution()

        self.ui.update_phase_status("核心管理器", "已初始化")

    async def _initialize_mcp_manager(self) -> None:
        """建立 MCP 工具连接；连接资源由 shutdown() 释放。"""
        from src.core.managers import get_mcp_manager

        self.mcp_manager = get_mcp_manager()
        await self.mcp_manager.initialize()
        self.ui.update_phase_status("MCP", "已初始化")
        
    async def _initialize_http_server(self) -> None:
        """按配置启动 HTTP 服务，供插件加载完成时挂载路由。"""
        assert self.config is not None
        from src.core.transport.router.http_server import get_http_server
        
        if self.config.http_router.enable_http_router:
            host = self.config.http_router.http_router_host
            port = self.config.http_router.http_router_port
            api_keys = self.config.http_router.api_keys
            
            # 安全检查：检测对外开放且无有效密钥的情况
            self._check_http_security(host, api_keys)
            
            self.http_server = get_http_server(host=host, port=port)
            await self.http_server.start()

            self.ui.update_phase_status("HTTP服务器", "已启动")
        else:
            self.ui.update_phase_status("HTTP服务器", "已跳过")

    async def _initialize_plugins(self) -> None:
        """同步市场、发现插件、安装依赖并按依赖顺序加载插件。"""
        # 步骤 1：同步市场后发现插件并解析加载顺序。
        await self._sync_plugin_market()
        await self._discover_plugins()
        # 步骤 2：安装依赖并剔除缺少必需依赖的插件。
        await self._install_plugin_deps()
        # 步骤 3：加载插件并发布 ON_ALL_PLUGIN_LOADED。
        self.ui.begin_plugin_loading(len(self.load_order))
        await self._load_plugins()

    def _validate_startup_contract(self) -> None:
        """检查必需资源已准备完成，并记录预注册任务供诊断。

        Raises:
            BotRuntimeError: 必需资源缺失。
        """
        for name in (
            "config", "logger", "event_bus", "task_manager", "scheduler",
            "media_manager", "message_receiver", "sink_manager", "plugin_manager",
        ):
            if getattr(self, name) is None:
                raise BotRuntimeError(f"启动依赖未就绪: {name}")
        assert self.logger is not None
        assert self.scheduler is not None
        self.logger.debug(f"已注册定时任务: {self.scheduler.list_schedule_names()}")

    async def _discover_plugins(self) -> None:
        """发现插件并解析依赖"""
        self.ui.update_phase_status("发现插件", "扫描中...")

        from src.core.components.loader import PluginLoader

        self.plugin_loader = PluginLoader()
        self.load_order, self.manifests = await self.plugin_loader.plan_plugins(
            self.plugins_dir
        )

        # 显示插件加载计划
        self.ui.display_plugin_plan(self.load_order, self.manifests)
        self.ui.update_phase_status("发现插件", f"已发现 {len(self.load_order)} 个插件")

    async def _sync_plugin_market(self) -> None:
        """在插件发现前同步插件市场订阅。"""

        assert self.config is not None

        self.ui.update_phase_status("插件市场同步", "检查中...")

        try:
            from .plugin_market_sync import PluginMarketSyncService

            service = PluginMarketSyncService(
                config=self.config,
                plugins_dir=self.plugins_dir,
            )
            report = await service.sync()
            self.ui.update_phase_status("插件市场同步", report.summary())
            if self.logger and report.errors:
                for error in report.errors:
                    self.logger.warning(f"插件市场同步警告: {error}")
        except Exception as exc:
            self.ui.update_phase_status("插件市场同步", "失败，已跳过")
            if self.logger:
                self.logger.warning(f"插件市场同步失败，已跳过: {exc}")

    async def _install_plugin_deps(self) -> None:
        """Phase 3.5：批量安装所有插件声明的 Python 包依赖。

        在插件发现完成、插件加载开始之前执行：
        1. 读取全局开关 plugin_deps.enabled，若为 False 则跳过整个流程。
        2. 收集 load_order 中每个插件的 python_dependencies，构建 PluginDepSpec 列表。
        3. 调用 DependencyInstaller.install_for_plugins() 批量安装（去重，可选跳过已满足包）。
        4. 对安装失败且 dependencies_required=True 的插件，将其从 load_order / manifests 中移除。
        5. 对安装失败且 dependencies_required=False 的插件，仅记录 WARNING，保留加载队列。
        """
        assert self.config is not None

        cfg = self.config.plugin_deps

        if not cfg.enabled:
            self.ui.update_phase_status("依赖安装", "已跳过（已禁用）")
            return

        # 构建规格列表（忽略无依赖的插件）
        from src.core.components.utils import DependencyInstaller, PluginDepSpec

        specs = [
            PluginDepSpec(
                plugin_name=name,
                packages=list(self.manifests[name].python_dependencies),
                required=self.manifests[name].dependencies_required,
            )
            for name in self.load_order
            if self.manifests[name].python_dependencies
        ]

        if not specs:
            self.ui.update_phase_status("依赖安装", "无需安装")
            return

        total_pkgs = sum(len(s.packages) for s in specs)
        self.ui.update_phase_status("依赖安装", f"检查 {total_pkgs} 个依赖...")

        installer = DependencyInstaller()
        results = await installer.install_for_plugins(
            specs,
            command=cfg.install_command,
            skip_if_satisfied=cfg.skip_if_satisfied,
        )

        # 根据结果决定是否将插件从加载队列移除
        removed: list[str] = []
        for plugin_name, success in results.items():
            if not success:
                manifest = self.manifests[plugin_name]
                if manifest.dependencies_required:
                    removed.append(plugin_name)
                    if self.logger:
                        self.logger.warning(
                            f"插件 '{plugin_name}' 依赖安装失败且标记为必需，已从加载队列移除。"
                        )
                else:
                    if self.logger:
                        self.logger.warning(
                            f"插件 '{plugin_name}' 依赖安装失败但标记为非必需，仍尝试加载。"
                        )

        for name in removed:
            self.load_order.remove(name)
            self.load_results[name] = False

        status_parts: list[str] = []
        installed_count = sum(1 for ok in results.values() if ok)
        if installed_count:
            status_parts.append(f"{installed_count} 个插件依赖已就绪")
        if removed:
            status_parts.append(f"{len(removed)} 个插件因依赖失败被移除")
        self.ui.update_phase_status("依赖安装", "、".join(status_parts) or "完成")

    async def _load_plugins(self) -> dict[str, bool]:
        """加载插件"""
        self.ui.update_phase_status("加载插件", "启动中...")

        from src.core.managers import get_plugin_manager

        self.plugin_manager = get_plugin_manager()

        for plugin_name in self.load_order:
            manifest = self.manifests[plugin_name]
            try:
                success = await self.plugin_manager.load_plugin_from_manifest(
                    manifest._source_path, manifest
                )
                self.load_results[plugin_name] = success
                self.ui.update_plugin_progress(plugin_name, success)

            except Exception as e:
                self.load_results[plugin_name] = False
                self.ui.update_plugin_progress(plugin_name, False)
                if self.logger:
                    self.logger.error(f"插件 '{plugin_name}' 加载失败: {e}")
                # 继续加载其他插件（容错策略）

        # 更新统计
        self._stats["plugins_loaded"] = len(
            [r for r in self.load_results.values() if r]
        )
        self._stats["plugins_failed"] = len(
            [r for r in self.load_results.values() if not r]
        )

        # 发布插件加载完成事件
        assert self.event_bus is not None

        from src.core.components.types import EventType
        await self.event_bus.publish(EventType.ON_ALL_PLUGIN_LOADED, {})
        
        return self.load_results

    async def run(self) -> None:
        """校验依赖、启动运行时服务，再进入命令循环。

        服务启动失败时不发布 ON_START；start() 的 finally 负责释放资源。
        信号处理器和命令解析器保留到 shutdown() 末尾，以支持二次中断。

        Raises:
            BotRuntimeError: Bot 未初始化、重复运行或已进入关闭流程。
        """
        if not self._initialized:
            raise BotRuntimeError("Bot 未初始化。请先调用 initialize()。")
        if self._running or self._shutdown_requested:
            raise BotRuntimeError("Bot 已在运行或已进入关闭流程。")
        self._validate_startup_contract()

        # ON_START 处理器可以请求停止，因此启动后不能重新覆盖该标志。
        self._running = True
        try:
            # 步骤 1：激活调度器及依赖它的后台服务。
            await self._start_runtime_services()
            # 步骤 2：服务就绪后通知插件；处理器可请求停止。
            await self._publish_start_event()
            # 步骤 3：准备信号和终端输入，保留到 shutdown() 末尾。
            self._install_signal_handler()
            self._create_command_parser()
            assert self.logger is not None
            self.logger.info("Neo-MoFox Bot 启动成功")
            self.logger.info("输入 /help 查看可用命令")
            # 步骤 4：进入命令循环，退出后复位运行标记。
            await self._run_command_loop()
        finally:
            self._running = False
            await self._record_runtime_snapshot(event_name="run_stopped")

    async def _start_runtime_services(self) -> None:
        """先启动调度器，再启动媒体、日志清理及云端遥测服务。"""
        # 步骤 1：启动检查循环，激活插件在初始化阶段注册的任务。
        await self._start_scheduler()
        # 步骤 2：注册媒体和日志清理，延迟从此时开始计算。
        await self._start_media_manager()
        await self._start_log_cleanup()
        # 步骤 3：启动可选的云端遥测发送循环。
        await self._start_cloud_telemetry()

    async def _start_scheduler(self) -> None:
        """激活预注册任务的检查循环，成功后更新运行统计。"""
        assert self.scheduler is not None
        await self.scheduler.start()
        self._stats["scheduler_running"] = True

    async def _start_media_manager(self) -> None:
        """在调度器运行后注册媒体清理任务，错误交由启动入口处理。"""
        assert self.scheduler is not None
        assert self.media_manager is not None
        if not self.scheduler.is_started:
            raise BotRuntimeError("启动媒体清理前必须启动调度器。")
        await self.media_manager.start()

    async def _start_log_cleanup(self) -> None:
        """注册日志清理任务；失败时记录警告并继续运行。"""
        if self._start_logger_cleanup is not None:
            try:
                await self._start_logger_cleanup()
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"启动日志自动清理失败: {e}")

    async def _start_cloud_telemetry(self) -> None:
        """启动已启用的云端遥测发送循环，失败不阻止 Bot 运行。"""
        try:
            from src.app.cloud_telemetry import get_cloud_telemetry_client

            cloud_client = get_cloud_telemetry_client()
            if cloud_client is not None and cloud_client.enabled:
                await cloud_client.start()
        except Exception as e:
            if self.logger:
                self.logger.warning(f"启动云端遥测发送循环失败: {e}")
        
    async def _publish_start_event(self) -> None:
        """在服务启动完成后发布 ON_START，处理器异常记录为警告。"""
        try:
            from src.core.components.types import EventType
            assert self.event_bus is not None
            await self.event_bus.publish(EventType.ON_START, {})
            if self.logger:
                self.logger.info("已触发 ON_START 事件")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"触发 ON_START 事件失败: {e}")

    def _install_signal_handler(self) -> None:
        """安装信号处理器，保留至关闭结束以支持第二次 Ctrl+C。"""
        self._signal_handler = SignalHandler(self)
        self._signal_handler.register_signals()

    def _create_command_parser(self) -> None:
        """创建交互命令解析器，其输入线程由 shutdown() 末尾关闭。"""
        from .command_parser import CommandParser

        self._command_parser = CommandParser(self)

    async def _run_command_loop(self) -> None:
        """处理终端命令，直到退出命令、关闭信号或任务取消。

        单条命令异常被记录后继续读取；解析器的释放属于关闭阶段。
        """
        assert self._command_parser is not None
        assert self.logger is not None
        await self._record_telemetry_event(
            domain="runtime",
            event_name="run_started",
            summary="bot run loop started",
        )
        while self._running:
            try:
                should_continue = await self._command_parser.read_and_execute()
                if not should_continue:
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._record_telemetry_event(
                    domain="error",
                    event_name="run_loop_error",
                    severity="error",
                    summary="run loop error",
                    attributes={"error_type": type(e).__name__},
                    detail={"message": str(e)},
                )
                self.logger.error(f"主循环错误: {e}", exc_info=e)

    def _install_telemetry_hooks(self) -> None:
        """安装遥测日志订阅。"""
        if self.event_bus is None or self._telemetry_log_unsubscribe is not None:
            return

        from src.kernel.event import EventDecision
        from src.kernel.logger import LOG_OUTPUT_EVENT
        from src.kernel.telemetry import (
            TelemetryEventRecord,
            anonymize_identifier,
            get_telemetry_collector,
        )

        async def _on_log_output(
            event_name: str,
            params: dict[str, Any],
        ) -> tuple[EventDecision, dict[str, Any]]:
            collector = get_telemetry_collector()
            level = str(params.get("level", "")).upper()
            if level not in {"WARNING", "ERROR", "CRITICAL"}:
                return (EventDecision.PASS, params)
            if not collector.is_domain_enabled("error"):
                return (EventDecision.PASS, params)

            metadata = params.get("metadata")
            safe_metadata = metadata if isinstance(metadata, dict) else {}
            entity_source = (
                safe_metadata.get("stream_id")
                or safe_metadata.get("session_id")
                or safe_metadata.get("user_id")
            )
            message = str(params.get("message", ""))
            display = str(params.get("display", params.get("logger_name", "logger")))

            await collector.record(
                TelemetryEventRecord(
                    domain="error",
                    event_name="log_error" if level != "WARNING" else "log_warning",
                    severity=level.lower(),
                    summary=f"{level} from {display}",
                    entity_id=anonymize_identifier(
                        str(entity_source) if entity_source is not None else None,
                        salt=collector.hash_salt,
                    ),
                    attributes={
                        "logger_name": str(params.get("logger_name", "")),
                        "display": display,
                        "level": level,
                        "message_hash": anonymize_identifier(message, salt=collector.hash_salt),
                        "message_length": len(message),
                        "metadata_keys": sorted(str(key) for key in safe_metadata.keys())[:20],
                    },
                    detail={
                        "message": message,
                        "metadata": safe_metadata,
                    },
                )
            )
            return (EventDecision.PASS, params)

        self._telemetry_log_unsubscribe = self.event_bus.subscribe(
            LOG_OUTPUT_EVENT,
            _on_log_output,
            priority=-100,
        )

    async def _record_telemetry_event(
        self,
        *,
        domain: str,
        event_name: str,
        summary: str,
        severity: str = "info",
        attributes: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """记录一条通用遥测事件。"""
        from src.kernel.telemetry import TelemetryEventRecord, get_telemetry_collector

        collector = get_telemetry_collector()
        if not collector.is_domain_enabled(domain):
            return

        await collector.record(
            TelemetryEventRecord(
                domain=domain,
                event_name=event_name,
                summary=summary,
                severity=severity,
                attributes=attributes or {},
                detail=detail,
            )
        )

    async def _record_runtime_snapshot(self, *, event_name: str) -> None:
        """记录运行时健康快照。"""
        from src.core.transport.distribution.stream_loop_manager import (
            get_stream_loop_manager,
        )
        from src.kernel.concurrency import get_watchdog

        if self.task_manager is None:
            return

        await self._record_telemetry_event(
            domain="runtime",
            event_name=event_name,
            summary="runtime snapshot",
            attributes={
                "bot_running": self._running,
                "initialized": self._initialized,
                "shutdown_requested": self._shutdown_requested,
                "stats": dict(self._stats),
                "task_manager": self.task_manager.get_stats(),
                "watchdog": get_watchdog().get_stats(),
                "stream_loop_manager": get_stream_loop_manager().get_stats(),
            },
        )

    async def reload_plugin(
        self, plugin_name: str | None = None
    ) -> dict[str, bool]:
        """重新加载插件

        Args:
            plugin_name: 插件名，None 表示重新加载所有插件

        Returns:
            加载结果字典 {plugin_name: success}
        """
        results = {}

        assert self.plugin_manager is not None
        try:
            if plugin_name:
                # 单插件重载
                if plugin_name not in self.manifests:
                    self.ui.display_error(f"未知插件: {plugin_name}")
                    return {plugin_name: False}

                # 卸载
                await self.plugin_manager.unload_plugin(plugin_name)

                # 重新加载
                manifest = self.manifests[plugin_name]
                success = await self.plugin_manager.load_plugin_from_manifest(
                    manifest._source_path, manifest
                )
                results[plugin_name] = success

            else:
                # 全部重载
                await self._unload_all_plugins()
                results = await self._load_plugins()

        except Exception as e:
            if self.logger:
                self.logger.error(f"插件重载失败: {e}", exc_info=e)
            if plugin_name:
                results[plugin_name] = False

        return results

    async def _unload_all_plugins(self) -> None:
        """卸载所有插件"""
        if not self.plugin_manager:
            return

        # 按相反顺序卸载
        for plugin_name in reversed(self.load_order):
            try:
                await self.plugin_manager.unload_plugin(plugin_name)
                if self.logger:
                    self.logger.info(f"插件已卸载: {plugin_name}")
            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        f"插件 '{plugin_name}' 卸载失败: {e}"
                    )

        # 清空统计
        self.load_results.clear()
        self._stats["plugins_loaded"] = 0
        self._stats["plugins_failed"] = 0

    async def shutdown(self, timeout: float = 30.0) -> None:
        """按依赖顺序释放已取得的资源，汇总错误后再报告失败。

        Args:
            timeout: 每个异步清理步骤（包括单个插件）的等待上限，秒。
                依赖协程配合取消；同步阻塞操作不受 asyncio 超时抢占。

        Raises:
            ValueError: timeout 不大于零。
            BotShutdownError: 一个或多个清理步骤失败或超时。
        """
        if self._shutdown_requested:
            return
        if timeout <= 0:
            raise ValueError("关闭步骤超时必须大于零")
        self._shutdown_requested = True
        self._running = False
        errors: list[str] = []

        async def step(name: str, callback: Callable[[], Awaitable[Any]]) -> None:
            """独立限制清理步骤的等待时间；失败不会跳过后续步骤。"""
            try:
                async with asyncio.timeout(timeout):
                    await callback()
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

        try:
            # 步骤 1：停止主循环，记录关闭开始；遥测失败也不能阻断清理。
            if "telemetry" in self._owned_resources:
                await step("关闭开始遥测", partial(
                    self._record_telemetry_event, domain="runtime",
                    event_name="shutdown_started", summary="bot shutdown started",
                ))

            # 步骤 2：在插件还存在时发布停止事件，停止 HTTP 入口与聊天流。
            await step("ON_STOP", self._publish_stop_event)
            await step("HTTP", self._stop_http_server)
            await step("聊天流", self._stop_stream_loops)

            # 步骤 3：逐个逆序卸载插件，每个插件拥有独立的超时与错误记录。
            if self.plugin_manager is not None:
                for name in reversed(self.load_order):
                    await step(f"插件 {name}", partial(self._shutdown_plugin, name))
            self.load_results.clear()
            self._stats.update(plugins_loaded=0, plugins_failed=0)

            # 步骤 4：移除周期服务的任务，再停止统一调度器。
            await step("日志清理", self._stop_log_cleanup)
            await step("媒体管理器", self._stop_media_manager)
            await step("调度器", self._stop_scheduler)

            # 步骤 5：停止外部连接、遥测发送和工作任务，避免访问已关闭的数据库。
            await step("MCP", self._stop_mcp_manager)
            await step("云端遥测", self._close_cloud_telemetry)
            await step("看门狗", self._stop_watchdog)
            await step("任务管理器", self._stop_task_manager)

            # 步骤 6：后台工作停止后，逐个关闭业务存储。
            await step("主数据库", self._close_database)
            await step("向量数据库", self._close_vector_database)
            await step("LLM统计", self._close_llm_statistics)

            # 步骤 7：遥测尚可写时记录结果，之后解绑订阅并关闭遥测存储。
            if "telemetry" in self._owned_resources:
                await step("关闭结果遥测", partial(
                    self._record_telemetry_event, domain="runtime",
                    event_name="shutdown_failed" if errors else "shutdown_complete",
                    summary="bot shutdown finished", detail={"errors": list(errors)},
                ))
            await step("遥测订阅", self._remove_telemetry_hooks)
            await step("本地遥测", self._close_telemetry)
        finally:
            # 步骤 8：即使关闭任务被取消，也恢复线程池、终端和信号；各项独立兜底。
            for name, cleanup in (
                ("网络运行环境", self._restore_network_runtime),
                ("日志系统", self._close_logger),
                ("命令解析器", self._close_command_parser),
                ("信号处理器", self._restore_signal_handler),
            ):
                try:
                    cleanup()
                except Exception as exc:
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")
            self._initialized = False

        if errors:
            message = "关闭部分失败：" + "；".join(errors)
            self.ui.display_error(message)
            raise BotShutdownError(message)
        self.ui.display_success("关闭完成")

    async def _shutdown_plugin(self, name: str) -> None:
        """卸载单个插件，并将已加载插件的失败返回值转换为关闭错误。"""
        assert self.plugin_manager is not None
        result = await self.plugin_manager.unload_plugin(name)
        if result is False and self.load_results.get(name):
            raise BotShutdownError(f"插件 {name} 卸载失败")

    async def _publish_stop_event(self) -> None:
        """仅在事件总线已创建时通知插件停止，供部分初始化回滚使用。"""
        if self.event_bus is not None:
            from src.core.components.types import EventType

            await self.event_bus.publish(EventType.ON_STOP, {})

    async def _stop_http_server(self) -> None:
        """停止已创建的 HTTP 服务，避免后续释放期间继续接收请求。"""
        if self.http_server is not None:
            await self.http_server.stop()

    async def _stop_stream_loops(self) -> None:
        """在插件和数据库释放前停止聊天流；关闭时不懒创建管理器。"""
        if self._stream_loop_manager is not None:
            await self._stream_loop_manager.stop()

    async def _stop_log_cleanup(self) -> None:
        """在调度器仍可操作时移除日志清理任务。"""
        if self._stop_logger_cleanup is not None:
            await self._stop_logger_cleanup()

    async def _stop_media_manager(self) -> None:
        """显式释放媒体清理任务，允许媒体服务尚未启动。"""
        if self.media_manager is not None:
            await self.media_manager.shutdown()

    async def _stop_scheduler(self) -> None:
        """停止调度器并清空预注册任务，成功后更新统计。"""
        if self.scheduler is not None:
            await self.scheduler.stop()
            self._stats["scheduler_running"] = False

    async def _stop_mcp_manager(self) -> None:
        """关闭已创建的 MCP 连接和工具资源。"""
        if self.mcp_manager is not None:
            await self.mcp_manager.cleanup()

    async def _stop_watchdog(self) -> None:
        """停止看门狗，避免将正常关闭中的任务报告为异常。"""
        if self.watchdog is not None:
            self.watchdog.stop()

    async def _stop_task_manager(self) -> None:
        """取消并等待受管理任务，包括守护任务；始终回收进程池。

        只清理 TaskManager 持有的任务，不取消宿主事件循环中的无关任务。
        当前关闭任务可能也受管理，必须排除，避免自取消或自等待。
        """
        if self.task_manager is None:
            return
        current = asyncio.current_task()
        pending = []
        try:
            for info in self.task_manager.get_active_tasks():
                if info.task is not None and info.task is not current:
                    self.task_manager.cancel_task(info.task_id)
                    pending.append(info.task)
            if pending:
                await asyncio.wait(pending)
        finally:
            try:
                self.task_manager.cleanup_tasks()
            finally:
                self.task_manager.shutdown_process_pool(wait=False)

    async def _close_database(self) -> None:
        """释放本 Bot 尝试初始化的主数据库，包括初始化部分失败的情况。"""
        if "database" in self._owned_resources:
            from src.kernel.db import close_engine

            await close_engine()
            self._stats["db_connected"] = False
            self._owned_resources.discard("database")

    async def _close_vector_database(self) -> None:
        """关闭本 Bot 初始化过的向量数据库服务。"""
        if "vector_db" in self._owned_resources:
            from src.kernel.vector_db import close_all_vector_db_services

            await close_all_vector_db_services()
            self._owned_resources.discard("vector_db")

    async def _close_cloud_telemetry(self) -> None:
        """在统计存储关闭前停止云端发送循环并释放运行时。"""
        if "cloud_telemetry" in self._owned_resources:
            from src.app.cloud_telemetry import close_cloud_telemetry_runtime

            await close_cloud_telemetry_runtime()
            self._owned_resources.discard("cloud_telemetry")

    async def _close_llm_statistics(self) -> None:
        """关闭已尝试初始化的 LLM 统计数据库。"""
        if "llm_stats" in self._owned_resources:
            from src.kernel.llm.stats import close_llm_stats_db

            await close_llm_stats_db()
            self._owned_resources.discard("llm_stats")

    async def _remove_telemetry_hooks(self) -> None:
        """在遥测存储关闭前解除日志订阅。"""
        if callable(self._telemetry_log_unsubscribe):
            self._telemetry_log_unsubscribe()
            self._telemetry_log_unsubscribe = None

    async def _close_telemetry(self) -> None:
        """关闭已尝试初始化的本地遥测数据库。"""
        if "telemetry" in self._owned_resources:
            from src.kernel.telemetry import close_telemetry_db

            await close_telemetry_db()
            self._owned_resources.discard("telemetry")

    def _restore_network_runtime(self) -> None:
        """恢复 DNS 方法和原默认线程池，仅关闭本 Bot 创建的线程池。"""
        loop = asyncio.get_running_loop()
        if self._original_getaddrinfo is not None:
            loop.getaddrinfo = self._original_getaddrinfo  # type: ignore[method-assign]
            self._original_getaddrinfo = None
        if self._original_getnameinfo is not None:
            loop.getnameinfo = self._original_getnameinfo  # type: ignore[method-assign]
            self._original_getnameinfo = None
        if (
            self._default_executor is not None
            and getattr(loop, "_default_executor", None) is self._default_executor
        ):
            if self._previous_default_executor is not None:
                loop.set_default_executor(self._previous_default_executor)
            else:
                # 公共 setter 不接受 None；恢复未创建默认池的状态需动态赋值。
                setattr(loop, "_default_executor", None)
        try:
            if self._dns_executor is not None:
                self._dns_executor.shutdown(wait=False, cancel_futures=True)
                self._dns_executor = None
        finally:
            if self._default_executor is not None:
                self._default_executor.shutdown(wait=False, cancel_futures=True)
                self._default_executor = None

    def _close_logger(self) -> None:
        """关闭本 Bot 初始化过的日志系统，错误报告随后走控制台 UI。"""
        if "logger" in self._owned_resources:
            from src.kernel.logger import shutdown_logger_system

            shutdown_logger_system()
            self._owned_resources.discard("logger")

    def _close_command_parser(self) -> None:
        """在资源释放结束后停止输入线程，让关闭期间的二次中断有效。"""
        if self._command_parser is not None:
            self._command_parser.close()
            self._command_parser = None

    def _restore_signal_handler(self) -> None:
        """最后恢复进程原信号处理器，即使命令解析器关闭失败也执行。"""
        if self._signal_handler is not None:
            self._signal_handler.restore_handlers()
            self._signal_handler = None

    async def start(self) -> None:
        """完整的启动流程（初始化 + 运行 + 关闭）

        这是推荐的启动方式。
        """
        try:
            # 步骤 1：准备依赖与插件，允许调度任务预注册。
            await self.initialize()
            # 步骤 2：启动服务并进入命令循环。
            await self.run()

        except KeyboardInterrupt:
            # 用户中断
            await self._record_telemetry_event(
                domain="runtime",
                event_name="keyboard_interrupt",
                summary="bot interrupted by user",
                severity="warning",
            )
            if self.logger:
                self.logger.info("用户中断")
            else:
                print("\n[用户中断]")
        except Exception as e:
            await self._record_telemetry_event(
                domain="error",
                event_name="fatal_error",
                severity="error",
                summary="bot fatal error",
                attributes={"error_type": type(e).__name__},
                detail={"message": str(e)},
            )
            if self.logger:
                self.logger.error(f"Bot 错误: {e}", exc_info=e)
            else:
                print(f"\n[致命错误: {e}]")
            raise
        finally:
            # 步骤 3：任何退出路径都尝试清理；清理失败不能覆盖原始启动异常。
            original_error = sys.exc_info()[1]
            try:
                await self.shutdown()
            except Exception as cleanup_error:
                if original_error is None:
                    raise
                original_error.add_note(f"关闭期间另有异常: {cleanup_error}")


__all__ = ["Bot"]
