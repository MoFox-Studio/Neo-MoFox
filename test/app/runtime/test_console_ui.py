"""Console UI 管理器测试"""


from src.app.runtime.console_ui import ConsoleUIManager


class TestConsoleUIManager:
    """测试 ConsoleUIManager"""

    def test_initialization(self) -> None:
        """测试 UI 管理器初始化"""
        ui = ConsoleUIManager()
        assert ui.console is not None

    def test_create_progress_tracker(self) -> None:
        """测试创建进度跟踪器"""
        ui = ConsoleUIManager()
        progress = ui.create_progress_tracker()
        assert progress is not None
        # 详细模式下进度条应包含完整列
        assert len(progress.columns) > 5

    def test_stats_initialization(self) -> None:
        """测试统计数据初始化"""
        ui = ConsoleUIManager()
        assert ui._stats["plugins_loaded"] == 0
        assert ui._stats["plugins_failed"] == 0
        assert ui._stats["db_connected"] is False
        assert ui._stats["uptime_start"] is None
        assert ui._stats["last_activity"] is None

    def test_update_plugin_progress_success(self) -> None:
        """测试更新插件进度（成功）"""
        ui = ConsoleUIManager()
        ui.update_plugin_progress("test_plugin", success=True)
        assert ui._stats["plugins_loaded"] == 1
        assert ui._stats["plugins_failed"] == 0

    def test_update_plugin_progress_failure(self) -> None:
        """测试更新插件进度（失败）"""
        ui = ConsoleUIManager()
        ui.update_plugin_progress("test_plugin", success=False)
        assert ui._stats["plugins_loaded"] == 0
        assert ui._stats["plugins_failed"] == 1

    def test_inline_progress_bar(self) -> None:
        """测试内联进度条生成"""
        ui = ConsoleUIManager()
        bar = ui._create_inline_progress(5, 10, width=20)
        # 检查包含进度字符
        assert "━" in bar or "─" in bar

    def test_inline_progress_bar_empty(self) -> None:
        """测试空进度条"""
        ui = ConsoleUIManager()
        bar = ui._create_inline_progress(0, 10, width=20)
        # 空进度条应该都是未填充字符
        assert "─" in bar

    def test_inline_progress_bar_full(self) -> None:
        """测试满进度条"""
        ui = ConsoleUIManager()
        bar = ui._create_inline_progress(10, 10, width=20)
        # 满进度条应该有填充字符
        assert "━" in bar

    def test_display_error(self) -> None:
        """测试错误显示不抛异常"""
        ui = ConsoleUIManager()
        # 不应抛出异常
        ui.display_error("测试错误")
        ui.display_error("测试错误", ValueError("详细信息"))

    def test_display_warning(self) -> None:
        """测试警告显示不抛异常"""
        ui = ConsoleUIManager()
        ui.display_warning("测试警告")

    def test_display_success(self) -> None:
        """测试成功显示不抛异常"""
        ui = ConsoleUIManager()
        ui.display_success("测试成功")

    def test_display_info(self) -> None:
        """测试信息显示不抛异常"""
        ui = ConsoleUIManager()
        ui.display_info("测试信息")
        ui.display_info("测试信息", title="自定义标题")

    def test_section(self) -> None:
        """测试分节显示不抛异常"""
        ui = ConsoleUIManager()
        ui.section("测试分节")

    def test_log(self) -> None:
        """测试日志显示不抛异常"""
        ui = ConsoleUIManager()
        for log_level in ["debug", "info", "warning", "error"]:
            ui.log(f"测试 {log_level} 消息", level=log_level)

    def test_display_status(self) -> None:
        """测试状态显示不抛异常"""
        status = {
            "plugins_loaded": 5,
            "db_connected": True,
            "scheduler_running": False,
        }
        ui = ConsoleUIManager()
        ui.display_status(status)

    def test_display_table(self) -> None:
        """测试表格显示不抛异常"""
        data = [
            {"name": "Plugin1", "version": "1.0"},
            {"name": "Plugin2", "version": "2.0"},
        ]
        ui = ConsoleUIManager()
        ui.display_table(data)
        ui.display_table(data, columns=["name"], title="测试表格")
        ui.display_table([])  # 空数据

    def test_show_banner(self) -> None:
        """测试横幅显示不抛异常"""
        ui = ConsoleUIManager()
        ui.show_banner("1.0.0", "TestBot")

    def test_update_phase_status(self) -> None:
        """测试阶段状态更新不抛异常"""
        ui = ConsoleUIManager()
        ui.update_phase_status("初始化", "进行中", total_steps=3, completed_step=1)
        ui.update_phase_status("初始化", "完成", total_steps=3, completed_step=3)
