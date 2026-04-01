"""交互式命令解析器

处理用户输入的交互式命令。
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import TYPE_CHECKING, Any, Callable

from src.kernel.terminal_io import begin_user_input, end_user_input

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
except ImportError:
    PromptSession = None
    patch_stdout = None

if TYPE_CHECKING:
    from .bot import Bot


class CommandExecutionError(Exception):
    """命令执行异常"""

    pass


class CommandParser:
    """交互式命令解析器

    解析和执行用户输入的命令。

    支持的命令：
    - /help: 显示帮助信息
    - /status: 显示 Bot 状态
    - /reload [plugin_name]: 重新加载插件
    - /stop: 停止 Bot
    - /plugins: 列出所有插件及状态
    - /tasks: 显示当前任务统计
    - /ui level [minimal|standard|verbose]: 调整 UI 级别

    Attributes:
        bot: Bot 实例
        commands: 命令处理器字典
    """

    def __init__(self, bot: "Bot") -> None:
        """初始化命令解析器

        Args:
            bot: Bot 实例
        """
        self.bot = bot
        self.commands: dict[str, Callable[[list[str]], Any]] = {}
        self._help_texts: dict[str, str] = {}
        self._input_queue: queue.Queue[str | BaseException] = queue.Queue()
        self._input_stop_event = threading.Event()
        self._prompt_session = PromptSession("> ") if PromptSession is not None else None
        self._input_thread = threading.Thread(
            target=self._input_worker,
            name="command_input_reader",
            daemon=True,
        )
        self._input_thread.start()

        # 注册默认命令
        self._register_default_commands()

    def _input_worker(self) -> None:
        """后台读取标准输入并写入队列。"""
        while not self._input_stop_event.is_set():
            try:
                line = self._read_input_line()
                self._input_queue.put(line)
            except EOFError as exc:
                self._input_queue.put(exc)
                break
            except KeyboardInterrupt:
                # 在 Windows 上 Ctrl+C 通常由主线程处理，此处忽略并继续等待。
                continue
            except Exception as exc:
                self._input_queue.put(exc)
                break

    def _read_input_line(self) -> str:
        """读取一行输入。"""
        begin_user_input()
        try:
            if self._prompt_session is not None and patch_stdout is not None:
                with patch_stdout(raw=True):
                    return self._prompt_session.prompt()
            return input("> ")
        finally:
            end_user_input()

    def close(self) -> None:
        """关闭命令解析器资源。"""
        self._input_stop_event.set()

    async def _get_next_input(
        self, timeout: float = 0.2
    ) -> str | BaseException | None:
        """异步获取下一条输入。"""
        try:
            return await asyncio.to_thread(self._input_queue.get, True, timeout)
        except queue.Empty:
            return None

    def _register_default_commands(self) -> None:
        """注册默认命令"""
        self.register_command("help", self.cmd_help, "显示帮助信息")
        self.register_command("status", self.cmd_status, "显示 Bot 状态")
        self.register_command(
            "reload", self.cmd_reload, "重新加载插件 [plugin_name]"
        )
        self.register_command("stop", self.cmd_stop, "停止 Bot")
        self.register_command("plugins", self.cmd_plugins, "列出所有插件及状态")
        self.register_command("tasks", self.cmd_tasks, "显示当前任务统计")
        self.register_command(
            "ui", self.cmd_ui, "调整 UI 级别 (minimal|standard|verbose)"
        )

        dog_controller = getattr(self.bot, "dog_controller", None)
        if dog_controller is not None:
            dog_controller.register_commands(self)

    def register_command(
        self, name: str, handler: Callable[[list[str]], Any], help_text: str
    ) -> None:
        """注册命令

        Args:
            name: 命令名称
            handler: 命令处理函数
            help_text: 帮助文本
        """
        self.commands[name] = handler
        self._help_texts[name] = help_text

    async def read_and_execute(self) -> bool:
        """读取并执行命令

        Returns:
            bool: False 表示应该停止 Bot

        Raises:
            CommandExecutionError: 命令执行失败
        """
        try:
            input_item = await self._get_next_input()
            if input_item is None:
                return True

            if isinstance(input_item, BaseException):
                if isinstance(input_item, EOFError):
                    return False
                if isinstance(input_item, KeyboardInterrupt):
                    return False
                raise input_item

            line = input_item

            if not line:
                return True

            line = line.strip()

            # 检查是否是命令
            if not line.startswith("/"):
                self.bot.ui.console.print(
                    "[yellow]未知命令。输入 /help 查看可用命令。[/yellow]"
                )
                return True

            # 解析命令
            parts = line[1:].split()  # 移除开头的 /
            if not parts:
                return True

            command_name = parts[0]
            args = parts[1:]

            # 查找命令处理器
            if command_name not in self.commands:
                self.bot.ui.console.print(
                    f"[red]未知命令: {command_name}[/red]"
                )
                return True

            # 执行命令
            handler = self.commands[command_name]
            await handler(args)

            # 检查是否应该停止
            if command_name == "stop":
                return False

            return True

        except EOFError:
            # 输入结束（如 Ctrl+D）
            return False
        except KeyboardInterrupt:
            # 用户中断（如 Ctrl+C）
            return False
        except Exception as e:
            raise CommandExecutionError(
                f"Failed to execute command: {e}"
            ) from e

    async def cmd_help(self, args: list[str]) -> None:
        """显示帮助信息

        Args:
            args: 命令参数（忽略）
        """
        from rich.table import Table

        table = Table(title="可用命令")
        table.add_column("命令", style="cyan")
        table.add_column("描述", style="dim")

        for cmd_name, help_text in self._help_texts.items():
            table.add_row(f"/{cmd_name}", help_text)

        self.bot.ui.console.print(table)

    async def cmd_status(self, args: list[str]) -> None:
        """显示 Bot 状态

        Args:
            args: 命令参数（忽略）
        """
        if not self.bot._initialized:
            self.bot.ui.console.print("[yellow]Bot 尚未初始化。[/yellow]")
            return

        # 收集状态信息
        from src.kernel.concurrency import get_task_manager

        task_manager = get_task_manager()

        status = {
            "已初始化": self.bot._initialized,
            "运行中": self.bot._running,
            "已加载插件": self.bot._stats.get("plugins_loaded", 0),
            "加载失败插件": self.bot._stats.get("plugins_failed", 0),
            "活动任务": len(task_manager.get_all_tasks()),
        }

        self.bot.ui.display_status(status)

    async def cmd_reload(self, args: list[str]) -> None:
        """重新加载插件

        Args:
            args: 命令参数
                - 无参数：重新加载所有插件
                - 有参数：重新加载指定插件
        """
        if not self.bot._initialized:
            self.bot.ui.console.print("[yellow]Bot 尚未初始化。[/yellow]")
            return

        plugin_name = args[0] if args else None

        try:
            if plugin_name:
                self.bot.ui.console.print(
                    f"[cyan]重新加载插件: {plugin_name}[/cyan]"
                )
            else:
                self.bot.ui.console.print("[cyan]重新加载所有插件...[/cyan]")

            results = await self.bot.reload_plugin(plugin_name)

            # 显示结果
            from rich.table import Table

            table = Table(title="重载结果")
            table.add_column("插件", style="cyan")
            table.add_column("状态", style="green")

            for name, success in results.items():
                status = "[green]✓ 成功[/green]" if success else "[red]✗ 失败[/red]"
                table.add_row(name, status)

            self.bot.ui.console.print(table)

        except Exception as e:
            self.bot.ui.display_error(f"插件重载失败: {e}", e)

    async def cmd_stop(self, args: list[str]) -> None:
        """停止 Bot

        Args:
            args: 命令参数（忽略）
        """
        self.bot.ui.console.print("[yellow]正在停止 Bot...[/yellow]")
        self.bot._running = False

    async def cmd_plugins(self, args: list[str]) -> None:
        """列出所有插件及状态

        Args:
            args: 命令参数（忽略）
        """
        from rich.table import Table

        if not self.bot.plugin_manager:
            self.bot.ui.console.print("[yellow]插件管理器未初始化。[/yellow]")
            return

        table = Table(title="已加载插件")
        table.add_column("插件", style="cyan")
        table.add_column("版本", style="blue")
        table.add_column("状态", style="green")

        from src.core.managers import get_plugin_manager

        plugin_manager = get_plugin_manager()
        plugins = plugin_manager.get_all_plugins()

        for plugin_name, plugin in plugins.items():
            # 尝试获取版本信息
            version = plugin.plugin_version
            status = "[green]运行中[/green]"

            table.add_row(plugin_name, version, status)

        self.bot.ui.console.print(table)

    async def cmd_tasks(self, args: list[str]) -> None:
        """显示当前任务统计

        Args:
            args: 命令参数（忽略）
        """
        from src.kernel.concurrency import get_task_manager

        task_manager = get_task_manager()
        tasks = task_manager.get_all_tasks()

        from rich.table import Table

        table = Table(title=f"活动任务 (共 {len(tasks)} 个)")
        table.add_column("任务名称", style="cyan")
        table.add_column("状态", style="yellow")

        for task_info in tasks:
            # 任务名称优先用 name，没有就退回 task_id
            task_name = task_info.name or task_info.task_id
            status = "运行中" if not task_info.is_done() else "已完成"
            table.add_row(task_name, status)

        self.bot.ui.console.print(table)

    async def cmd_ui(self, args: list[str]) -> None:
        """调整 UI 级别

        Args:
            args: 命令参数 (minimal|standard|verbose)
        """
        if not args:
            self.bot.ui.console.print(
                "[yellow]用法: /ui level <minimal|standard|verbose>[/yellow]"
            )
            return

        if args[0] != "level":
            self.bot.ui.console.print(
                "[yellow]用法: /ui level <minimal|standard|verbose>[/yellow]"
            )
            return

        if len(args) < 2:
            self.bot.ui.console.print(
                "[yellow]请指定 UI 级别: minimal, standard, 或 verbose[/yellow]"
            )
            return

        from .console_ui import UILevel

        level_map = {
            "minimal": UILevel.MINIMAL,
            "standard": UILevel.STANDARD,
            "verbose": UILevel.VERBOSE,
        }

        level_str = args[1].lower()
        if level_str not in level_map:
            self.bot.ui.console.print(
                f"[red]无效的 UI 级别: {level_str}[/red]\n"
                "[yellow]有效选项: minimal, standard, verbose[/yellow]"
            )
            return

        new_level = level_map[level_str]

        # 如果当前是 VERBOSE，需要停止仪表盘
        if self.bot.ui.level == UILevel.VERBOSE:
            self.bot.ui.stop_live_dashboard()

        # 更新 UI 级别
        self.bot.ui.level = new_level
        self.bot.ui.console.print(
            f"[green]UI 级别已更改为: {level_str}[/green]"
        )

        # 如果新级别是 VERBOSE，启动仪表盘
        if new_level == UILevel.VERBOSE and self.bot._running:
            self.bot.ui.start_live_dashboard()


__all__ = ["CommandParser", "CommandExecutionError"]
