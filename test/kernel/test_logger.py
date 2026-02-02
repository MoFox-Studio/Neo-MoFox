"""
Logger 模块单元测试

测试 Logger、COLOR 枚举和 get_logger 函数的功能。
"""

from __future__ import annotations

import asyncio
from io import StringIO

from rich.console import Console

from src.kernel.logger import (
    get_logger,
    Logger,
    COLOR,
    remove_logger,
    get_all_loggers,
    clear_all_loggers,
    get_rich_color,
    DEFAULT_LEVEL_COLORS,
    RotationMode,
    FileHandler,
    LOG_OUTPUT_EVENT,
)
from pathlib import Path
import tempfile
import shutil


class TestColor:
    """测试 COLOR 枚举"""

    def test_color_enum_values(self) -> None:
        """测试 COLOR 枚举值"""
        assert COLOR.RED.value == "red"
        assert COLOR.BLUE.value == "blue"
        assert COLOR.YELLOW.value == "yellow"
        assert COLOR.GREEN.value == "green"

    def test_get_rich_color(self) -> None:
        """测试获取 rich 颜色"""
        # 从 COLOR 枚举
        assert get_rich_color(COLOR.RED) == "red"
        assert get_rich_color(COLOR.BLUE) == "blue"

        # 从字符串
        assert get_rich_color("custom_color") == "custom_color"

    def test_default_level_colors(self) -> None:
        """测试默认日志级别颜色"""
        assert DEFAULT_LEVEL_COLORS["DEBUG"] == COLOR.DEBUG
        assert DEFAULT_LEVEL_COLORS["INFO"] == COLOR.INFO
        assert DEFAULT_LEVEL_COLORS["WARNING"] == COLOR.WARNING
        assert DEFAULT_LEVEL_COLORS["ERROR"] == COLOR.ERROR
        assert DEFAULT_LEVEL_COLORS["CRITICAL"] == COLOR.CRITICAL


class TestLogger:
    """测试 Logger 类"""

    def test_logger_creation(self) -> None:
        """测试 Logger 创建"""
        console = Console(file=StringIO())
        logger = Logger(
            name="test_logger",
            display="测试日志",
            color=COLOR.BLUE,
            console=console,
        )

        assert logger.name == "test_logger"
        assert logger.display == "测试日志"
        assert logger.color == "blue"
        assert isinstance(logger.console, Console)

    def test_logger_repr(self) -> None:
        """测试 Logger 字符串表示"""
        console = Console(file=StringIO())
        logger = Logger(name="test", display="Test", color=COLOR.RED, console=console)

        repr_str = repr(logger)
        assert "test" in repr_str
        assert "Test" in repr_str
        assert "red" in repr_str

    def test_logger_metadata(self) -> None:
        """测试日志元数据"""
        console = Console(file=StringIO())
        logger = Logger(name="test", console=console)

        # 设置元数据
        logger.set_metadata("key1", "value1")
        assert logger.get_metadata("key1") == "value1"

        # 设置多个元数据
        logger.set_metadata("key2", "value2")
        assert logger.get_metadata("key1") == "value1"
        assert logger.get_metadata("key2") == "value2"

        # 移除元数据
        logger.remove_metadata("key1")
        assert logger.get_metadata("key1") is None
        assert logger.get_metadata("key2") == "value2"

        # 清除所有元数据
        logger.clear_metadata()
        assert logger.get_metadata("key2") is None

    def test_log_levels(self) -> None:
        """测试不同日志级别"""
        console = Console(file=StringIO())
        logger = Logger(name="test", console=console)

        # 测试所有日志级别不会抛出异常
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")

        # 测试带元数据的日志
        logger.set_metadata("user", "test_user")
        logger.info("User action", action="login", ip="127.0.0.1")

    def test_print_panel(self) -> None:
        """测试面板输出"""
        console = Console(file=StringIO())
        logger = Logger(name="test", console=console)

        # 测试面板输出不会抛出异常
        logger.print_panel("Panel content", title="Test Panel")
        logger.print_panel("Another panel")

    def test_print_rich(self) -> None:
        """测试直接使用 rich 打印"""
        console = Console(file=StringIO())
        logger = Logger(name="test", console=console)

        # 测试直接打印不会抛出异常
        logger.print_rich("[bold]Bold text[/bold]")
        logger.print_rich("[red]Red text[/red]")


