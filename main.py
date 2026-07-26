"""Neo-MoFox 主入口

启动 Neo-MoFox Bot 应用。
"""
import asyncio


async def main() -> None:
    """主函数"""
    from src.app.runtime import Bot
    from src.app.runtime.user_agreements import ensure_startup_agreements

    # 创建 Bot 实例
    if not await ensure_startup_agreements("config/core.toml"):
        return

    bot = Bot(
        config_path="config/core.toml",
        plugins_dir="plugins",
        log_dir="logs",
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
