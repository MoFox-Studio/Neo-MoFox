"""
command_api 示例脚本

展示命令 API 的查询与匹配能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import command_api
from src.core.components.loader import load_all_plugins
from src.core.config import get_core_config, init_core_config
from src.core.utils.schema_sync import enforce_database_schema_consistency
from src.kernel.db import init_database_from_config


async def main() -> None:
    """演示 command_api 的基础功能。"""
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

    commands = command_api.get_all_commands()
    print(f"已注册 Command 数量: {len(commands)}")

    command_names = command_api.get_all_command_names()
    print(f"命令名称示例: {command_names[:5]}")

    if not commands:
        return

    first_signature = next(iter(commands.keys()))
    command_cls = command_api.get_command_class(first_signature)
    print(f"首个 Command 签名: {first_signature}")
    print(f"是否找到 Command 类: {command_cls is not None}")

    help_text = command_api.get_command_help(first_signature)
    print(f"Command 帮助: {help_text}")

    sample_text = command_names[0] if command_names else "/help"
    print(f"是否命令: {command_api.is_command(sample_text)}")
    path, _, args = command_api.match_command(sample_text)
    print(f"匹配命令结果: path={path}, args={args}")


if __name__ == "__main__":
    asyncio.run(main())
