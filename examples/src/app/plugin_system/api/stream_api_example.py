"""
stream_api 示例脚本

展示聊天流 API 的创建、消息写入与查询能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import stream_api
from src.app.plugin_system.types import ChatType, Message, MessageType
from src.core.config import get_core_config, init_core_config
from src.core.utils.schema_sync import enforce_database_schema_consistency
from src.kernel.db import init_database_from_config


async def main() -> None:
    """演示 stream_api 的基础功能。"""
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))
    db_cfg = get_core_config().database
    await init_database_from_config(
        database_type=db_cfg.database_type,
        sqlite_path=db_cfg.sqlite_path,
        postgresql_host=db_cfg.postgresql_host,
        postgresql_port=db_cfg.postgresql_port,
        postgresql_database=db_cfg.postgresql_database,
        postgresql_user=db_cfg.postgresql_user,
        postgresql_password=db_cfg.postgresql_password,
        postgresql_schema=db_cfg.postgresql_schema,
        postgresql_ssl_mode=db_cfg.postgresql_ssl_mode,
        postgresql_ssl_ca=db_cfg.postgresql_ssl_ca,
        postgresql_ssl_cert=db_cfg.postgresql_ssl_cert,
        postgresql_ssl_key=db_cfg.postgresql_ssl_key,
        connection_pool_size=db_cfg.connection_pool_size,
        connection_timeout=db_cfg.connection_timeout,
        echo=db_cfg.echo,
    )
    await enforce_database_schema_consistency()

    chat_stream = await stream_api.get_or_create_stream(
        platform="qq",
        user_id="demo_user",
        chat_type=ChatType.PRIVATE,
    )
    print(f"创建/获取聊天流: {chat_stream.stream_id}")

    message = Message(
        message_id="demo_message_1",
        content="hello stream",
        processed_plain_text="hello stream",
        message_type=MessageType.TEXT,
        sender_id="demo_user",
        sender_name="demo_user",
        platform="qq",
        chat_type=ChatType.PRIVATE.value,
        stream_id=chat_stream.stream_id,
    )
    await stream_api.add_message(message)

    messages = await stream_api.get_stream_messages(chat_stream.stream_id, limit=10)
    print(f"当前流消息数: {len(messages)}")

    info = await stream_api.get_stream_info(chat_stream.stream_id)
    print(f"流信息: {info}")

    deleted = await stream_api.delete_stream(chat_stream.stream_id, delete_messages=True)
    print(f"删除流结果: {deleted}")


if __name__ == "__main__":
    asyncio.run(main())
