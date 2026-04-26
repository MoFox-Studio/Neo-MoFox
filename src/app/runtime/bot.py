"""Bot 主类

Neo-MoFox 框架的核心协调器，负责系统初始化、插件加载和生命周期管理。
"""

from __future__ import annotations

import asyncio
import socket
import tomllib
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from src.core.config import CORE_VERSION

from .console_ui import ConsoleUIManager, UILevel
from .exceptions import BotInitializationError, BotRuntimeError, BotShutdownError
from .signal_handler import SignalHandler

if TYPE_CHECKING:
    from src.core.config import CoreConfig
    from src.core.components import PluginLoader, PluginManifest
    from src.core.managers import PluginManager
    from src.core.transport import MessageReceiver, HTTPServer, SinkManager
    from src.kernel.concurrency import TaskManager, WatchDog
    from src.kernel.event import EventBus
    from src.kernel.logger import Logger
    from src.kernel.scheduler import UnifiedScheduler
    from src.kernel.storage import JSONStore
    from src.kernel.vector_db import VectorDBBase

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
        ui_level: UI 详细程度
    """

    bot_name: str = "Neo-MoFox"
    bot_version: str = CORE_VERSION

    def __init__(
        self,
        config_path: str = "config/core.toml",
        plugins_dir: str = "plugins",
        log_dir: str = "logs",
        ui_level: UILevel = UILevel.STANDARD,
    ) -> None:
        """初始化 Bot

        Args:
            config_path: 配置文件路径
            plugins_dir: 插件目录
            log_dir: 日志目录
            ui_level: UI 详细程度
        """
        self.config_path = config_path
        self.plugins_dir = plugins_dir
        self.log_dir = log_dir

        # UI 管理器
        self.ui = ConsoleUIManager(level=ui_level)

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
        self.http_server: HTTPServer | None = None
        self.load_order: list[str] = []
        self.manifests: dict[str, PluginManifest] = {}
        self.load_results: dict[str, bool] = {}

        # 统计数据
        self._stats: dict[str, int | bool | dict] = {
            "plugins_loaded": 0,
            "plugins_failed": 0,
            "components_by_type": {},
        }

    async def initialize(self) -> None:
        """完整初始化流程

        按顺序初始化：
        1. Kernel 层（9 步）
        2. Core 层组件初始化
        3. 插件发现
        4. 插件加载

        Raises:
            BotInitializationError: 初始化失败
        """
        try:
            # 显示启动横幅（在进度条之前）
            self.ui.show_banner(self.bot_version, self.bot_name)

            # 启动前优化 async 连接池/DNS 行为
            await self._optimize_async_network_runtime()

            # 单一总体进度条贯穿全部初始化阶段
            with self.ui.startup_progress(total_steps=15):
                # Phase 1: Kernel 初始化
                await self._initialize_kernel()

                # Phase 2: Core 组件初始化
                await self._initialize_core()

                # Phase 3: 插件发现
                await self._discover_plugins()

                # Phase 3.5: 安装插件 Python 依赖
                await self._install_plugin_deps()

                # Phase 4: 插件加载（进度条追加插件子任务）
                self.ui.begin_plugin_loading(len(self.load_order))
                await self._load_plugins()

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

        except Exception as e:
            self.ui.display_error(f"Initialization failed: {e}", e)
            raise BotInitializationError(str(e), "unknown") from e

    async def _optimize_async_network_runtime(self) -> None:
        """优化异步网络运行时：线程池与 DNS 预解析。"""
        loop = asyncio.get_running_loop()

        # 默认线程池：承载 to_thread / run_in_executor(None, ...)
        loop.set_default_executor(ThreadPoolExecutor(max_workers=192))

        # DNS 专用线程池：避免 getaddrinfo 被通用任务挤占
        dns_executor = ThreadPoolExecutor(max_workers=16)

        async def _patched_getaddrinfo(host, port, *args, **kwargs):
            func = partial(socket.getaddrinfo, host, port, *args, **kwargs)
            return await loop.run_in_executor(dns_executor, func)

        async def _patched_getnameinfo(sockaddr, flags=0):
            func = partial(socket.getnameinfo, sockaddr, flags)
            return await loop.run_in_executor(dns_executor, func)

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
        """初始化 Kernel 层（9 步）

        1. Config
        2. Logger
        3. Event Bus
        4. Task Manager
        5. Scheduler
        6. WatchDog
        7. Database
        8. VectorDB
        9. Storage
        """
        self.ui.update_phase_status("初始化内核", "启动中...")

        # Step 1: Config
        from src.core.config import init_core_config, init_model_config

        self.config = init_core_config(self.config_path)
        init_model_config("config/model.toml")
        self.ui.update_phase_status("配置", "已加载")

        # Step 2: Logger
        from src.kernel.logger import get_logger, initialize_logger_system, COLOR

        initialize_logger_system(log_dir=self.log_dir, log_level=self.config.bot.log_level)
        self.logger = get_logger(name="console", display="控制台", color=COLOR.BLUE)
        self.ui.update_phase_status("日志", "已初始化")

        await self._preflight_llm_providers()

        # Step 3: Event Bus
        from src.kernel.event import get_event_bus

        self.event_bus = get_event_bus()
        self.ui.update_phase_status("事件总线", "已初始化")

        # Step 4: Task Manager
        from src.kernel.concurrency import get_task_manager, get_watchdog

        self.task_manager = get_task_manager(
            process_workers=self.config.advanced.process_workers
        )
        
        # 仅在启用时启动 WatchDog
        if self.config.bot.enable_watchdog:
            get_watchdog().start()
        else:
            self.logger.warning("WatchDog 已禁用 (调试模式)")
            
        self.ui.update_phase_status("任务管理器", "已初始化")

        # Step 5: Scheduler
        from src.kernel.scheduler import get_unified_scheduler

        self.scheduler = get_unified_scheduler()
        self.ui.update_phase_status("调度器", "已初始化")

        # Step 6: WatchDog
        from src.kernel.concurrency import WatchDog

        self.watchdog = WatchDog()
        self.ui.update_phase_status("看门狗", "已初始化")

        # Step 7: Database
        from src.kernel.db import init_database_from_config

        db_cfg = self.config.database
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

        # Step 8: VectorDB
        from src.kernel.vector_db import get_vector_db_service

        # 确保 data 目录存在
        Path("data/chroma_db").mkdir(parents=True, exist_ok=True)
        self.vector_db = get_vector_db_service("data/chroma_db")
        self.ui.update_phase_status("向量数据库", "已初始化")

        # Step 9: Storage
        from src.kernel.storage import JSONStore

        # 确保 data 目录存在
        Path("data/json_storage").mkdir(parents=True, exist_ok=True)
        self.storage = JSONStore("data/json_storage")
        self.ui.update_phase_status("存储", "已初始化")

    async def _preflight_llm_providers(self) -> None:
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
                    self.logger.info(
                        f"LLM 预检: {provider.name} {resp.status_code} {elapsed:.2f}s ({url})"
                    )
                except Exception as e:
                    elapsed = time.perf_counter() - start
                    self.logger.warning(
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
            input("输入回车来继续:")

            # 同时在 UI 中显示警告状态
            self.ui.update_phase_status("HTTP服务器", "⚠️ 不安全配置")
            
    async def _initialize_core(self) -> None:
        """初始化 Core 层组件

        包括插件管理器、Action 管理器、Chatter 管理器、Command 管理器等。
        """
        assert self.config is not None

        # Step 1: 初始化 MessageReceiver 和 SinkManager
        from src.core.transport import MessageReceiver, SinkManager
        from src.core.transport.sink import set_sink_manager
        
        self.message_receiver = MessageReceiver()
        self.sink_manager = SinkManager(self.message_receiver)
        set_sink_manager(self.sink_manager)
        self.ui.update_phase_status("消息接收器", "已初始化")
        
        # Step 2: 导入其他manager以初始化
        from src.core.managers import (
            initialize_adapter_manager,
            initialize_router_manager,
            initialize_event_manager,
            initialize_distribution,
        )

        initialize_adapter_manager()
        initialize_router_manager()
        initialize_event_manager()
        initialize_distribution()

        self.ui.update_phase_status("核心管理器", "已初始化")
        
        # Step 3: 启动 HTTP 服务器
        from src.core.transport.router.http_server import get_http_server
        
        if self.config.http_router.enable_http_router:
            host = self.config.http_router.http_router_host
            port = self.config.http_router.http_router_port
            api_keys = self.config.http_router.api_keys
            
            # 安全检查：检测对外开放且无有效密钥的情况
            self._check_http_security(host, api_keys)
            
            self.http_server = get_http_server(host=host, port=port)
            await self.http_server.start()

            # 挂载 LLM 请求体检视器（调试用 WebUI）
            try:
                from src.kernel.llm.request_inspector import get_inspector
                get_inspector().mount(self.http_server.app)
            except Exception:
                pass

            self.ui.update_phase_status("HTTP服务器", "已启动")

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
        """主运行循环

        Raises:
            BotRuntimeError: Bot 未初始化
        """
        if not self._initialized:
            raise BotRuntimeError("Bot 未初始化。请先调用 initialize()。")

        # 断言核心组件已初始化（由于_initialized=True，这些不应该为None）
        assert self.logger is not None
        assert self.scheduler is not None
        assert self.task_manager is not None

        self._running = True

        # 启动调度器
        await self.scheduler.start()
        self._stats["scheduler_running"] = True
        
        # 触发 ON_START 事件（所有初始化完成，系统即将进入运行状态）
        try:
            from src.core.components.types import EventType
            assert self.event_bus is not None
            await self.event_bus.publish(EventType.ON_START, {})
            if self.logger:
                self.logger.info("已触发 ON_START 事件")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"触发 ON_START 事件失败: {e}")
        
        # 启动实时仪表盘（如果 UI 级别为 VERBOSE）
        if self.ui.level == UILevel.VERBOSE:
            self.ui.start_live_dashboard()

        # 启动信号处理器
        signal_handler = SignalHandler(self)
        signal_handler.register_signals()

        # 创建交互式命令解析器
        from .command_parser import CommandParser

        command_parser = CommandParser(self)

        self.logger.info("Neo-MoFox Bot 启动成功")
        self.logger.info("输入 /help 查看可用命令")

        # 主循环
        try:
            while self._running:
                try:
                    # 读取并执行命令（内部使用短超时轮询）
                    should_continue = await command_parser.read_and_execute()

                    if not should_continue:
                        break

                    # 更新仪表盘统计
                    if self.ui.level == UILevel.VERBOSE:
                        await self._update_runtime_stats()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"主循环错误: {e}", exc_info=e)

        finally:
            command_parser.close()

            # 停止实时仪表盘
            if self.ui.level == UILevel.VERBOSE:
                self.ui.stop_live_dashboard()

            # 恢复信号处理器
            signal_handler.restore_handlers()

    async def _update_runtime_stats(self) -> None:
        """更新运行时统计数据（用于仪表盘）"""
        assert self.task_manager is not None

        stats = {
            "plugins_loaded": self._stats["plugins_loaded"],
            "plugins_failed": self._stats["plugins_failed"],
            "components_by_type": self._stats["components_by_type"],
            "tasks_active": len(self.task_manager.get_all_tasks()),
            "db_connected": self._stats["db_connected"],
            "scheduler_running": self._stats["scheduler_running"],
        }

        self.ui.update_dashboard_stats(stats)

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
        """优雅关闭 Bot

        Args:
            timeout: 关闭超时时间（秒）

        Raises:
            BotShutdownError: 关闭失败
        """
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        self._running = False

        if self.logger:
            self.logger.info("正在关闭 Neo-MoFox Bot...")
        else:
            print("正在关闭 Neo-MoFox Bot...")

        try:
            # 1. 停止接受新工作
            if self.logger:
                self.logger.info("停止接受新任务...")

            # 2. 触发 ON_STOP 事件（让事件处理器在插件卸载前执行清理）
            try:
                from src.core.components.types import EventType
                assert self.event_bus is not None
                await self.event_bus.publish(EventType.ON_STOP, {})
                if self.logger:
                    self.logger.info("已触发 ON_STOP 事件")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"触发 ON_STOP 事件失败: {e}")

            # 3. 卸载插件
            await self._unload_all_plugins()

            # 4. 停止调度器
            if self.scheduler:
                await self.scheduler.stop()
                self._stats["scheduler_running"] = False

            # 5. 停止 HTTP 服务器
            if self.http_server and self.http_server.is_running():
                if self.logger:
                    self.logger.info("停止 HTTP 服务器...")
                await self.http_server.stop()

            # 6. 停止 WatchDog
            if self.watchdog:
                self.watchdog.stop()

            # 7. 停止任务管理器（取消所有活动任务）
            if self.task_manager:
                active_tasks = self.task_manager.get_active_tasks()
                for task_info in active_tasks:
                    self.task_manager.cancel_task(task_info.task_id)

                # 等待所有非守护任务完成
                await self.task_manager.wait_all_tasks()

                # 清理已完成的任务
                self.task_manager.cleanup_tasks()
                self.task_manager.shutdown_process_pool(wait=False)

            # 8. 关闭数据库
            from src.kernel.db import close_engine

            await close_engine()
            self._stats["db_connected"] = False

            # 9. 关闭向量数据库
            from src.kernel.vector_db import close_all_vector_db_services

            await close_all_vector_db_services()

            # 10. 关闭日志系统（停止事件广播）
            from src.kernel.logger import shutdown_logger_system

            shutdown_logger_system()

            self.ui.display_success("关闭完成")

        except Exception as e:
            if self.logger:
                self.logger.error(f"关闭过程中出错: {e}", exc_info=e)
            else:
                print(f"关闭过程中出错: {e}")
            raise BotShutdownError(f"关闭失败: {e}") from e

    async def start(self) -> None:
        """完整的启动流程（初始化 + 运行 + 关闭）

        这是推荐的启动方式。
        """
        try:
            await self.initialize()
            await self.run()

        except KeyboardInterrupt:
            # 用户中断
            if self.logger:
                self.logger.info("用户中断")
            else:
                print("\n[用户中断]")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Bot 错误: {e}", exc_info=e)
            else:
                print(f"\n[致命错误: {e}]")
            raise
        finally:
            await self.shutdown()


__all__ = ["Bot"]
