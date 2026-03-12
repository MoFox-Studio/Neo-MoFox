"""
adapter_api 示例脚本

展示适配器 API 的启动与查询能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import adapter_api, config_api
from src.core.components.loader import load_all_plugins
from src.core.components.registry import get_global_registry
from src.core.components.types import ComponentType
from src.core.config import init_core_config


async def main() -> None:
    """演示 adapter_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    await load_all_plugins(str(REPO_ROOT / "plugins"))

    registry = get_global_registry()
    adapter_components = registry.get_by_type(ComponentType.ADAPTER)
    print(f"已注册 Adapter 数量: {len(adapter_components)}")

    active_adapters = adapter_api.list_active_adapters()
    print(f"已启动 Adapter 数量: {len(active_adapters)}")

    if not adapter_components:
        return

    first_signature = next(iter(adapter_components.keys()))
    plugin_name = first_signature.split(":")[0]
    config = config_api.get_config(plugin_name)
    bot_section = getattr(config, "bot", None)
    qq_id = getattr(bot_section, "qq_id", "")
    qq_nickname = getattr(bot_section, "qq_nickname", "")
    can_start = (
        isinstance(qq_id, str)
        and qq_id.strip().isdigit()
        and isinstance(qq_nickname, str)
        and qq_nickname.strip()
    )
    if not can_start:
        print("跳过启动: bot.qq_id 或 bot.qq_nickname 未配置")
        return

    started = await adapter_api.start_adapter(first_signature)
    print(f"启动适配器结果: {started}")

    if adapter_api.is_adapter_active(first_signature):
        adapter_instance = adapter_api.get_adapter(first_signature)
        if adapter_instance:
            bot_info = await adapter_api.get_bot_info_by_platform(
                adapter_instance.platform
            )
            print(f"Bot 信息: {bot_info}")

        stopped = await adapter_api.stop_adapter(first_signature)
        print(f"停止适配器结果: {stopped}")


if __name__ == "__main__":
    asyncio.run(main())
