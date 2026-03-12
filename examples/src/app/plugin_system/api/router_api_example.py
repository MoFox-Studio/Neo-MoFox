"""
router_api 示例脚本

展示路由 API 的查询与挂载能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import plugin_api, router_api
from src.core.components.loader import load_all_plugins
from src.core.config import init_core_config


async def main() -> None:
    """演示 router_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    await load_all_plugins(str(REPO_ROOT / "plugins"))

    routers = router_api.get_all_routers()
    print(f"已注册 Router 数量: {len(routers)}")

    info_list = router_api.get_all_router_info()
    print(f"Router 信息数量: {len(info_list)}")

    if not routers:
        return

    first_signature = next(iter(routers.keys()))
    plugin_name = first_signature.split(":")[0]
    plugin = plugin_api.get_plugin(plugin_name)
    if not plugin:
        print(f"未找到插件实例: {plugin_name}")
        return

    router = await router_api.mount_router(first_signature, plugin)
    print(f"挂载 Router: {router.router_name}")

    await router_api.unmount_router(first_signature)
    print("已卸载 Router")


if __name__ == "__main__":
    asyncio.run(main())
