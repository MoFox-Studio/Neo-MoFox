"""
agent_api 示例脚本

展示 Agent API 的查询、Schema 获取与执行能力。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import agent_api
from src.app.plugin_system.types import ChatType
from src.core.components.loader import load_all_plugins
from src.core.config import get_core_config, init_core_config
from src.core.managers import get_plugin_manager
from src.core.utils.schema_sync import enforce_database_schema_consistency
from src.kernel.db import init_database_from_config


async def main() -> None:
    """演示 agent_api 的基础功能。"""
    # 初始化配置
    init_core_config(str(REPO_ROOT / "config" / "core.toml"))

    # 初始化数据库
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

    # 加载所有插件
    await load_all_plugins(str(REPO_ROOT / "plugins"))

    print("=" * 60)
    print("Agent API 示例演示")
    print("=" * 60)

    # 1. 获取所有 Agent
    agents = agent_api.get_all_agents()
    print(f"\n1. 已注册 Agent 数量: {len(agents)}")

    if not agents:
        print("   未发现 Agent，跳过后续演示")
        return

    # 显示所有 Agent 签名
    for signature in agents.keys():
        print(f"   - {signature}")

    first_signature = next(iter(agents.keys()))
    print(f"\n2. 首个 Agent 签名: {first_signature}")

    # 2. 获取 Agent Schema
    schema = agent_api.get_agent_schema(first_signature)
    print("\n3. Agent Schema:")
    if schema:
        func_schema = schema.get("function", {})
        print(f"   - 名称: {func_schema.get('name')}")
        print(f"   - 描述: {func_schema.get('description')}")
        print(f"   - 参数: {func_schema.get('parameters', {}).get('properties', {}).keys()}")

    # 3. 获取特定聊天类型的 Agent
    agents_for_chat = agent_api.get_agents_for_chat(chat_type=ChatType.PRIVATE)
    print(f"\n4. 私聊可用 Agent 数量: {len(agents_for_chat)}")

    # 4. 获取 Agent Schemas
    schemas_for_chat = agent_api.get_agent_schemas(chat_type=ChatType.PRIVATE)
    print(f"\n5. 私聊 Agent Schema 数量: {len(schemas_for_chat)}")

    # 5. 按插件获取 Agent
    plugin_name = first_signature.split(":")[0]
    plugin_agents = agent_api.get_agents_for_plugin(plugin_name)
    print(f"\n6. 插件 '{plugin_name}' 的 Agent 数量: {len(plugin_agents)}")

    # 6. 获取 Agent 的专属 usables
    usables = agent_api.get_agent_usables(first_signature)
    print(f"\n7. Agent '{first_signature}' 的专属 usables 数量: {len(usables)}")
    if usables:
        print("   专属 usables:")
        for usable_cls in usables:
            usable_name = getattr(usable_cls, "tool_name", None) or getattr(
                usable_cls, "action_name", None
            ) or getattr(usable_cls, "agent_name", "unknown")
            print(f"   - {usable_name}")

    # 7. 获取 Agent 专属 usables 的 Schema
    usable_schemas = agent_api.get_agent_usable_schemas(first_signature)
    print(f"\n8. Agent 专属 usables Schema 数量: {len(usable_schemas)}")

    # 8. 执行 Agent（仅演示，需要根据实际 Agent 的参数调整）
    plugin = get_plugin_manager().get_plugin(plugin_name)
    if not plugin:
        print(f"\n9. 未找到插件实例: {plugin_name}，跳过执行演示")
        return

    stream_id = "demo_agent_stream"

    # 9. 检查 Agent 需要的参数
    required_params = []
    if schema:
        params = schema.get("function", {}).get("parameters", {})
        required_params = params.get("required", [])

    if required_params:
        print(f"\n9. Agent 需要的参数: {', '.join(required_params)}")
        print("   提示: 实际执行需要根据具体 Agent 提供正确的参数")

        # 示例：尝试执行（如果没有必需参数或可以提供默认值）
        try:
            # 这里只是演示，实际使用时需要根据 Agent 的具体要求传入参数
            if len(required_params) == 1 and "task" in required_params:
                print(f"\n   尝试执行 Agent: {first_signature}")
                success, result = await agent_api.execute_agent(
                    signature=first_signature,
                    plugin=plugin,
                    stream_id=stream_id,
                    task="这是一个测试任务",
                )
                print(f"   执行结果: {'成功' if success else '失败'}")
                print(f"   返回: {result}")
            else:
                print("   跳过实际执行（请根据 Agent 参数手动调整）")

        except Exception as e:
            print(f"   执行出错: {e}")

    # 10. 执行 Agent 的专属 usable（示例）
    if usable_schemas:
        first_usable = usable_schemas[0]
        usable_name = first_usable.get("function", {}).get("name", "").replace(
            "tool-", ""
        ).replace("action-", "").replace("agent-", "")

        if usable_name:
            print(f"\n10. 尝试执行 Agent 专属 usable: {usable_name}")
            try:
                # 示例执行，实际需要根据 usable 的参数要求调整
                usable_params = (
                    first_usable.get("function", {}).get("parameters", {})
                )
                usable_required = usable_params.get("required", [])

                if not usable_required or (
                    len(usable_required) == 1 and "query" in usable_required
                ):
                    success, result = await agent_api.execute_agent_usable(
                        signature=first_signature,
                        plugin=plugin,
                        stream_id=stream_id,
                        usable_name=usable_name,
                        query="测试查询",
                    )
                    print(f"    执行结果: {'成功' if success else '失败'}")
                    print(f"    返回: {result}")
                else:
                    print(f"    跳过执行（需要参数: {usable_required}）")

            except Exception as e:
                print(f"    执行出错: {e}")

    print("\n" + "=" * 60)
    print("Agent API 示例演示完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
