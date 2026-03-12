"""Neo-MoFox 主入口

启动 Neo-MoFox Bot 应用。
"""
import tomllib
import asyncio

from src.app.runtime import UILevel

def load_ui_level_from_config(config_path: str = "config/core.toml") -> "UILevel":  # type: ignore
    """从配置文件加载 UI 级别

    Args:
        config_path: 配置文件路径

    Returns:
        UILevel: UI 级别枚举值
    """
    

    level_map = {
        "minimal": UILevel.MINIMAL,
        "standard": UILevel.STANDARD,
        "verbose": UILevel.VERBOSE,
    }

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
            ui_level_str = config.get("bot", {}).get("ui_level", "standard").lower()
            return level_map.get(ui_level_str, UILevel.STANDARD)
    except Exception:
        return UILevel.STANDARD


async def main() -> None:
    """主函数"""
    from src.app.runtime import Bot

    # 从配置文件读取 UI 级别
    ui_level = load_ui_level_from_config("config/core.toml")

    # 创建 Bot 实例
    bot = Bot(
        config_path="config/core.toml",
        plugins_dir="plugins",
        log_dir="logs",
        ui_level=ui_level,
    )

    # 启动 Bot（包含初始化、运行和关闭）
    await bot.start()


if __name__ == "__main__":
    try:
        # 运行异步主函数
        asyncio.run(main())
    except KeyboardInterrupt:
        # 用户中断（Ctrl+C）
        print("\n[Interrupted by user]")
    except Exception as e:
        # 捕获并显示其他异常
        print(f"\n[Fatal error: {e}]")
        raise
