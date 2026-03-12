"""
permission_api 示例脚本

展示权限 API 的用户权限与命令覆盖管理能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import permission_api
from src.core.components.types import PermissionLevel
from src.core.config import get_core_config, init_core_config
from src.core.utils.schema_sync import enforce_database_schema_consistency
from src.kernel.db import init_database_from_config


async def main() -> None:
    """演示 permission_api 的基础功能。"""
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

    person_id = permission_api.generate_person_id("demo", "user_1")
    level = await permission_api.get_user_permission_level(person_id)
    print(f"初始权限级别: {level}")

    updated = await permission_api.set_user_permission_group(
        person_id=person_id,
        level=PermissionLevel.OPERATOR,
        reason="demo update",
    )
    print(f"设置权限组结果: {updated}")

    overrides = await permission_api.get_user_command_overrides(person_id)
    print(f"命令权限覆盖数量: {len(overrides)}")

    removed = await permission_api.remove_user_permission_group(person_id)
    print(f"移除权限组结果: {removed}")


if __name__ == "__main__":
    asyncio.run(main())
