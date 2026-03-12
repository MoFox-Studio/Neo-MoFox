"""配置模块使用示例

演示 kernel.config 的核心用法：
- 定义静态可见的配置模型（ConfigBase + SectionBase）
- 从 TOML 文件加载
- 使用 auto_update=True 自动将配置文件同步到模型“签名”（节/字段/注释/类型/默认值）

运行：
    uv run python examples/src/kernel/config/config_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import tempfile

# 允许从任意工作目录直接运行该示例文件
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.kernel.config import ConfigBase, SectionBase, config_section, Field
from src.kernel.logger import get_logger, COLOR

# 创建全局 logger
logger = get_logger("config_example", display="Config", color=COLOR.YELLOW)


class AppConfig(ConfigBase):
    @config_section("database")
    class DatabaseSection(SectionBase):
        """数据库配置"""

        host: str = Field(default="localhost", description="数据库主机")
        port: int = Field(default=5432, description="数据库端口")

    @config_section("features")
    class FeaturesSection(SectionBase):
        """功能开关"""

        enable_cache: bool = Field(default=True, description="启用缓存")
        enable_logging: bool = Field(default=False, description="启用日志")

    database: DatabaseSection = Field(default_factory=DatabaseSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)


def _write_initial_toml(path: Path) -> None:
    # 刻意写入：
    # - port 用字符串表示（类型签名不一致，但可被 pydantic 规范化为 int）
    # - features 节缺少 enable_logging
    # - 含有未定义字段 legacy 和未定义节 unused
    path.write_text(
        (
            "[database]\n"
            "host = \"db.example.com\"\n"
            "port = \"3306\"\n"
            "legacy = 1\n\n"
            "[features]\n"
            "enable_cache = false\n\n"
            "[unused]\n"
            "foo = \"bar\"\n"
        ),
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "app_config.toml"
        _write_initial_toml(config_path)

        logger.info("=== 原始配置文件内容 ===")
        logger.info(config_path.read_text(encoding="utf-8"))

        cfg = AppConfig.load(config_path, auto_update=True)

        logger.info("=== 加载后的配置对象 ===")
        logger.info(f"database.host = {cfg.database.host}")
        logger.info(f"database.port = {cfg.database.port}")
        logger.info(f"features.enable_cache = {cfg.features.enable_cache}")
        logger.info(f"features.enable_logging = {cfg.features.enable_logging}")

        logger.info("=== auto_update=True 后的配置文件内容 ===")
        logger.info(config_path.read_text(encoding="utf-8"))

        logger.info("[OK] 示例完成")


if __name__ == "__main__":
    main()
