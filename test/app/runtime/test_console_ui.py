"""Console UI 管理器测试"""


from src.app.runtime.console_ui import UILevel, ConsoleUIManager


class TestUILevel:
    """测试 UILevel 枚举"""

    def test_ui_level_values(self) -> None:
        """测试 UI 级别枚举值"""
        assert UILevel.MINIMAL.value == "minimal"
        assert UILevel.STANDARD.value == "standard"
        assert UILevel.VERBOSE.value == "verbose"

    def test_ui_level_comparison_ge(self) -> None:
        """测试 UI 级别比较 >="""
        assert UILevel.VERBOSE >= UILevel.STANDARD
        assert UILevel.VERBOSE >= UILevel.MINIMAL
        assert UILevel.STANDARD >= UILevel.MINIMAL
        assert UILevel.STANDARD >= UILevel.STANDARD
        assert not (UILevel.MINIMAL >= UILevel.STANDARD)

    def test_ui_level_comparison_gt(self) -> None:
        """测试 UI 级别比较 >"""
        assert UILevel.VERBOSE > UILevel.STANDARD
        assert UILevel.VERBOSE > UILevel.MINIMAL
        assert UILevel.STANDARD > UILevel.MINIMAL
        assert not (UILevel.STANDARD > UILevel.STANDARD)

    def test_ui_level_comparison_le(self) -> None:
        """测试 UI 级别比较 <="""
        assert UILevel.MINIMAL <= UILevel.STANDARD
        assert UILevel.MINIMAL <= UILevel.VERBOSE
        assert UILevel.STANDARD <= UILevel.VERBOSE
        assert UILevel.STANDARD <= UILevel.STANDARD

    def test_ui_level_comparison_lt(self) -> None:
        """测试 UI 级别比较 <"""
        assert UILevel.MINIMAL < UILevel.STANDARD
        assert UILevel.MINIMAL < UILevel.VERBOSE
        assert UILevel.STANDARD < UILevel.VERBOSE
        assert not (UILevel.STANDARD < UILevel.STANDARD)


class TestConsoleUIManager:
    """测试 ConsoleUIManager"""

    def test_initialization(self) -> None:
        """测试 UI 管理器初始化"""
        ui = ConsoleUIManager()
        assert ui.level == UILevel.STANDARD
        assert ui.console is not None

    def test_initialization_with_level(self) -> None:
        """测试使用指定级别初始化"""
        ui = ConsoleUIManager(level=UILevel.VERBOSE)
        assert ui.level == UILevel.VERBOSE

    def test_initialization_minimal_no_color(self) -> None:
        """测试 MINIMAL 模式禁用颜色"""
        ui = ConsoleUIManager(level=UILevel.MINIMAL)
        assert ui.console.no_color is True

    def test_style_config_property(self) -> None:
        """测试样式配置属性"""
        # MINIMAL
        ui_minimal = ConsoleUIManager(level=UILevel.MINIMAL)
        assert ui_minimal.style_config["use_color"] is False
        assert ui_minimal.style_config["show_spinner"] is False

        # STANDARD
        ui_standard = ConsoleUIManager(level=UILevel.STANDARD)
        assert ui_standard.style_config["use_color"] is True
        assert ui_standard.style_config["show_spinner"] is True

        # VERBOSE
        ui_verbose = ConsoleUIManager(level=UILevel.VERBOSE)
        assert ui_verbose.style_config["use_color"] is True
        assert ui_verbose.style_config.get("show_live") is True

    def test_create_progress_tracker(self) -> None:
        """测试创建进度跟踪器"""
        ui = ConsoleUIManager()
        progress = ui.create_progress_tracker()
        assert progress is not None

    def test_create_progress_tracker_minimal(self) -> None:
        """测试 MINIMAL 模式进度跟踪器"""
        ui = ConsoleUIManager(level=UILevel.MINIMAL)
        progress = ui.create_progress_tracker()
        assert progress is not None
        # MINIMAL 模式进度条较简单（只有一个 TextColumn）
        # 检查列数是否比 STANDARD/VERBOSE 少
        assert len(progress.columns) < 5

    def test_create_progress_tracker_verbose(self) -> None:
        """测试 VERBOSE 模式进度跟踪器"""
        ui = ConsoleUIManager(level=UILevel.VERBOSE)
        progress = ui.create_progress_tracker()
        assert progress is not None
        # VERBOSE 模式应该有更多列
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

    def test_start_stop_live_dashboard(self) -> None:
        """测试启动和停止实时仪表盘"""
        ui = ConsoleUIManager(level=UILevel.VERBOSE)

        # 启动仪表盘
        ui.start_live_dashboard()
        assert ui._dashboard_running is True
        assert ui._stats["uptime_start"] is not None

        # 停止仪表盘
        ui.stop_live_dashboard()
        assert ui._dashboard_running is False
        assert ui._live is None

    def test_dashboard_not_started_for_non_verbose(self) -> None:
        """测试非 VERBOSE 模式下仪表盘不启动"""
        ui = ConsoleUIManager(level=UILevel.STANDARD)
        ui.start_live_dashboard()
        assert ui._dashboard_running is False

    def test_record_activity(self) -> None:
        """测试记录活动"""
        ui = ConsoleUIManager()
        ui.record_activity("测试活动")
        assert "测试活动" in ui._stats["last_activity"]

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

    def test_display_error_all_levels(self) -> None:
        """测试所有级别的错误显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            # 不应抛出异常
            ui.display_error("测试错误")
            ui.display_error("测试错误", ValueError("详细信息"))

    def test_display_warning_all_levels(self) -> None:
        """测试所有级别的警告显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.display_warning("测试警告")

    def test_display_success_all_levels(self) -> None:
        """测试所有级别的成功显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.display_success("测试成功")

    def test_display_info_all_levels(self) -> None:
        """测试所有级别的信息显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.display_info("测试信息")
            ui.display_info("测试信息", title="自定义标题")

    def test_section_all_levels(self) -> None:
        """测试所有级别的分节显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.section("测试分节")

    def test_log_all_levels(self) -> None:
        """测试所有级别的日志显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            for log_level in ["debug", "info", "warning", "error"]:
                ui.log(f"测试 {log_level} 消息", level=log_level)

    def test_display_status_all_levels(self) -> None:
        """测试所有级别的状态显示不抛异常"""
        status = {
            "plugins_loaded": 5,
            "db_connected": True,
            "scheduler_running": False,
        }
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.display_status(status)

    def test_display_table_all_levels(self) -> None:
        """测试所有级别的表格显示不抛异常"""
        data = [
            {"name": "Plugin1", "version": "1.0"},
            {"name": "Plugin2", "version": "2.0"},
        ]
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.display_table(data)
            ui.display_table(data, columns=["name"], title="测试表格")
            ui.display_table([])  # 空数据

    def test_show_banner_all_levels(self) -> None:
        """测试所有级别的横幅显示不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.show_banner("1.0.0", "TestBot")

    def test_update_phase_status_all_levels(self) -> None:
        """测试所有级别的阶段状态更新不抛异常"""
        for level in [UILevel.MINIMAL, UILevel.STANDARD, UILevel.VERBOSE]:
            ui = ConsoleUIManager(level=level)
            ui.update_phase_status("初始化", "进行中", total_steps=3, completed_step=1)
            ui.update_phase_status("初始化", "完成", total_steps=3, completed_step=3)
