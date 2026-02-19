"""控制台 UI 管理器

使用 Rich 库提供现代化的控制台界面，包括：
- ASCII 艺术字横幅
- 进度条跟踪
- 可配置的 UI 级别（简洁/标准/详细）
- 实时状态面板
- 差异化视觉效果

UI 级别特性：
- MINIMAL: 纯文本输出，无装饰，适合日志采集和生产环境
- STANDARD: 适度装饰，进度条，表格，面板，适合日常使用
- VERBOSE: 完整 Rich 特性，实时仪表盘，Layout 分屏，动态刷新
"""

from __future__ import annotations

import datetime
from contextlib import contextmanager
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterator

from rich.box import ROUNDED, SIMPLE, HEAVY
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.spinner import Spinner
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

if TYPE_CHECKING:
    from src.core.components import PluginManifest


class UILevel(Enum):
    """UI 级别枚举

    定义三种 UI 详细程度，每个级别提供不同的视觉效果：

    - MINIMAL: 最小化输出
        - 纯文本，无颜色装饰
        - 无进度条，仅文字状态
        - 适合日志采集、CI/CD、生产环境

    - STANDARD: 标准显示（默认）
        - 彩色文本和图标
        - 简洁进度条
        - 简单表格和面板
        - 适合日常开发使用

    - VERBOSE: 详细模式
        - 完整 Rich 装饰效果
        - 实时 Live 仪表盘
        - Layout 分屏布局
        - 详细表格、树形结构
        - 动态 Spinner 和进度指示
        - 适合调试和演示
    """

    MINIMAL = "minimal"
    STANDARD = "standard"
    VERBOSE = "verbose"

    def __ge__(self, other: "UILevel") -> bool:
        """支持级别比较"""
        order = [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]
        return order.index(self) >= order.index(other)

    def __gt__(self, other: "UILevel") -> bool:
        """支持级别比较"""
        order = [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]
        return order.index(self) > order.index(other)

    def __le__(self, other: "UILevel") -> bool:
        """支持级别比较"""
        order = [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]
        return order.index(self) <= order.index(other)

    def __lt__(self, other: "UILevel") -> bool:
        """支持级别比较"""
        order = [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]
        return order.index(self) < order.index(other)


