"""
config_api 示例脚本

展示配置 API 的加载与查询能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import config_api
from src.core.components.loader import load_all_plugins
from src.core.config import init_core_config


async def main() -> None:
    """演示 config_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    await load_all_plugins(str(REPO_ROOT / "plugins"))
    config_api.initialize_all_configs()

    loaded_plugins = config_api.get_loaded_plugins()
    print(f"已加载配置的插件数量: {len(loaded_plugins)}")

    if not loaded_plugins:
        return

    first_plugin = loaded_plugins[0]
    config = config_api.get_config(first_plugin)
    print(f"首个插件配置实例: {config}")


if __name__ == "__main__":
    asyncio.run(main())