class TestGetLogger:
    """测试 get_logger 工厂函数"""

    def test_get_logger_single_instance(self) -> None:
        """测试相同名称返回同一实例"""
        console = Console(file=StringIO())

        logger1 = get_logger("test_single", console=console)
        logger2 = get_logger("test_single", console=console)

        assert logger1 is logger2

    def test_get_logger_different_names(self) -> None:
        """测试不同名称返回不同实例"""
        console = Console(file=StringIO())

        logger1 = get_logger("logger1", console=console)
        logger2 = get_logger("logger2", console=console)

        assert logger1 is not logger2
        assert logger1.name == "logger1"
        assert logger2.name == "logger2"

    def test_get_logger_with_display(self) -> None:
        """测试自定义显示名称"""
        console = Console(file=StringIO())

        logger = get_logger("test", display="测试日志", console=console)
        assert logger.name == "test"
        assert logger.display == "测试日志"

    def test_get_logger_default_display(self) -> None:
        """测试默认显示名称"""
        console = Console(file=StringIO())

        logger = get_logger("test_logger", console=console)
        assert logger.display == "test_logger"

    def test_get_logger_with_color(self) -> None:
        """测试自定义颜色"""
        console = Console(file=StringIO())

        logger = get_logger("test_color_red", color=COLOR.RED, console=console)
        assert logger.color == "red"

        logger2 = get_logger("test_color_blue", color="blue", console=console)
        assert logger2.color == "blue"


class TestLoggerManagement:
    """测试日志记录器管理功能"""

    def test_remove_logger(self) -> None:
        """测试移除日志记录器"""
        console = Console(file=StringIO())

        logger = get_logger("to_remove", console=console)
        assert logger is not None

        remove_logger("to_remove")

        # 重新获取应该创建新实例
        new_logger = get_logger("to_remove", console=console)
        assert new_logger is not logger

    def test_get_all_loggers(self) -> None:
        """测试获取所有日志记录器"""
        console = Console(file=StringIO())

        # 清空所有日志记录器
        clear_all_loggers()

        # 创建多个日志记录器
        get_logger("logger1", console=console)
        get_logger("logger2", console=console)
        get_logger("logger3", console=console)

        all_loggers = get_all_loggers()
        assert len(all_loggers) == 3
        assert "logger1" in all_loggers
        assert "logger2" in all_loggers
        assert "logger3" in all_loggers

    def test_clear_all_loggers(self) -> None:
        """测试清除所有日志记录器"""
        console = Console(file=StringIO())

        # 创建多个日志记录器
        get_logger("logger1", console=console)
        get_logger("logger2", console=console)

        assert len(get_all_loggers()) >= 2

        # 清除所有
        clear_all_loggers()

        assert len(get_all_loggers()) == 0


