"""
service_api 示例脚本

展示服务 API 的查询与实例创建能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import service_api
from src.core.components.loader import load_all_plugins
from src.core.config import init_core_config


async def main() -> None:
    """演示 service_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    await load_all_plugins(str(REPO_ROOT / "plugins"))

    services = service_api.get_all_services()
    print(f"已注册 Service 数量: {len(services)}")

    if not services:
        return

    first_signature = next(iter(services.keys()))
    service_cls = service_api.get_service_class(first_signature)
    service_instance = service_api.get_service(first_signature)
    print(f"首个 Service 签名: {first_signature}")
    print(f"是否找到 Service 类: {service_cls is not None}")
    print(f"是否创建 Service 实例: {service_instance is not None}")


if __name__ == "__main__":
    asyncio.run(main())
