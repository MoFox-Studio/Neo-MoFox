"""
plugin_api 示例脚本

展示插件 API 的查询能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import plugin_api
from src.core.components.loader import load_all_plugins
from src.core.config import init_core_config


async def main() -> None:
    """演示 plugin_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    await load_all_plugins(str(REPO_ROOT / "plugins"))

    plugins = plugin_api.get_all_plugins()
    print(f"已加载插件数量: {len(plugins)}")

    names = plugin_api.list_loaded_plugins()
    print(f"插件列表: {names}")

    if not names:
        return

    first_plugin = names[0]
    plugin = plugin_api.get_plugin(first_plugin)
    manifest = plugin_api.get_manifest(first_plugin)
    print(f"首个插件实例: {plugin}")
    print(f"首个插件清单: {manifest}")
    print(f"是否已加载: {plugin_api.is_plugin_loaded(first_plugin)}")


if __name__ == "__main__":
    asyncio.run(main())
