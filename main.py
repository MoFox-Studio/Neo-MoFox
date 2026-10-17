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
        # 用户中断（Ctrl+C）。强制退出路径可能已同步恢复终端，
        # 这里作为兜底：KeyboardInterrupt 也可能从其它路径抛出，
        # 确保 raw 模式不会残留导致终端卡死。
        try:
            from src.app.runtime.console_input import restore_terminal

            restore_terminal()
        except Exception:
            pass
        print("\n[Interrupted by user]")
    except Exception as e:
        # 捕获并显示其他异常
        try:
            from src.app.runtime.console_input import restore_terminal

            restore_terminal()
        except Exception:
            pass
        print(f"\n[Fatal error: {e}]")
        raise