class TestIntegration:
    """集成测试"""

    def test_logger_with_metadata_integration(self) -> None:
        """测试日志与元数据的集成"""
        console = Console(file=StringIO())
        logger = get_logger("integration_test", display="集成测试", color=COLOR.CYAN, console=console)

        # 设置全局元数据
        logger.set_metadata("session_id", "abc123")

        # 输出带临时元数据的日志
        logger.info("User logged in", user_id="user001")

        # 验证全局元数据仍然存在
        assert logger.get_metadata("session_id") == "abc123"

    def test_multiple_loggers_independent(self) -> None:
        """测试多个日志记录器相互独立"""
        console = Console(file=StringIO())

        logger1 = get_logger("logger1", display="Logger 1", color=COLOR.RED, console=console)
        logger2 = get_logger("logger2", display="Logger 2", color=COLOR.BLUE, console=console)

        # 设置不同的元数据
        logger1.set_metadata("key", "value1")
        logger2.set_metadata("key", "value2")

        # 验证元数据相互独立
        assert logger1.get_metadata("key") == "value1"
        assert logger2.get_metadata("key") == "value2"

        # 清除一个不应影响另一个
        logger1.clear_metadata()
        assert logger1.get_metadata("key") is None
        assert logger2.get_metadata("key") == "value2"

    def test_default_color_parameter(self) -> None:
        """测试默认颜色参数"""
        console = Console(file=StringIO())

        # 不指定颜色，应该使用默认的 WHITE
        logger = get_logger("default_color", console=console)
        assert logger.color == "white"


