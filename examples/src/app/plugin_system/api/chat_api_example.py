"""
chat_api 示例脚本

展示聊天 API 的基本查询与管理能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许从任意工作目录直接运行该示例文件
REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import chat_api
from src.app.plugin_system.types import ChatType
from src.core.components.loader import load_all_plugins
from src.core.config import init_core_config


async def main() -> None:
    """演示 chat_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    await load_all_plugins(str(REPO_ROOT / "plugins"))
    chatters = chat_api.get_all_chatters()
    print(f"已注册 Chatter 数量: {len(chatters)}")

    active = chat_api.get_active_chatters()
    print(f"活跃 Chatter 数量: {len(active)}")

    if chatters:
        first_signature = next(iter(chatters.keys()))
        chatter_cls = chat_api.get_chatter_class(first_signature)
        print(f"首个 Chatter 签名: {first_signature}")
        print(f"是否找到 Chatter 类: {chatter_cls is not None}")

    chatter = chat_api.get_or_create_chatter_for_stream(
        stream_id="demo_stream",
        chat_type=ChatType.PRIVATE,
        platform="qq",
    )
    print(f"自动绑定 Chatter 结果: {chatter is not None}")


if __name__ == "__main__":
    asyncio.run(main())
