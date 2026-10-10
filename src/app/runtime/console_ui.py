"""控制台 UI 管理器

使用 Rich 库提供现代化的控制台界面，包括：
- ASCII 艺术字横幅
- 进度条跟踪
- 详细状态面板
- 差异化视觉效果

本模块仅保留单一详细输出模式，不再支持 UI 级别切换与实时仪表盘。
"""

from __future__ import annotations

import datetime
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

from rich.box import ROUNDED, SIMPLE, HEAVY
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

if TYPE_CHECKING:
    from src.core.components import PluginManifest


class ConsoleUIManager:
    """Rich 控制台 UI 管理器

    提供 Bot 运行时的可视化界面，包括启动横幅、进度跟踪和状态面板。

    采用单一详细输出模式：完整 Rich 装饰效果，详细表格、树形结构，
    动态 Spinner 和进度指示。

    Attributes:
        console: Rich Console 实例
    """

    def __init__(self) -> None:
        """初始化 UI 管理器。"""
        self.console = Console(stderr=True, force_terminal=True)

        # 进度跟踪器（延迟创建）
        self._progress: Progress | None = None
        self._status: Status | None = None

        # 启动进度上下文
        self._startup_progress: Progress | None = None
        self._startup_task_id: TaskID | None = None
        self._plugin_task_id: TaskID | None = None
        self._startup_live: Any | None = None

        # 统计数据（用于状态显示）
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

    def show_banner(self, version: str, bot_name: str = "Neo-MoFox") -> None:
        """显示启动横幅

        显示完整 ASCII 艺术字横幅 + 边框 + 详细系统信息。

        Args:
            version: Bot 版本号
            bot_name: Bot 名称
        """
        import platform

        from rich.align import Align

        try:
            import pyfiglet

            ascii_art = pyfiglet.figlet_format(bot_name, font="slant")
            # 用 Panel 包裹居中的 ASCII 艺术字
            self.console.print(
                Panel(
                    Align.center(Text(ascii_art.rstrip() + "\n", style="cyan bold")),
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

        返回完整进度条 + Spinner + 时间估算的 Rich 进度条实例。

        Returns:
            Progress: Rich 进度条实例
        """
        if self._progress is None:
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
    def startup_progress(self, total_steps: int = 14) -> Iterator[None]:
        """启动进度上下文管理器 —— 显示单一总体进度条

        在整个初始化过程中呈现一条宽进度条，每个子阶段完成后推进一格，
        而不是为每个阶段单独打印一行。

        Args:
            total_steps: 固定初始化步骤数（不含插件子进度）

        Yields:
            None
        """
        from rich.live import Live

        progress = Progress(
            SpinnerColumn("dots12"),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(
                bar_width=None,
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

        self._startup_progress = progress
        self._startup_task_id = progress.add_task("初始化系统", total=total_steps)
        self._plugin_task_id = None

        live = Live(
            progress,
            console=self.console,
            refresh_per_second=12,
            vertical_overflow="visible",
            transient=False,
        )
        self._startup_live = live

        try:
            with live:
                yield
        finally:
            self._startup_progress = None
            self._startup_task_id = None
            self._plugin_task_id = None
            self._startup_live = None

    def begin_plugin_loading(self, total_plugins: int) -> None:
        """在启动进度条中添加插件加载子进度

        在 ``startup_progress`` 上下文内调用，追加一条独立的插件进度任务，
        显示在初始化进度条下方。

        Args:
            total_plugins: 待加载插件总数
        """
        if self._startup_progress is None or total_plugins <= 0:
            return

        self._plugin_task_id = self._startup_progress.add_task(
            "加载插件",
            total=total_plugins,
        )

    def advance_startup(self, description: str = "") -> None:
        """推进启动进度（兼容旧调用，新代码请直接用 update_phase_status）

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

        显示动态 Spinner 状态指示。

        Args:
            message: 状态消息

        Yields:
            Status: Rich Status 实例
        """
        with self.console.status(
            f"[bold blue]{message}...",
            spinner="dots12",
        ) as status:
            yield status

    def update_phase_status(
        self, phase: str, status: str, total_steps: int = 1, completed_step: int = 1
    ) -> None:
        """更新初始化阶段状态

        当处于 ``startup_progress`` 上下文内时，更新总体进度条的描述并（对终态）
        推进一格，而不是打印一行新文本。

        终态判断：status 不以 ``...`` 结尾视为终态，会推进进度。
        示例终态： 已加载 / 已初始化 / 已连接 / 已启动 / 已完成 / 已跳过
        示例非终态：启动中... / 进行中... / 扫描中...

        若未在 startup_progress 上下文内，则回退为打印文本。

        Args:
            phase: 阶段名称（如 "初始化内核"）
            status: 状态描述
            total_steps: 总步骤数（仅非进度条模式使用）
            completed_step: 已完成步骤数（仅非进度条模式使用）
        """
        # ── 大进度条模式 ──────────────────────────────────────────
        if self._startup_progress is not None and self._startup_task_id is not None:
            is_terminal = not status.endswith("...")
            desc = (
                f"{phase}  [dim]· {status}[/dim]"
                if is_terminal
                else f"{phase}  [dim]{status}[/dim]"
            )
            self._startup_progress.update(self._startup_task_id, description=desc)
            if is_terminal:
                self._startup_progress.advance(self._startup_task_id)
            return

        # ── 回退：无进度条时打印文本 ───────────────────────────────
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

        当处于 ``startup_progress`` 上下文且已调用 ``begin_plugin_loading`` 时，
        推进插件进度条并向控制台打印一行日志（Rich Live 会将它置于进度条上方）。
        否则退化为打印文本。

        Args:
            plugin_name: 插件名称
            success: 是否加载成功
        """
        # 统计更新
        if success:
            self._stats["plugins_loaded"] += 1
        else:
            self._stats["plugins_failed"] += 1

        # ── 大进度条模式：推进插件任务 ─────────────────────────────
        if self._startup_progress is not None and self._plugin_task_id is not None:
            if success:
                loaded = self._stats["plugins_loaded"]
                self.console.print(
                    f"  [green]✓[/green] [cyan]{plugin_name}[/cyan]"
                    f" [dim](#{loaded})[/dim]"
                )
            else:
                self.console.print(
                    f"  [red]✗[/red] 插件加载失败: [cyan]{plugin_name}[/cyan]"
                )
            self._startup_progress.advance(self._plugin_task_id)
            return

        # ── 回退：无进度条时打印文本 ───────────────────────────────
        if success:
            loaded = self._stats["plugins_loaded"]
            self.console.print(
                f"  [green]✓[/green] 已加载插件: [cyan bold]{plugin_name}[/cyan bold] "
                f"[dim](#{loaded})[/dim]"
            )
        else:
            self.console.print(
                f"  [red]✗[/red] 插件加载失败: [cyan]{plugin_name}[/cyan]"
            )

    def display_plugin_plan(
        self, load_order: list[str], manifests: dict[str, PluginManifest]
    ) -> None:
        """显示插件加载计划

        显示详细表格 + 树形依赖视图。

        Args:
            load_order: 插件加载顺序
            manifests: 插件清单字典
        """
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
        """显示插件依赖树

        Args:
            load_order: 插件加载顺序
            manifests: 插件清单字典
        """
        # 找出有依赖的插件
        has_deps = [
            name
            for name in load_order
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

        显示详细面板 + 异常堆栈。

        Args:
            message: 错误消息
            exc: 异常实例（可选）
        """
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

        显示带图标的警告面板。

        Args:
            message: 警告消息
        """
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

        显示带图标和动画的成功面板。

        Args:
            message: 成功消息
        """
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

        显示带图标的信息面板。

        Args:
            message: 信息内容
            title: 标题
        """
        self.console.print(
            Panel(
                f"[blue]ℹ️  {message}[/blue]",
                title=f"[bold blue]{title}[/bold blue]",
                border_style="blue",
                box=ROUNDED,
                padding=(0, 2),
            )
        )

    def display_status(self, status: dict[str, Any]) -> None:
        """显示状态信息

        显示详细表格 + 面板。

        Args:
            status: 状态信息字典
        """
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
        self.console.print("\n[green bold]>[/green bold] ", end="")

    def display_command_result(self, command: str, result: str | None = None) -> None:
        """显示命令执行结果

        Args:
            command: 执行的命令
            result: 执行结果（可选）
        """
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

        Args:
            data: 数据列表
            columns: 列名列表（可选，默认使用数据的键）
            title: 表格标题
        """
        if not data:
            self.console.print("[dim]无数据[/dim]")
            return

        if columns is None:
            columns = list(data[0].keys())

        table = Table(title=title if title else None, box=ROUNDED)

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

        根据日志级别显示带时间戳和图标的消息。

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

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.console.print(
            f"[dim]{timestamp}[/dim] {icon} [{style}]{message}[/{style}]"
        )

    def section(self, title: str) -> None:
        """显示分节标题

        显示带装饰的分隔线。

        Args:
            title: 分节标题
        """
        self.console.print()
        self.console.print(Rule(f"[bold cyan]{title}[/bold cyan]", style="cyan"))

    def clear(self) -> None:
        """清屏"""
        self.console.clear()


__all__ = ["ConsoleUIManager"]
