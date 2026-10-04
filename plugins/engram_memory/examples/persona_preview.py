"""使用完整正式资料与请求级超时执行只读人物印象预览。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import tempfile
import time
import tomllib
from collections import Counter
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from sqlalchemy import URL, inspect, text

from src.app.plugin_system.api import llm_api, person_api
from src.app.plugin_system.types import ROLE, Text
from src.core.config import core_config, model_config
from src.kernel.db import close_engine, configure_engine, get_engine
from src.kernel.llm import LLMRequest, ModelEntry, ModelSet
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.model_client.shared import build_httpx_timeout

from ..config import EngramMemoryConfig
from ..vnext.persona_service import (
    MEMORY_FOOTNOTE_SEPARATOR,
    MEMORY_REFERENCE,
    PersonaService,
    _format_memory_footnotes,
    _inline_memory_references,
)
from ..vnext.schema import SCHEMA_KEY, SCHEMA_VERSION, VNextSchema
from ..vnext.schema_migration import _table_hash, migrate_snapshot

ROOT = Path(__file__).resolve().parents[3]
PREVIEW_TIMEOUT = 200.0
CONFIG_PATHS = ("core.toml", "model.toml", "plugins/engram_memory/config.toml")


def read_config(path: str) -> dict[str, Any]:
    """只读解析配置，避免配置初始化器回写文件。"""
    with (ROOT / "config" / path).open("rb") as handle:
        return tomllib.load(handle)


def table_fingerprints(source: Path) -> dict[str, str]:
    """在一致只读事务内计算正式数据库全部表的摘要。"""
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            )
        ]
        return {table: _table_hash(connection, table) for table in tables}


def copy_source(source: Path, target: Path) -> dict[str, str]:
    """从正式库只读 WAL 一致视图备份至临时路径。"""
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            )
        ]
        original = {table: _table_hash(connection, table) for table in tables}
        with closing(sqlite3.connect(target)) as destination:
            connection.backup(destination)
        return original


def mapped_columns(person: Any) -> dict[str, Any]:
    """提取人物全部映射列值，不比较 ORM 实例身份。"""
    return {
        column.key: getattr(person, column.key)
        for column in inspect(type(person)).column_attrs
    }


def verify_timeout(model: ModelEntry) -> None:
    """核对单次请求总时限与 HTTP 读写时限。"""
    if model["timeout"] != PREVIEW_TIMEOUT:
        raise ValueError("预览请求未使用指定超时")
    timeout = build_httpx_timeout(model["timeout"])
    if timeout.read != PREVIEW_TIMEOUT or timeout.write != PREVIEW_TIMEOUT:
        raise ValueError("HTTP 读写超时未使用指定值")
    if timeout.connect != 10.0 or timeout.pool != 5.0:
        raise ValueError("HTTP 连接或连接池时限偏离原策略")


def verify_provider_payload(payloads: list[Any], state: dict[str, Any]) -> None:
    """核对当前生成器的提示词、完整人设与全部原始资料。"""
    if [payload.role for payload in payloads] != [ROLE.SYSTEM, ROLE.USER]:
        raise ValueError("人物生成混入了非预期上下文")
    if any(
        not isinstance(part, Text) for payload in payloads for part in payload.content
    ):
        raise ValueError("人物生成包含非文本内容")
    system = "".join(part.text for part in payloads[0].content)
    user = "".join(part.text for part in payloads[1].content)
    if system != state["system_prompt"]:
        raise ValueError("模型发送边界的提示词与当前生成器不同")
    if json.dumps(state["personality"], ensure_ascii=False) not in system:
        raise ValueError("人物生成未传入完整人设")
    if json.loads(user) != json.loads(json.dumps(state["material"])):
        raise ValueError("模型输入与完整原始资料不同")


async def preview(account: str, *, check_only: bool) -> None:
    """读取正式资料并在临时 Schema 副本上调用当前生成器。"""
    config_before = {
        path: (ROOT / "config" / path).read_bytes() for path in CONFIG_PATHS
    }
    core_data = read_config("core.toml")
    model_data = read_config("model.toml")
    plugin_data = read_config("plugins/engram_memory/config.toml")
    core = core_config.CoreConfig.model_validate(core_data)
    models = model_config.ModelConfig.model_validate(model_data)
    plugin = EngramMemoryConfig.from_dict(plugin_data)
    database = core_data["database"]
    if database["database_type"] != "postgresql":
        raise ValueError("正式人物库不属于预期的 PostgreSQL 数据库")
    source = Path(plugin.storage.vnext_db_path)
    source = (source if source.is_absolute() else ROOT / source).resolve(strict=True)
    if not source.is_file():
        raise ValueError("正式记忆数据库不存在")
    url = URL.create(
        "postgresql+asyncpg",
        username=database["postgresql_user"],
        password=database["postgresql_password"],
        host=database["postgresql_host"],
        port=database["postgresql_port"],
        database=database["postgresql_database"],
    )
    configure_engine(
        url.render_as_string(hide_password=False),
        db_type="postgresql",
        apply_optimizations=False,
        engine_kwargs={
            "connect_args": {
                "server_settings": {
                    "default_transaction_read_only": "on",
                    "search_path": database["postgresql_schema"],
                },
                "ssl": database["postgresql_ssl_mode"],
                "timeout": database["connection_timeout"],
            }
        },
    )
    with tempfile.TemporaryDirectory(prefix="engram-persona-preview-") as directory:
        print("TEMP_DIRECTORY " + directory, flush=True)
        snapshot = Path(directory) / "source.db"
        candidate = Path(directory) / "schema.db"
        before = copy_source(source, snapshot)
        with closing(sqlite3.connect(snapshot)) as connection:
            schema_version = connection.execute(
                "SELECT version FROM engram_vnext_schema_version WHERE schema_key=?",
                (SCHEMA_KEY,),
            ).fetchone()[0]
        if schema_version in {3, 4}:
            if migrate_snapshot(snapshot, candidate)["source_unchanged"] is not True:
                raise ValueError("隔离副本迁移时来源发生变化")
        elif schema_version == SCHEMA_VERSION:
            candidate = snapshot
        else:
            raise ValueError("正式记忆库版本不符合预览条件")
        schema = VNextSchema(str(candidate))
        try:
            with (
                patch.object(core_config, "_global_config", core),
                patch.object(model_config, "_global_model_config", models),
                patch(
                    "src.kernel.db.core.engine.record_db_lifecycle_event",
                    new=AsyncMock(),
                ),
                patch.object(
                    person_api,
                    "update_user_impression",
                    new=AsyncMock(side_effect=RuntimeError("禁止写入正式印象")),
                ),
            ):
                core_config._inject_kernel_llm_policy(core)
                async with (await get_engine()).connect() as connection:
                    if (
                        await connection.scalar(text("SHOW transaction_read_only"))
                        != "on"
                    ):
                        raise ValueError("核心数据库连接并非只读")
                await schema.initialize()
                service = PersonaService(schema, persona_config=plugin.vnext.persona)
                person = await service.get_core_person(f"qq:{account}")
                if (
                    person is None
                    or person.platform != "qq"
                    or person.user_id != account
                ):
                    raise ValueError("未找到平台账号对应的精确核心人物")
                original_columns = mapped_columns(person)
                try:
                    aliases = await service._repository.resolve_person_aliases(
                        person.person_id
                    )
                    memories = await service._load_active_memories(aliases)
                    if not memories:
                        raise ValueError("人物没有关联的有效记忆")
                    trusted = await service.is_current_impression(
                        person.person_id, person.impression or ""
                    )
                    recent_chat = await service._load_recent_chat(person, aliases)
                    material = {
                        "person_id": person.person_id,
                        "current_impression": _inline_memory_references(
                            person.impression or ""
                        )
                        if trusted
                        else "",
                        "active_memories": memories,
                        "changes": (),
                        "recent_chat": recent_chat,
                    }
                    state: dict[str, Any] = {
                        "personality": core.personality.model_dump(mode="json"),
                        "material": material,
                        "provider_attempts": 0,
                    }
                    roles = Counter(
                        message["role"]
                        for block in recent_chat
                        for message in cast(list[dict[str, Any]], block["messages"])
                    )
                    print(
                        "MATERIAL "
                        + json.dumps(
                            {
                                "active_memories": len(memories),
                                "recent_blocks": len(recent_chat),
                                "recent_messages": sum(roles.values()),
                                "roles": dict(roles),
                                "personality_fields": len(state["personality"]),
                                "trusted_old_impression": trusted,
                                "core_read_only": True,
                                "memory_read_only": True,
                            }
                        ),
                        flush=True,
                    )
                    original_request = llm_api.create_llm_request
                    original_client = ModelClientRegistry.get_client_for_model
                    original_send = LLMRequest.send

                    def create_request(
                        model_set: ModelSet, **keywords: Any
                    ) -> LLMRequest:
                        """仅复制预览请求模型配置并关闭统计写入。"""
                        if (
                            keywords.get("request_name")
                            != "engram_vnext_persona_update"
                        ):
                            raise ValueError("超时覆盖仅适用于指定人物预览请求")
                        if "with_reminder" in keywords or "stream_id" in keywords:
                            raise ValueError("内部生成不能附加聊天提醒")
                        isolated = deepcopy(model_set)
                        for original, copied in zip(model_set, isolated, strict=True):
                            copied["timeout"] = PREVIEW_TIMEOUT
                            verify_timeout(copied)
                            if {
                                key: value
                                for key, value in copied.items()
                                if key != "timeout"
                            } != {
                                key: value
                                for key, value in original.items()
                                if key != "timeout"
                            }:
                                raise ValueError("预览修改了超时以外的模型配置")
                        request = original_request(isolated, **keywords)
                        request.enable_metrics = False
                        print(
                            "TIMEOUT_PASS "
                            + json.dumps(
                                {
                                    "configured_seconds": [
                                        model["timeout"] for model in model_set
                                    ],
                                    "preview_seconds": PREVIEW_TIMEOUT,
                                    "max_retry": [
                                        model["max_retry"]
                                        for model in request.model_set
                                    ],
                                }
                            ),
                            flush=True,
                        )
                        return request

                    def checked_client(
                        registry: ModelClientRegistry, model: ModelEntry
                    ) -> Any:
                        """检查真实客户端发送边界，保留其调用和返回值。"""
                        verify_timeout(model)
                        client = original_client(registry, model)

                        class CheckedClient:
                            """原生客户端的输入核对包装。"""

                            async def create(self, **keywords: Any) -> tuple[Any, ...]:
                                """验证资料与超时后执行原客户端请求。"""
                                verify_provider_payload(keywords["payloads"], state)
                                verify_timeout(keywords["model_set"])
                                if check_only:
                                    raise RuntimeError("检查模式禁止模型网络请求")
                                state["provider_attempts"] += 1
                                print(
                                    f"PROVIDER_INPUT_PASS model={keywords['model_name']} attempt={state['provider_attempts']} timeout={keywords['model_set']['timeout']} current_prompt=true",
                                    flush=True,
                                )
                                result = await client.create(**keywords)
                                state["usage"] = result[4] if len(result) == 5 else None
                                return result

                        return CheckedClient()

                    async def checked_send(request: LLMRequest, *, stream: bool) -> Any:
                        """保存当前生成器输入并执行预检或原始请求。"""
                        if stream or not request.model_set:
                            raise ValueError("预览应包含实际模型并使用非流式生成")
                        state["system_prompt"] = "".join(
                            part.text
                            for payload in request.payloads
                            if payload.role == ROLE.SYSTEM
                            for part in payload.content
                            if isinstance(part, Text)
                        )
                        verify_provider_payload(request.payloads, state)
                        if not check_only:
                            return await original_send(request, stream=stream)
                        context_manager = request.context_manager
                        if context_manager is None:
                            raise ValueError("预检缺少上下文管理器")
                        for model in request.model_set:
                            verify_timeout(model)
                            prepared = await context_manager.prepare_payloads_for_model(
                                request.payloads,
                                model,
                                request=request,
                            )
                            verify_provider_payload(prepared, state)
                        response: asyncio.Future[str] = (
                            asyncio.get_running_loop().create_future()
                        )
                        response.set_result(
                            '{"impression_text":"","reason":"check-only"}'
                        )
                        return response

                    payload = json.dumps(material, ensure_ascii=False)
                    with (
                        patch.object(llm_api, "create_llm_request", create_request),
                        patch.object(
                            ModelClientRegistry, "get_client_for_model", checked_client
                        ),
                        patch.object(LLMRequest, "send", checked_send),
                    ):
                        if check_only:
                            await service._generate(payload)
                            print(
                                "INPUT_CHECK_PASS current_prompt=true complete_material=true timeout=200 no_network=true",
                                flush=True,
                            )
                        else:
                            started = time.perf_counter()
                            result = await service._generate(payload)
                            reason = service._required_text(result, "reason").strip()
                            raw = service._required_text(
                                result, "impression_text", allow_empty=True
                            ).strip()
                            inline = _inline_memory_references(raw)
                            used_ids = tuple(
                                dict.fromkeys(MEMORY_REFERENCE.findall(inline))
                            )
                            active_ids = {
                                str(memory["memory_id"]) for memory in memories
                            }
                            if (
                                not inline
                                or not used_ids
                                or any(
                                    identifier not in active_ids
                                    for identifier in used_ids
                                )
                            ):
                                print(
                                    "INVALID_PREVIEW_BEGIN\n"
                                    + raw
                                    + "\nINVALID_PREVIEW_END",
                                    flush=True,
                                )
                                raise ValueError("模型结果未通过原服务的有效引用校验")
                            formatted = _format_memory_footnotes(inline)
                            if _inline_memory_references(formatted) != inline:
                                raise ValueError("引用排版不能无损展开")
                            body, separator, footnotes = formatted.partition(
                                MEMORY_FOOTNOTE_SEPARATOR
                            )
                            print(
                                "RESULT "
                                + json.dumps(
                                    {
                                        "seconds": round(
                                            time.perf_counter() - started, 2
                                        ),
                                        "provider_attempts": state["provider_attempts"],
                                        "timeout": PREVIEW_TIMEOUT,
                                        "valid_memory_ids": len(used_ids),
                                        "body_characters": len(body),
                                        "footnotes": len(footnotes.splitlines())
                                        if separator
                                        else 0,
                                        "usage": state.get("usage"),
                                        "reason": reason,
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                            print(
                                "PREVIEW_BEGIN\n" + formatted + "\nPREVIEW_END",
                                flush=True,
                            )
                finally:
                    current = await service.get_core_person(f"qq:{account}")
                    current_columns = (
                        mapped_columns(current) if current is not None else None
                    )
                    core_unchanged = current_columns == original_columns
                    memory_unchanged = table_fingerprints(source) == before
                    config_unchanged = all(
                        (ROOT / "config" / path).read_bytes() == value
                        for path, value in config_before.items()
                    )
                    print(
                        f"SOURCE_CHECK core_unchanged={core_unchanged} memory_unchanged={memory_unchanged} config_unchanged={config_unchanged}",
                        flush=True,
                    )
                    if (
                        not core_unchanged
                        or not memory_unchanged
                        or not config_unchanged
                    ):
                        raise ValueError("正式人物、记忆数据库或配置发生变化")
                    print(
                        "UNCHANGED_PASS core_person=true memory_tables=true configs=true",
                        flush=True,
                    )
        finally:
            await schema.close()
            await close_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    parser.add_argument("--check-only", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(preview(arguments.account, check_only=arguments.check_only))
