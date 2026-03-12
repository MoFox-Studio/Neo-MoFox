"""
action_api 示例脚本

展示 Action API 的查询、Schema 获取与执行能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import action_api
from src.app.plugin_system.types import ChatType, Message
from src.core.components.loader import load_all_plugins
from src.core.config import get_core_config, init_core_config
from src.core.managers import get_plugin_manager
from src.core.utils.schema_sync import enforce_database_schema_consistency
from src.kernel.db import init_database_from_config


async def main() -> None:
    """演示 action_api 的基础功能。"""
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
    await load_all_plugins(str(REPO_ROOT / "plugins"))

    actions = action_api.get_all_actions()
    print(f"已注册 Action 数量: {len(actions)}")
    if not actions:
        print("未发现 Action，跳过后续演示")
        return

    first_signature = next(iter(actions.keys()))
    print(f"首个 Action 签名: {first_signature}")

    schema = action_api.get_action_schema(first_signature)
    print(f"Action schema: {schema}")

    actions_for_chat = action_api.get_actions_for_chat(chat_type=ChatType.PRIVATE)
    print(f"私聊可用 Action 数量: {len(actions_for_chat)}")

    schemas_for_chat = action_api.get_action_schemas(chat_type=ChatType.PRIVATE)
    print(f"私聊 Action schema 数量: {len(schemas_for_chat)}")

    stream_id = "demo_action_stream"
    available_actions = await action_api.modify_actions(
        stream_id=stream_id,
        message_content="hello",
    )
    print(f"根据上下文可用 Action 数量: {len(available_actions)}")

    plugin_name = first_signature.split(":")[0]
    plugin = get_plugin_manager().get_plugin(plugin_name)
    if not plugin:
        print(f"未找到插件实例: {plugin_name}")
        return

    message = Message(
        message_id="demo_action_message",
        content="hello",
        processed_plain_text="hello",
        sender_id="demo_user",
        sender_name="demo_user",
        platform="demo",
        chat_type=ChatType.PRIVATE.value,
        stream_id=stream_id,
    )

    required = []
    if schema:
        required = list(
            schema.get("function", {})
            .get("parameters", {})
            .get("required", [])
            or []
        )
    if required:
        print(f"Action 需要参数 {required}，跳过执行")
        return

    success, result = await action_api.execute_action(
        signature=first_signature,
        plugin=plugin,
        message=message,
    )
    print(f"Action 执行结果: success={success}, result={result}")


if __name__ == "__main__":
    asyncio.run(main())