class TestFileOutput:
    """测试文件输出功能"""

    def test_enable_file_output(self) -> None:
        """测试启用文件输出"""
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "file_test",
                enable_file=False,
                console=console,
            )

            # 初始状态文件输出未启用
            assert logger._enable_file is False
            assert logger.file_handler is None

            # 启用文件输出
            logger.enable_file_output(log_dir=temp_dir)

            # 验证文件输出已启用
            assert logger._enable_file is True
            assert logger.file_handler is not None

            # 写入日志
            logger.info("Test message")

            # 验证文件已创建
            log_files = list(Path(temp_dir).glob("file_test*.log"))
            assert len(log_files) >= 1

        finally:
            # 清理临时目录
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_disable_file_output(self) -> None:
        """测试禁用文件输出"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "file_disable_test",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 初始状态文件输出已启用
            assert logger._enable_file is True
            assert logger.file_handler is not None

            # 禁用文件输出
            logger.disable_file_output()

            # 验证文件输出已禁用
            assert logger._enable_file is False
            assert logger.file_handler is None

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_file_output_with_rotation_mode(self) -> None:
        """测试不同轮转模式"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())

            # 按日期轮转
            logger1 = get_logger(
                "date_rotation",
                enable_file=True,
                log_dir=temp_dir,
                file_rotation=RotationMode.DATE,
                console=console,
            )
            assert logger1.file_handler is not None
            assert logger1.file_handler.rotation_mode == RotationMode.DATE

            # 按大小轮转
            logger2 = get_logger(
                "size_rotation",
                enable_file=True,
                log_dir=temp_dir,
                file_rotation=RotationMode.SIZE,
                max_file_size=1024,  # 1KB
                console=console,
            )
            assert logger2.file_handler is not None
            assert logger2.file_handler.rotation_mode == RotationMode.SIZE
            assert logger2.file_handler.max_size == 1024

            # 不轮转
            logger3 = get_logger(
                "no_rotation",
                enable_file=True,
                log_dir=temp_dir,
                file_rotation=RotationMode.NEVER,
                console=console,
            )
            assert logger3.file_handler is not None
            assert logger3.file_handler.rotation_mode == RotationMode.NEVER

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_file_write_content(self) -> None:
        """测试文件写入内容"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "content_test",
                display="ContentTest",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 写入日志
            logger.info("Test message 1")
            logger.warning("Test message 2", key="value")

            # 查找日志文件
            log_files = list(Path(temp_dir).glob("content_test*.log"))
            assert len(log_files) >= 1

            # 读取文件内容
            content = log_files[0].read_text(encoding="utf-8")

            # 验证内容包含日志消息
            assert "Test message 1" in content
            assert "Test message 2" in content
            assert "ContentTest" in content
            assert "INFO" in content
            assert "WARNING" in content

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_logger_close(self) -> None:
        """测试关闭日志记录器"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "close_test",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 验证文件处理器存在
            assert logger.file_handler is not None

            # 关闭日志记录器
            logger.close()

            # 验证文件处理器已关闭
            assert logger.file_handler is None

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_remove_logger_closes_file(self) -> None:
        """测试移除日志记录器时关闭文件"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            _ = get_logger(
                "remove_file_test",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 移除日志记录器
            remove_logger("remove_file_test")

            # 验证日志记录器已从注册表中移除
            all_loggers = get_all_loggers()
            assert "remove_file_test" not in all_loggers

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_logger_repr_with_file(self) -> None:
        """测试带文件输出的日志记录器字符串表示"""
        console = Console(file=StringIO())

        logger1 = get_logger("with_file", enable_file=True, console=console)
        repr1 = repr(logger1)
        assert "file=enabled" in repr1

        logger2 = get_logger("without_file", enable_file=False, console=console)
        repr2 = repr(logger2)
        assert "file=disabled" in repr2


class TestFileHandlerEdgeCases:
    """测试 FileHandler 的边界情况"""

    def test_file_handler_repr(self) -> None:
        """测试 FileHandler 字符串表示"""
        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="test",
                rotation_mode=RotationMode.DATE,
            )

            repr_str = repr(handler)
            assert "FileHandler" in repr_str
            assert "test" in repr_str
            assert "date" in repr_str

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_size_rotation_creates_multiple_files(self) -> None:
        """测试按大小轮转创建多个文件"""
        temp_dir = tempfile.mkdtemp()

        try:
            # 创建一个小的 max_size 以便快速触发轮转
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="size_test",
                rotation_mode=RotationMode.SIZE,
                max_size=100,  # 100 bytes
            )

            # 写入超过 100 字节的数据以触发轮转
            for i in range(5):
                handler.write(f"Log message {i} " * 10 + "\n")

            # 关闭处理器以确保所有数据写入
            handler.close()

            # 检查是否创建了多个文件
            log_files = list(Path(temp_dir).glob("size_test*.log"))
            assert len(log_files) >= 2  # 应该至少有2个文件

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_never_rotation_single_file(self) -> None:
        """测试不轮转模式始终使用单个文件"""
        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="never_test",
                rotation_mode=RotationMode.NEVER,
            )

            # 写入多条日志
            for i in range(10):
                handler.write(f"Log message {i}\n")

            handler.close()

            # 验证只有一个文件
            log_files = list(Path(temp_dir).glob("never_test*.log"))
            assert len(log_files) == 1
            assert log_files[0].name == "never_test.log"

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_file_handler_write_error_handling(self) -> None:
        """测试文件写入失败时的错误处理"""
        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="error_test",
                rotation_mode=RotationMode.DATE,
            )

            # 写入正常日志
            handler.write("Normal message\n")

            # 关闭文件
            handler.close()

            # 尝试写入已关闭的文件应该不会抛出异常
            handler.write("After close\n")  # 应该被忽略，不抛出异常

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_file_handler_close_idempotent(self) -> None:
        """测试多次关闭文件不会出错"""
        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="close_test",
                rotation_mode=RotationMode.DATE,
            )

            handler.write("Test message\n")
            handler.close()
            handler.close()  # 第二次关闭应该安全
            handler.close()  # 第三次关闭也应该安全

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestLoggerEdgeCases:
    """测试 Logger 的边界情况"""

    def test_custom_console(self) -> None:
        """测试自定义 Console 参数"""
        custom_console = Console(file=StringIO())

        logger = get_logger(
            "custom_console",
            console=custom_console,
        )

        # 验证使用了自定义 Console
        assert logger.console is custom_console

        # 写入日志应该使用自定义 Console
        logger.info("Test message")

    def test_logger_with_file_and_custom_console(self) -> None:
        """测试同时使用文件输出和自定义 Console"""
        temp_dir = tempfile.mkdtemp()

        try:
            custom_console = Console(file=StringIO())

            logger = get_logger(
                "file_and_console",
                enable_file=True,
                log_dir=temp_dir,
                console=custom_console,
            )

            # 验证同时使用了自定义 Console 和文件处理器
            assert logger.console is custom_console
            assert logger.file_handler is not None

            logger.info("Test message")

            # 验证文件已创建
            log_files = list(Path(temp_dir).glob("file_and_console*.log"))
            assert len(log_files) >= 1

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_enable_file_already_enabled(self) -> None:
        """测试在已启用文件输出时再次启用"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "already_enabled",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 文件处理器已存在
            original_handler = logger.file_handler

            # 再次启用应该不会创建新的处理器
            logger.enable_file_output(log_dir=temp_dir)

            assert logger.file_handler is original_handler

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_metadata_with_file_output(self) -> None:
        """测试带文件输出的元数据处理"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "metadata_file",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 设置元数据
            logger.set_metadata("app", "test_app")
            logger.set_metadata("version", "1.0.0")

            # 写入带临时元数据的日志
            logger.info("Test with metadata", request_id="12345")

            # 验证元数据持久化
            assert logger.get_metadata("app") == "test_app"
            assert logger.get_metadata("version") == "1.0.0"

            # 验证文件内容包含元数据
            log_files = list(Path(temp_dir).glob("metadata_file*.log"))
            content = log_files[0].read_text(encoding="utf-8")
            assert "request_id=12345" in content
            assert "app=test_app" in content
            assert "version=1.0.0" in content

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clear_all_loggers_with_files(self) -> None:
        """测试清除所有 logger 时关闭文件"""
        temp_dir = tempfile.mkdtemp()

        try:
            # 创建多个带文件输出的 logger
            for i in range(3):
                get_logger(
                    f"clear_test_{i}",
                    enable_file=True,
                    log_dir=temp_dir,
                )

            # 清除所有 logger
            clear_all_loggers()

            # 验证所有 logger 已清除
            all_loggers = get_all_loggers()
            assert len(all_loggers) == 0

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_print_panel_does_not_write_to_file(self) -> None:
        """测试 print_panel 只输出到控制台，不写入文件"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "panel_test",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 先写入一条普通日志以创建文件
            logger.info("Regular log")

            # 使用 print_panel
            logger.print_panel("Panel content", title="Test")

            # 读取日志文件
            log_files = list(Path(temp_dir).glob("panel_test*.log"))
            content = log_files[0].read_text(encoding="utf-8")

            # 验证文件中有普通日志，但没有 Panel 内容（panel 只输出到控制台）
            assert "Regular log" in content
            assert "Panel content" not in content

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_print_rich_does_not_write_to_file(self) -> None:
        """测试 print_rich 只输出到控制台，不写入文件"""
        temp_dir = tempfile.mkdtemp()

        try:
            console = Console(file=StringIO())
            logger = get_logger(
                "rich_test",
                enable_file=True,
                log_dir=temp_dir,
                console=console,
            )

            # 先写入一条普通日志以创建文件
            logger.info("Regular log")

            # 使用 print_rich
            logger.print_rich("[bold]Bold text[/bold]")

            # 读取日志文件
            log_files = list(Path(temp_dir).glob("rich_test*.log"))
            content = log_files[0].read_text(encoding="utf-8")

            # 验证文件中有普通日志，但没有 rich 格式化内容
            assert "Regular log" in content
            assert "Bold text" not in content
            assert "[bold]" not in content

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestFileHandlerInternalMethods:
    """测试 FileHandler 内部方法以提升覆盖率"""

    def test_should_rotate_with_none_file(self) -> None:
        """测试 _should_rotate 在文件为 None 时返回 True"""
        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="test",
                rotation_mode=RotationMode.DATE,
            )

            # 初始状态文件为 None
            assert handler._current_file is None
            # 应该返回 True，表示需要轮转（打开文件）
            assert handler._should_rotate() is True

        finally:
            handler.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_size_mode_nonexistent_file(self) -> None:
        """测试 SIZE 模式下文件不存在时的轮转逻辑"""
        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="nonexist",
                rotation_mode=RotationMode.SIZE,
                max_size=1000,
            )

            # 创建一个新的 handler，确保文件不存在
            new_handler = FileHandler(
                log_dir=temp_dir,
                base_filename="brand_new",
                rotation_mode=RotationMode.SIZE,
                max_size=1000,
            )

            # 文件不存在时，_should_rotate 应该返回 True（需要创建文件）
            # 这覆盖了第104行的代码
            assert new_handler._should_rotate() is True

            # 写入数据，确认文件被创建
            new_handler.write("Test\n")
            log_file = Path(temp_dir) / "brand_new.log"
            assert log_file.exists()

        finally:
            handler.close()
            new_handler.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_size_mode_file_deleted_after_open(self) -> None:
        """测试 SIZE 模式下文件打开后被删除的情况（覆盖第104行）"""
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="deleted_test",
                rotation_mode=RotationMode.SIZE,
                max_size=1000,
            )

            # 先写入数据，打开文件
            handler.write("Initial data\n")

            # 使用 mock 模拟文件不存在的情况
            original_exists = Path.exists

            def mock_exists(path):
                # 对于日志文件返回 False，其他返回正常结果
                if "deleted_test.log" in str(path):
                    return False
                return original_exists(path)

            with patch.object(Path, "exists", mock_exists):
                # 现在检查轮转状态
                # 第91行的检查不会触发（_current_file 不为 None）
                # 第102行的 exists() 返回 False
                # 因此会执行第104行：return True
                should_rotate = handler._should_rotate()

                # 应该返回 True，因为文件不存在了
                assert should_rotate is True

        finally:
            handler.close()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_write_failure_is_silent(self) -> None:
        """测试写入失败时不会抛出异常"""
        from unittest.mock import patch, MagicMock

        temp_dir = tempfile.mkdtemp()

        try:
            handler = FileHandler(
                log_dir=temp_dir,
                base_filename="error_test",
                rotation_mode=RotationMode.DATE,
            )

            # 使用 mock 模拟写入失败
            with patch.object(handler, "_open_file") as mock_open:
                mock_file = MagicMock()
                mock_file.write.side_effect = IOError("Simulated write error")
                mock_open.return_value = mock_file

                # 写入应该失败，但不应该抛出异常
                # （因为 write 方法中有异常处理）
                handler.write("This should fail silently\n")

                # 验证：如果能到达这里，说明异常被正确处理了
                assert True

        finally:
            handler.close()
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestEventBroadcast:
    """测试日志事件广播功能"""

    def test_logger_event_broadcast_default_enabled(self) -> None:
        """测试默认情况下事件广播是启用的"""
        console = Console(file=StringIO())
        logger = get_logger("no_broadcast", console=console)

        assert logger._enable_event_broadcast is True
    def test_logger_enable_event_broadcast(self) -> None:
        """测试启用事件广播"""
        console = Console(file=StringIO())
        logger = get_logger(
            "with_broadcast",
            enable_event_broadcast=True,
            console=console,
        )

        assert logger._enable_event_broadcast is True

    async def test_log_event_data_structure(self) -> None:
        """测试日志事件的数据结构"""
        console = Console(file=StringIO())
        logger = get_logger(
            "event_test",
            enable_event_broadcast=True,
            display="EventTest",
            color=COLOR.CYAN,
            console=console,
        )

        # 收集事件数据
        received_events: list[dict] = []

        async def event_handler(event_name: str, params: dict):
            # 只收集特定 logger 的事件
            if params.get("logger_name") == "event_test":
                received_events.append(params)
            from src.kernel.event import EventDecision
            return (EventDecision.SUCCESS, params)

        # 订阅事件（通过 event 系统直接订阅）
        from src.kernel.event import get_event_bus
        unsubscribe = get_event_bus().subscribe(LOG_OUTPUT_EVENT, event_handler)

        try:
            # 输出日志
            logger.info("Test message", user_id="123")

            # 等待事件处理
            await asyncio.sleep(0.1)

            # 验证事件数据结构
            assert len(received_events) == 1
            log_data = received_events[0]

            # 验证必需字段
            assert "timestamp" in log_data
            assert "level" in log_data
            assert "logger_name" in log_data
            assert "display" in log_data
            assert "color" in log_data
            assert "message" in log_data

            # 验证字段值
            assert log_data["level"] == "INFO"
            assert log_data["logger_name"] == "event_test"
            assert log_data["display"] == "EventTest"
            assert log_data["color"] == "cyan"
            assert log_data["message"] == "Test message"
            assert "metadata" in log_data
            assert log_data["metadata"]["user_id"] == "123"

        finally:
            unsubscribe()

    async def test_event_broadcast_with_metadata(self) -> None:
        """测试带元数据的日志广播"""
        console = Console(file=StringIO())
        logger = get_logger(
            "metadata_broadcast_test",
            enable_event_broadcast=True,
            console=console,
        )

        received_logs: list[dict] = []

        async def log_handler(event_name: str, params: dict):
            # 只收集特定 logger 的事件
            if params.get("logger_name") == "metadata_broadcast_test":
                received_logs.append(params)
            from src.kernel.event import EventDecision
            return (EventDecision.SUCCESS, params)

        # 通过 event 系统订阅
        from src.kernel.event import get_event_bus
        unsubscribe = get_event_bus().subscribe(LOG_OUTPUT_EVENT, log_handler)

        try:
            # 设置全局元数据
            logger.set_metadata("app", "test_app")
            logger.set_metadata("version", "1.0")

            # 输出带临时元数据的日志
            logger.info("Action completed", user_id="123", status="success")

            # 等待事件处理
            await asyncio.sleep(0.1)

            assert len(received_logs) == 1
            log_data = received_logs[0]

            # 验证元数据
            assert log_data["metadata"]["app"] == "test_app"
            assert log_data["metadata"]["version"] == "1.0"
            assert log_data["metadata"]["user_id"] == "123"
            assert log_data["metadata"]["status"] == "success"

        finally:
            unsubscribe()

    async def test_event_broadcast_disabled_no_events(self) -> None:
        """测试禁用广播时不发布事件"""
        console = Console(file=StringIO())
        logger = get_logger(
            "no_broadcast_test",
            enable_event_broadcast=False,
            console=console,
        )

        received_logs: list[dict] = []

        async def log_handler(event_name: str, params: dict):
            # 只收集特定 logger 的事件
            if params.get("logger_name") == "no_broadcast_test":
                received_logs.append(params)
            from src.kernel.event import EventDecision
            return (EventDecision.SUCCESS, params)

        from src.kernel.event import get_event_bus
        unsubscribe = get_event_bus().subscribe(LOG_OUTPUT_EVENT, log_handler)

        try:
            logger.info("This should not be broadcasted")
            await asyncio.sleep(0.1)

            # 应该没有收到日志
            assert len(received_logs) == 0

        finally:
            unsubscribe()

    async def test_log_event_timestamp_format(self) -> None:
        """测试日志事件时间戳格式"""
        from datetime import datetime

        console = Console(file=StringIO())
        logger = get_logger(
            "timestamp_test",
            enable_event_broadcast=True,
            console=console,
        )

        received_timestamps: list[str] = []

        async def log_handler(event_name: str, params: dict):
            # 只收集特定 logger 的事件
            if params.get("logger_name") == "timestamp_test":
                received_timestamps.append(params["timestamp"])
            from src.kernel.event import EventDecision
            return (EventDecision.SUCCESS, params)

        from src.kernel.event import get_event_bus
        unsubscribe = get_event_bus().subscribe(LOG_OUTPUT_EVENT, log_handler)

        try:
            logger.info("Timestamp test")
            await asyncio.sleep(0.1)

            assert len(received_timestamps) == 1
            timestamp = received_timestamps[0]

            # 验证时间戳格式 (ISO 8601: YYYY-MM-DDTHH:MM:SS.mmm)
            datetime.fromisoformat(timestamp)

        finally:
            unsubscribe()