class ConsoleUIManager:
    """Rich 控制台 UI 管理器

    提供 Bot 运行时的可视化界面，包括启动横幅、进度跟踪、
    状态面板和实时仪表盘。

    根据 UI 级别提供差异化的视觉效果：
    - MINIMAL: 纯文本，无装饰
    - STANDARD: 适度装饰，进度条，面板
    - VERBOSE: 完整 Rich 特性，实时仪表盘

    Attributes:
        level: UI 详细程度级别
        console: Rich Console 实例
    """

    # 不同级别的样式配置
    _STYLES = {
        UILevel.MINIMAL: {
            "box": None,  # 无边框
            "use_color": False,
            "show_spinner": False,
            "show_progress_bar": False,
            "show_panel": False,
            "show_table_header": False,
        },
        UILevel.STANDARD: {
            "box": SIMPLE,
            "use_color": True,
            "show_spinner": True,
            "show_progress_bar": True,
            "show_panel": True,
            "show_table_header": True,
        },
        UILevel.VERBOSE: {
            "box": ROUNDED,
            "use_color": True,
            "show_spinner": True,
            "show_progress_bar": True,
            "show_panel": True,
            "show_table_header": True,
            "show_tree": True,
            "show_live": True,
        },
    }

    def __init__(self, level: UILevel = UILevel.STANDARD) -> None:
        """初始化 UI 管理器

        Args:
            level: UI 详细程度级别
        """
        self.level = level
        # MINIMAL 模式禁用颜色
        force_terminal = level != UILevel.MINIMAL
        self.console = Console(
            stderr=True,
            force_terminal=force_terminal,
            no_color=(level == UILevel.MINIMAL),
        )

        # 进度跟踪器（延迟创建）
        self._progress: Progress | None = None
        self._status: Status | None = None

        # 实时仪表盘（仅 VERBOSE 模式）
        self._live: Live | None = None
        self._layout: Layout | None = None
        self._dashboard_running = False

        # 启动进度上下文
        self._startup_progress: Progress | None = None
        self._startup_task_id: int | None = None

        # 统计数据（用于仪表盘）
        self._stats: dict[str, Any] = {
            "plugins_loaded": 0,
            "plugins_failed": 0,
            "components_by_type": {},
            "tasks_active": 0,
            "tasks_completed": 0,
            "db_connected": False,
            "scheduler_running": False,
            "uptime_start": None,
            "last_activity": None,
        }

    @property
    def style_config(self) -> dict[str, Any]:
        """获取当前级别的样式配置"""
        return self._STYLES.get(self.level, self._STYLES[UILevel.STANDARD])

    def show_banner(self, version: str, bot_name: str = "Neo-MoFox") -> None:
        """显示启动横幅

        根据 UI 级别显示不同风格的横幅：
        - MINIMAL: 单行纯文本
        - STANDARD: 简洁横幅 + 版本信息
        - VERBOSE: ASCII 艺术字 + 详细系统信息 + 装饰边框

        Args:
            version: Bot 版本号
            bot_name: Bot 名称
        """
        import platform

        if self.level == UILevel.MINIMAL:
            # MINIMAL: 单行纯文本
            self.console.print(
                f"{bot_name} v{version} - "
                f"Python {platform.python_version()} on {platform.system()}"
            )
            return

        if self.level == UILevel.STANDARD:
            # STANDARD: 简洁横幅
            self.console.print()
            self.console.print(Rule(f"[cyan bold]{bot_name}[/cyan bold]"))

            info_text = Text()
            info_text.append("版本: ", style="dim")
            info_text.append(f"{version}", style="green bold")
            info_text.append("  •  ", style="dim")
            info_text.append("Python: ", style="dim")
            info_text.append(f"{platform.python_version()}", style="blue")
            info_text.append("  •  ", style="dim")
            info_text.append(f"{platform.system()}", style="yellow")
            self.console.print(info_text, justify="center")
            self.console.print()
            return

        # VERBOSE: 完整 ASCII 艺术字横幅 + 边框
        from rich.align import Align
        
        try:
            import pyfiglet

            ascii_art = pyfiglet.figlet_format(bot_name, font="slant")
            # 用 Panel 包裹居中的 ASCII 艺术字
            self.console.print(
                Panel(
                    Align.center(Text(ascii_art.rstrip()+"\n", style="cyan bold")),
                    box=ROUNDED,
                    border_style="cyan",
                    padding=(0, 2),
                    title=f"[bold white]v{version}[/bold white]",
                    title_align="right",
                    subtitle="[dim]Neo-MoFox Bot Framework[/dim]",
                    subtitle_align="center",
                )
            )
        except ImportError:
            # 如果 pyfiglet 未安装，使用装饰性文本
            self.console.print()
            self.console.print(
                Panel(
                    Align.center(f"[cyan bold]{bot_name}[/cyan bold]"),
                    box=HEAVY,
                    border_style="cyan",
                    padding=(1, 4),
                    title=f"[bold white]v{version}[/bold white]",
                    title_align="right",
                )
            )

        # 详细系统信息
        sys_info = Table(show_header=False, box=None, padding=(0, 2))
        sys_info.add_column("Key", style="dim")
        sys_info.add_column("Value")

        sys_info.add_row("Python", f"[blue]{platform.python_version()}[/blue]")
        sys_info.add_row("平台", f"[yellow]{platform.system()}[/yellow]")
        sys_info.add_row("架构", f"[magenta]{platform.machine()}[/magenta]")
        sys_info.add_row(
            "时间",
            f"[dim]{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        )

        self.console.print(sys_info)
        self.console.print()

    def create_progress_tracker(self) -> Progress:
        """创建进度跟踪器

        根据 UI 级别返回不同复杂度的进度条：
        - MINIMAL: 仅文本描述
        - STANDARD: 文本 + 细长进度条 + 百分比
        - VERBOSE: 完整进度条 + Spinner + 时间估算

        Returns:
            Progress: Rich 进度条实例
        """
        if self._progress is None:
            if self.level == UILevel.MINIMAL:
                # MINIMAL: 仅显示文本，无进度条
                self._progress = Progress(
                    TextColumn("{task.description}"),
                    console=self.console,
                    transient=True,
                )
            elif self.level == UILevel.STANDARD:
                # STANDARD: 细长进度条
                self._progress = Progress(
                    SpinnerColumn("dots"),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(
                        bar_width=50,
                        style="bar.back",
                        complete_style="bar.complete",
                        finished_style="bar.finished",
                    ),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    console=self.console,
                )
            else:
                # VERBOSE: 完整细长进度条 + 时间估算
                self._progress = Progress(
                    SpinnerColumn("dots12"),
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(
                        bar_width=60,
                        style="#3a3a3a",
                        complete_style="#00d787",
                        finished_style="bold #00ff87",
                        pulse_style="#00d787",
                    ),
                    MofNCompleteColumn(),
                    TextColumn("[dim]│[/dim]"),
                    TaskProgressColumn(),
                    TextColumn("[dim]│[/dim]"),
                    TimeElapsedColumn(),
                    TextColumn("[dim]/[/dim]"),
                    TimeRemainingColumn(),
                    console=self.console,
                    expand=True,
                )
        return self._progress

    @contextmanager
    def startup_progress(self, total_steps: int = 5) -> Iterator[None]:
        """启动进度上下文管理器

        提供启动过程中的进度跟踪。

        Args:
            total_steps: 总步骤数

        Yields:
            None
        """
        if self.level == UILevel.MINIMAL:
            # MINIMAL: 无进度条
            yield
            return

        progress = self.create_progress_tracker()
        self._startup_progress = progress
        self._startup_task_id = progress.add_task("启动中...", total=total_steps)

        try:
            with progress:
                yield
        finally:
            self._startup_progress = None
            self._startup_task_id = None

    def advance_startup(self, description: str = "") -> None:
        """推进启动进度

        Args:
            description: 当前步骤描述
        """
        if self._startup_progress and self._startup_task_id is not None:
            if description:
                self._startup_progress.update(
                    self._startup_task_id, description=description
                )
            self._startup_progress.advance(self._startup_task_id)

    @contextmanager
    def status_spinner(self, message: str) -> Iterator[Status]:
        """状态 Spinner 上下文管理器

        根据 UI 级别显示不同的状态指示：
        - MINIMAL: 仅打印文本
        - STANDARD/VERBOSE: 显示动态 Spinner

        Args:
            message: 状态消息

        Yields:
            Status: Rich Status 实例（MINIMAL 模式下为 None）
        """
        if self.level == UILevel.MINIMAL:
            self.console.print(f"  {message}...")
            yield None  # type: ignore
            return

        spinner_type = "dots" if self.level == UILevel.STANDARD else "dots12"
        with self.console.status(
            f"[bold blue]{message}...",
            spinner=spinner_type,
        ) as status:
            yield status

    def update_phase_status(
        self, phase: str, status: str, total_steps: int = 1, completed_step: int = 1
    ) -> None:
        """更新初始化阶段状态

        根据 UI 级别显示不同风格的状态更新：
        - MINIMAL: 简单文本 "Phase: Status"
        - STANDARD: 带图标的彩色状态
        - VERBOSE: 带进度指示的详细状态

        Args:
            phase: 阶段名称（如 "Initializing Kernel"）
            status: 状态描述
            total_steps: 总步骤数
            completed_step: 已完成步骤数
        """
        if self.level == UILevel.MINIMAL:
            # MINIMAL: 纯文本
            self.console.print(f"  [{completed_step}/{total_steps}] {phase}: {status}")
        elif self.level == UILevel.STANDARD:
            # STANDARD: 带图标的彩色状态
            icon = "✓" if completed_step == total_steps else "→"
            color = "green" if completed_step == total_steps else "cyan"
            self.console.print(
                f"[{color}]{icon}[/{color}] [bold]{phase}[/bold]: {status}"
            )
        else:
            # VERBOSE: 详细状态 + 进度条样式
            progress_bar = self._create_inline_progress(completed_step, total_steps)
            self.console.print(
                f"[bold cyan]{phase}[/bold cyan] {progress_bar} [dim]{status}[/dim]"
            )

    def _create_inline_progress(
        self, current: int, total: int, width: int = 20
    ) -> str:
        """创建内联进度条字符串

        使用细腻的 Unicode 字符创建美观的进度条。

        Args:
            current: 当前进度
            total: 总进度
            width: 进度条宽度（默认 20）

        Returns:
            str: 进度条字符串
        """
        if total <= 0:
            return f"[dim]{'─' * width}[/dim]"

        # 计算进度
        ratio = current / total
        filled_width = ratio * width
        full_blocks = int(filled_width)
        remainder = filled_width - full_blocks

        # 使用不同的字符表示部分填充（8 级细分）
        # ▏▎▍▌▋▊▉█
        partial_chars = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
        partial_index = int(remainder * 8)
        partial_char = partial_chars[partial_index] if partial_index < 8 else ""

        # 构建进度条
        filled = "━" * full_blocks
        empty = "─" * (width - full_blocks - (1 if partial_char else 0))

        # 根据完成度选择颜色
        if ratio >= 1.0:
            color = "bold green"
        elif ratio >= 0.6:
            color = "green"
        elif ratio >= 0.3:
            color = "yellow"
        else:
            color = "cyan"

        return f"[{color}]{filled}{partial_char}[/{color}][dim]{empty}[/dim]"

    def update_plugin_progress(self, plugin_name: str, success: bool) -> None:
        """更新插件加载进度

        根据 UI 级别显示不同风格的插件状态：
        - MINIMAL: 仅失败时输出
        - STANDARD: 带图标的状态行
        - VERBOSE: 详细信息 + 统计

        Args:
            plugin_name: 插件名称
            success: 是否加载成功
        """
        if success:
            self._stats["plugins_loaded"] += 1
            if self.level == UILevel.MINIMAL:
                # MINIMAL: 成功时不输出
                pass
            elif self.level == UILevel.STANDARD:
                # STANDARD: 简洁成功消息
                self.console.print(
                    f"  [green]✓[/green] [cyan]{plugin_name}[/cyan]"
                )
            else:
                # VERBOSE: 详细成功消息
                loaded = self._stats["plugins_loaded"]
                self.console.print(
                    f"  [green]✓[/green] 已加载插件: [cyan bold]{plugin_name}[/cyan bold] "
                    f"[dim](#{loaded})[/dim]"
                )
        else:
            self._stats["plugins_failed"] += 1
            if self.level == UILevel.MINIMAL:
                # MINIMAL: 失败总是输出
                self.console.print(f"  FAILED: {plugin_name}")
            else:
                # STANDARD/VERBOSE: 带样式的失败消息
                self.console.print(
                    f"  [red]✗[/red] 插件加载失败: [cyan]{plugin_name}[/cyan]"
                )

    def display_plugin_plan(
        self, load_order: list[str], manifests: dict[str, PluginManifest]
    ) -> None:
        """显示插件加载计划

        根据 UI 级别显示不同复杂度的插件信息：
        - MINIMAL: 单行汇总
        - STANDARD: 简洁表格
        - VERBOSE: 详细表格 + 树形依赖视图

        Args:
            load_order: 插件加载顺序
            manifests: 插件清单字典
        """
        if self.level == UILevel.MINIMAL:
            # MINIMAL: 单行汇总
            self.console.print(f"  Plugins to load: {len(load_order)}")
            return

        if self.level == UILevel.STANDARD:
            # STANDARD: 简洁表格
            self.console.print()
            self.console.print(
                f"[bold]插件加载计划[/bold] [dim]({len(load_order)} 个)[/dim]"
            )
            for idx, plugin_name in enumerate(load_order, 1):
                manifest = manifests[plugin_name]
                self.console.print(
                    f"  [dim]{idx:2}.[/dim] [cyan]{plugin_name}[/cyan] "
                    f"[dim]v{manifest.version}[/dim]"
                )
            self.console.print()
            return

        # VERBOSE: 详细表格 + 依赖树
        self.console.print()
        self.console.print(Rule("[bold cyan]插件加载计划[/bold cyan]"))

        # 创建详细表格
        table = Table(
            title=f"共 {len(load_order)} 个插件",
            box=ROUNDED,
            show_lines=True,
            header_style="bold magenta",
        )
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("插件名称", style="cyan bold")
        table.add_column("版本", style="green")
        table.add_column("作者", style="yellow")
        table.add_column("描述", style="dim", max_width=40)
        table.add_column("依赖", style="blue")

        for idx, plugin_name in enumerate(load_order, 1):
            manifest = manifests[plugin_name]
            deps = ", ".join(manifest.dependencies) if manifest.dependencies else "-"
            desc = (
                manifest.description[:37] + "..."
                if len(manifest.description) > 40
                else manifest.description
            )
            table.add_row(
                str(idx),
                plugin_name,
                manifest.version,
                manifest.author,
                desc,
                deps,
            )

        self.console.print(table)

        # 显示依赖树
        self._display_dependency_tree(load_order, manifests)
        self.console.print()

    def _display_dependency_tree(
        self, load_order: list[str], manifests: dict[str, PluginManifest]
    ) -> None:
        """显示插件依赖树（仅 VERBOSE 模式）

        Args:
            load_order: 插件加载顺序
            manifests: 插件清单字典
        """
        if self.level != UILevel.VERBOSE:
            return

        # 找出有依赖的插件
        has_deps = [
            name for name in load_order
            if manifests[name].dependencies
        ]

        if not has_deps:
            return

        self.console.print()
        tree = Tree("[bold]插件依赖关系[/bold]", guide_style="dim")

        for plugin_name in has_deps:
            manifest = manifests[plugin_name]
            plugin_branch = tree.add(f"[cyan]{plugin_name}[/cyan]")
            for dep in manifest.dependencies:
                if dep in manifests:
                    plugin_branch.add(f"[green]✓[/green] {dep}")
                else:
                    plugin_branch.add(f"[yellow]?[/yellow] {dep} [dim](external)[/dim]")

        self.console.print(tree)

    def display_error(
        self, message: str, exc: Exception | None = None
    ) -> None:
        """显示错误信息

        根据 UI 级别显示不同风格的错误：
        - MINIMAL: 纯文本 "ERROR: message"
        - STANDARD: 红色边框面板
        - VERBOSE: 详细面板 + 异常堆栈

        Args:
            message: 错误消息
            exc: 异常实例（可选）
        """
        if self.level == UILevel.MINIMAL:
            # MINIMAL: 纯文本
            error_msg = f"ERROR: {message}"
            if exc:
                error_msg += f" ({exc})"
            self.console.print(error_msg)
            return

        if self.level == UILevel.STANDARD:
            # STANDARD: 简洁面板
            content = f"[red]{message}[/red]"
            if exc:
                content += f"\n[dim]{exc}[/dim]"
            self.console.print(
                Panel(content, title="[red]Error[/red]", border_style="red")
            )
            return

        # VERBOSE: 详细面板 + 可能的堆栈追踪
        error_group = []
        error_group.append(Text(message, style="red bold"))

        if exc:
            error_group.append(Text())
            error_group.append(Text(f"异常类型: {type(exc).__name__}", style="yellow"))
            error_group.append(Text(f"异常信息: {exc}", style="dim"))

            # 尝试显示简短堆栈
            import traceback
            tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
            if tb_lines:
                error_group.append(Text())
                error_group.append(Text("堆栈追踪:", style="dim italic"))
                # 只显示最后几行
                for line in tb_lines[-5:]:
                    error_group.append(Text(line.rstrip(), style="dim"))

        self.console.print(
            Panel(
                Group(*error_group),
                title="[bold red]❌ Error[/bold red]",
                border_style="red",
                box=HEAVY,
                padding=(1, 2),
            )
        )

    def display_warning(self, message: str) -> None:
        """显示警告信息

        根据 UI 级别显示不同风格的警告：
        - MINIMAL: 纯文本 "WARNING: message"
        - STANDARD: 黄色边框面板
        - VERBOSE: 带图标的警告面板

        Args:
            message: 警告消息
        """
        if self.level == UILevel.MINIMAL:
            self.console.print(f"WARNING: {message}")
            return

        if self.level == UILevel.STANDARD:
            self.console.print(
                Panel(
                    f"[yellow]{message}[/yellow]",
                    title="[yellow]Warning[/yellow]",
                    border_style="yellow",
                )
            )
            return

        # VERBOSE: 带图标的警告
        self.console.print(
            Panel(
                f"[yellow bold]⚠️  {message}[/yellow bold]",
                title="[bold yellow]Warning[/bold yellow]",
                border_style="yellow",
                box=ROUNDED,
                padding=(0, 2),
            )
        )

    def display_success(self, message: str) -> None:
        """显示成功信息

        根据 UI 级别显示不同风格的成功消息：
        - MINIMAL: 纯文本 "OK: message"
        - STANDARD: 绿色边框面板
        - VERBOSE: 带图标和动画的成功面板

        Args:
            message: 成功消息
        """
        if self.level == UILevel.MINIMAL:
            self.console.print(f"OK: {message}")
            return

        if self.level == UILevel.STANDARD:
            self.console.print(
                Panel(
                    f"[green]{message}[/green]",
                    title="[green]Success[/green]",
                    border_style="green",
                )
            )
            return

        # VERBOSE: 带图标的成功消息
        self.console.print(
            Panel(
                f"[green bold]✅ {message}[/green bold]",
                title="[bold green]Success[/bold green]",
                border_style="green",
                box=ROUNDED,
                padding=(0, 2),
            )
        )

    def display_info(self, message: str, title: str = "Info") -> None:
        """显示信息消息

        根据 UI 级别显示不同风格的信息：
        - MINIMAL: 纯文本
        - STANDARD: 蓝色边框面板
        - VERBOSE: 带图标的信息面板

        Args:
            message: 信息内容
            title: 标题
        """
        if self.level == UILevel.MINIMAL:
            self.console.print(f"  {message}")
            return

        if self.level == UILevel.STANDARD:
            self.console.print(
                Panel(
                    f"[blue]{message}[/blue]",
                    title=f"[blue]{title}[/blue]",
                    border_style="blue",
                )
            )
            return

        # VERBOSE: 带图标的信息
        self.console.print(
            Panel(
                f"[blue]ℹ️  {message}[/blue]",
                title=f"[bold blue]{title}[/bold blue]",
                border_style="blue",
                box=ROUNDED,
                padding=(0, 2),
            )
        )

    def start_live_dashboard(self) -> None:
        """启动实时仪表盘（仅 VERBOSE 模式）

        创建 Layout 分屏显示实时状态，包括：
        - 头部：Bot 名称和状态
        - 左侧：系统状态面板
        - 右侧：插件和任务统计
        - 底部：时间戳和快捷键提示
        """
        if self.level != UILevel.VERBOSE:
            return

        self._stats["uptime_start"] = datetime.datetime.now()

        # 创建布局
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=4),
        )

        # 主体部分分为两列
        layout["body"].split_row(
            Layout(name="left_panel", ratio=1),
            Layout(name="right_panel", ratio=1),
        )

        # 左侧再分为系统状态和连接状态
        layout["left_panel"].split_column(
            Layout(name="system_status", ratio=1),
            Layout(name="connection_status", ratio=1),
        )

        # 右侧分为插件状态和任务状态
        layout["right_panel"].split_column(
            Layout(name="plugin_status", ratio=1),
            Layout(name="task_status", ratio=1),
        )

        self._layout = layout
        self._dashboard_running = True

        # 初始化面板内容
        self._update_dashboard_panels()

        # 启动 Live 显示
        self._live = Live(
            layout,
            console=self.console,
            refresh_per_second=2,
            screen=False,
            transient=False,
        )

    def _update_dashboard_panels(self) -> None:
        """更新仪表盘所有面板"""
        if not self._layout:
            return

        # 更新头部
        header = Table(show_header=False, box=None, expand=True)
        header.add_column("left", ratio=1)
        header.add_column("center", ratio=2, justify="center")
        header.add_column("right", ratio=1, justify="right")

        Spinner("dots", style="green") if self._dashboard_running else "●"
        header.add_row(
            Text("Neo-MoFox Bot", style="cyan bold"),
            Text("实时仪表盘", style="dim"),
            Text("运行中", style="green bold") if self._dashboard_running else Text("已停止", style="red"),
        )
        self._layout["header"].update(Panel(header, box=SIMPLE, border_style="cyan"))

        # 系统状态面板
        sys_table = Table(show_header=True, header_style="bold", box=SIMPLE)
        sys_table.add_column("指标", style="cyan")
        sys_table.add_column("状态", justify="right")

        db_status = "[green]●[/green] 已连接" if self._stats.get("db_connected") else "[red]●[/red] 未连接"
        sched_status = "[green]●[/green] 运行中" if self._stats.get("scheduler_running") else "[yellow]●[/yellow] 已停止"

        sys_table.add_row("数据库", db_status)
        sys_table.add_row("调度器", sched_status)

        # 计算运行时间
        if self._stats.get("uptime_start"):
            uptime = datetime.datetime.now() - self._stats["uptime_start"]
            uptime_str = str(uptime).split(".")[0]  # 去掉微秒
            sys_table.add_row("运行时间", f"[dim]{uptime_str}[/dim]")

        self._layout["system_status"].update(
            Panel(sys_table, title="[bold]系统状态[/bold]", border_style="blue", box=ROUNDED)
        )

        # 连接状态面板
        conn_table = Table(show_header=False, box=None)
        conn_table.add_column("Key", style="dim")
        conn_table.add_column("Value")

        tasks_active = self._stats.get("tasks_active", 0)
        tasks_completed = self._stats.get("tasks_completed", 0)

        conn_table.add_row("活动任务", f"[yellow]{tasks_active}[/yellow]")
        conn_table.add_row("完成任务", f"[green]{tasks_completed}[/green]")

        self._layout["connection_status"].update(
            Panel(conn_table, title="[bold]任务概览[/bold]", border_style="magenta", box=ROUNDED)
        )

        # 插件状态面板
        plugin_table = Table(show_header=False, box=None)
        plugin_table.add_column("Metric", style="cyan")
        plugin_table.add_column("Value", justify="right")

        loaded = self._stats.get("plugins_loaded", 0)
        failed = self._stats.get("plugins_failed", 0)
        total = loaded + failed

        plugin_table.add_row("已加载", f"[green]{loaded}[/green]")
        plugin_table.add_row("失败", f"[red]{failed}[/red]" if failed > 0 else "[dim]0[/dim]")
        plugin_table.add_row("总计", f"[bold]{total}[/bold]")

        # 显示组件统计
        components = self._stats.get("components_by_type", {})
        if components:
            plugin_table.add_row("", "")  # 空行分隔
            for comp_type, count in components.items():
                plugin_table.add_row(
                    f"  {comp_type.capitalize()}",
                    f"[blue]{count}[/blue]"
                )

        self._layout["plugin_status"].update(
            Panel(plugin_table, title="[bold]插件统计[/bold]", border_style="green", box=ROUNDED)
        )

        # 任务状态面板 - 显示最近活动
        task_content = Text()
        last_activity = self._stats.get("last_activity")
        if last_activity:
            task_content.append("最近活动:\n", style="dim")
            task_content.append(f"  {last_activity}\n", style="cyan")
        else:
            task_content.append("等待活动...", style="dim italic")

        self._layout["task_status"].update(
            Panel(task_content, title="[bold]活动日志[/bold]", border_style="yellow", box=ROUNDED)
        )

        # 更新底部
        footer = Table(show_header=False, box=None, expand=True)
        footer.add_column("left", ratio=1)
        footer.add_column("right", ratio=1, justify="right")

        footer.add_row(
            Text("快捷键: /help 帮助 | /stop 停止 | /ui level <级别>", style="dim"),
            Text(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), style="dim"),
        )
        self._layout["footer"].update(Panel(footer, box=SIMPLE, border_style="dim"))

    def update_dashboard_stats(self, stats: dict[str, Any]) -> None:
        """更新仪表盘统计数据

        Args:
            stats: 统计数据字典
        """
        if not self._dashboard_running or self._layout is None:
            return

        # 更新内部统计数据
        self._stats.update(stats)
        self._stats["last_activity"] = datetime.datetime.now().strftime("%H:%M:%S")

        # 更新所有面板
        self._update_dashboard_panels()

    def record_activity(self, activity: str) -> None:
        """记录活动日志（用于仪表盘显示）

        Args:
            activity: 活动描述
        """
        self._stats["last_activity"] = f"{datetime.datetime.now().strftime('%H:%M:%S')} - {activity}"
        if self._dashboard_running:
            self._update_dashboard_panels()

    def stop_live_dashboard(self) -> None:
        """停止实时仪表盘"""
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        self._dashboard_running = False
        self._layout = None

    def display_status(self, status: dict[str, Any]) -> None:
        """显示状态信息（非仪表盘模式）

        根据 UI 级别显示不同风格的状态信息：
        - MINIMAL: 键值对列表
        - STANDARD: 简洁表格
        - VERBOSE: 详细表格 + 面板

        Args:
            status: 状态信息字典
        """
        if self.level == UILevel.MINIMAL:
            # MINIMAL: 简单键值对
            for key, value in status.items():
                self.console.print(f"  {key}: {value}")
            return

        if self.level == UILevel.STANDARD:
            # STANDARD: 简洁表格
            table = Table(box=SIMPLE, show_header=True)
            table.add_column("指标", style="cyan")
            table.add_column("值")

            for key, value in status.items():
                table.add_row(key.replace("_", " ").title(), str(value))

            self.console.print(table)
            return

        # VERBOSE: 详细表格 + 面板
        table = Table(
            title="Bot 状态",
            box=ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("指标", style="cyan bold")
        table.add_column("值", style="green")
        table.add_column("说明", style="dim")

        descriptions = {
            "plugins_loaded": "已成功加载的插件数量",
            "plugins_failed": "加载失败的插件数量",
            "db_connected": "数据库连接状态",
            "scheduler_running": "调度器运行状态",
            "tasks_active": "当前活动任务数",
            "tasks_completed": "已完成任务数",
        }

        for key, value in status.items():
            desc = descriptions.get(key, "")
            display_key = key.replace("_", " ").title()

            # 美化布尔值显示
            if isinstance(value, bool):
                display_value = "[green]✓[/green]" if value else "[red]✗[/red]"
            else:
                display_value = str(value)

            table.add_row(display_key, display_value, desc)

        self.console.print(table)

    def display_command_prompt(self) -> None:
        """显示命令提示符"""
        if self.level == UILevel.MINIMAL:
            self.console.print("> ", end="")
        else:
            self.console.print("\n[green bold]>[/green bold] ", end="")

    def display_command_result(self, command: str, result: str | None = None) -> None:
        """显示命令执行结果

        Args:
            command: 执行的命令
            result: 执行结果（可选）
        """
        if self.level == UILevel.MINIMAL:
            if result:
                self.console.print(f"  {result}")
            return

        if self.level == UILevel.STANDARD:
            if result:
                self.console.print(f"  [dim]→[/dim] {result}")
            return

        # VERBOSE: 带面板的结果
        if result:
            self.console.print(
                Panel(
                    result,
                    title=f"[dim]/{command}[/dim]",
                    border_style="dim",
                    box=SIMPLE,
                )
            )

    def display_table(
        self,
        data: list[dict[str, Any]],
        columns: list[str] | None = None,
        title: str = "",
    ) -> None:
        """通用表格显示方法

        根据 UI 级别显示不同风格的表格。

        Args:
            data: 数据列表
            columns: 列名列表（可选，默认使用数据的键）
            title: 表格标题
        """
        if not data:
            if self.level != UILevel.MINIMAL:
                self.console.print("[dim]无数据[/dim]")
            return

        if columns is None:
            columns = list(data[0].keys())

        if self.level == UILevel.MINIMAL:
            # MINIMAL: 简单文本
            for row in data:
                line = " | ".join(str(row.get(col, "")) for col in columns)
                self.console.print(f"  {line}")
            return

        # STANDARD/VERBOSE: 表格
        box_style = SIMPLE if self.level == UILevel.STANDARD else ROUNDED
        table = Table(title=title if title else None, box=box_style)

        for col in columns:
            table.add_column(col.replace("_", " ").title(), style="cyan")

        for row in data:
            table.add_row(*[str(row.get(col, "")) for col in columns])

        self.console.print(table)

    def print(self, *args: Any, **kwargs: Any) -> None:
        """直接打印到控制台

        Args:
            *args: 传递给 console.print 的参数
            **kwargs: 传递给 console.print 的关键字参数
        """
        self.console.print(*args, **kwargs)

    def log(self, message: str, level: str = "info") -> None:
        """记录日志消息

        根据 UI 级别和日志级别显示不同风格的消息。

        Args:
            message: 日志消息
            level: 日志级别 (info, debug, warning, error)
        """
        level_styles = {
            "debug": ("dim", "🔍"),
            "info": ("blue", "ℹ️"),
            "warning": ("yellow", "⚠️"),
            "error": ("red", "❌"),
        }

        style, icon = level_styles.get(level, ("white", "•"))

        if self.level == UILevel.MINIMAL:
            prefix = level.upper()
            self.console.print(f"[{prefix}] {message}")
        elif self.level == UILevel.STANDARD:
            self.console.print(f"[{style}]{message}[/{style}]")
        else:
            # VERBOSE: 带时间戳和图标
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.console.print(
                f"[dim]{timestamp}[/dim] {icon} [{style}]{message}[/{style}]"
            )

    def section(self, title: str) -> None:
        """显示分节标题

        根据 UI 级别显示不同风格的分节：
        - MINIMAL: 简单分隔线
        - STANDARD: 带标题的分隔线
        - VERBOSE: 带装饰的分隔线

        Args:
            title: 分节标题
        """
        if self.level == UILevel.MINIMAL:
            self.console.print(f"\n--- {title} ---")
        elif self.level == UILevel.STANDARD:
            self.console.print()
            self.console.print(Rule(title))
        else:
            self.console.print()
            self.console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan"))

    def clear(self) -> None:
        """清屏"""
        if self.level != UILevel.MINIMAL:
            self.console.clear()

    def __del__(self) -> None:
        """析构函数，确保资源清理"""
        self.stop_live_dashboard()


__all__ = ["UILevel", "ConsoleUIManager"]
