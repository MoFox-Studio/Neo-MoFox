"""Booku Memory 命令工具。

对外仅暴露一个工具：memory_command(command)。
通过命令行风格参数执行 help/search/read/create/update/delete，支持 && 串联。
"""

from __future__ import annotations

import shlex
from typing import Annotated, Any

from src.app.plugin_system.api import log_api
from src.core.components import BaseTool

from ..manual import BOOKU_MEMORY_COMMAND_MANUAL
from ..service import BookuMemoryService

logger = log_api.get_logger("booku_memory.command_tool")


def _service(plugin: Any) -> BookuMemoryService:
    """构建并返回绑定到指定插件实例的记忆服务对象。"""

    return BookuMemoryService(plugin=plugin)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    """将字符串解析为布尔值。"""

    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: str | None, default: int) -> int:
    """将字符串解析为整数，失败时返回默认值。"""

    if value is None:
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _parse_float(value: str | None, default: float = 0.0) -> float:
    """将字符串解析为浮点数，失败时返回默认值。"""

    if value is None:
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def _parse_csv_list(value: str | None) -> list[str]:
    """将逗号分隔字符串解析为字符串列表。"""

    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _split_segments(command: str) -> list[str]:
    """按 && 拆分命令段。"""

    return [part.strip() for part in command.split("&&") if part.strip()]


def _parse_segment(segment: str) -> tuple[str, dict[str, list[str]]]:
    """解析单条命令段为操作名和参数表。"""

    tokens = shlex.split(segment)
    if not tokens:
        raise ValueError("空命令段")

    operation = tokens[0].strip().lower()
    options: dict[str, list[str]] = {}

    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            index += 1
            continue

        key = token.lstrip("-").strip().lower()
        if not key:
            index += 1
            continue

        value: str
        if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
            value = tokens[index + 1]
            index += 2
        else:
            value = "true"
            index += 1

        options.setdefault(key, []).append(value)

    return operation, options


def _pick_option(options: dict[str, list[str]], *keys: str) -> str | None:
    """按候选键顺序读取单值参数。"""

    for key in keys:
        values = options.get(key, [])
        if values:
            return values[-1]
    return None


def _pick_ids(options: dict[str, list[str]]) -> list[str]:
    """读取并合并 id 参数。"""

    values: list[str] = []
    for key in ("id", "ids", "memory_id", "memory_ids"):
        for raw in options.get(key, []):
            values.extend(_parse_csv_list(raw))
    dedup: list[str] = []
    for value in values:
        if value not in dedup:
            dedup.append(value)
    return dedup


def _pick_tag_triplet(
    options: dict[str, list[str]],
    *,
    required: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """读取并严格校验三元标签组。"""

    tag_option_keys = (
        "core_tags",
        "core",
        "diffusion_tags",
        "diffusion",
        "opposing_tags",
        "opposing",
        "triple_tags",
        "triplet",
    )
    explicit_tag_input = any(key in options for key in tag_option_keys)

    core_tags = _parse_csv_list(_pick_option(options, "core_tags", "core"))
    diffusion_tags = _parse_csv_list(_pick_option(options, "diffusion_tags", "diffusion"))
    opposing_tags = _parse_csv_list(_pick_option(options, "opposing_tags", "opposing"))

    triplet = _pick_option(options, "triple_tags", "triplet")
    if triplet:
        # 语法：core1,core2|diff1,diff2|opp1,opp2
        parts = [part.strip() for part in triplet.split("|")]
        if len(parts) != 3:
            raise ValueError(
                "tag 三元组必须同时提供有效的 -core_tags、-diffusion_tags、-opposing_tags；"
                '或使用 -triple_tags "核心|扩散|对立" 一次性传入三组。'
            )
        if len(parts) >= 1 and not core_tags:
            core_tags = _parse_csv_list(parts[0])
        if len(parts) >= 2 and not diffusion_tags:
            diffusion_tags = _parse_csv_list(parts[1])
        if len(parts) >= 3 and not opposing_tags:
            opposing_tags = _parse_csv_list(parts[2])

    has_all_groups = bool(core_tags) and bool(diffusion_tags) and bool(opposing_tags)
    if required and not has_all_groups:
        raise ValueError(
            "必须同时提供有效的 -core_tags、-diffusion_tags、-opposing_tags 三组标签；"
            '也可使用 -triple_tags "核心|扩散|对立"，且三组都不能为空。'
        )

    if explicit_tag_input and not has_all_groups:
        raise ValueError(
            "tag 参数一旦出现，就必须同时提供有效的 -core_tags、-diffusion_tags、-opposing_tags 三组；"
            "禁止只传一组或两组。"
        )

    return core_tags, diffusion_tags, opposing_tags


class BookuMemoryCommandTool(BaseTool):
    """统一命令工具：memory_command。"""

    tool_name: str = "memory_command"
    tool_description: str = (
        "Booku Memory 命令工具。"
        "支持 help/search/read/create/update/delete。"
        "使用该工具应当非常频繁，多记多读。"

    )

    async def execute(
        self,
        command: Annotated[
            str,
            "Booku Memory 命令字符串。支持 help/search/read/create/update/delete 和 && 串联；"
            "不确定格式时先执行 help。",
        ],
    ) -> tuple[bool, str | dict]:
        """执行命令字符串。"""

        raw_command = (command or "").strip()
        if not raw_command:
            return True, self._build_command_result(
                ok=False,
                executions=[
                    {
                        "command": "",
                        "success": False,
                        "error": "command 不能为空",
                    }
                ],
            )

        segments = _split_segments(raw_command)
        if not segments:
            return True, self._build_command_result(
                ok=False,
                executions=[
                    {
                        "command": raw_command,
                        "success": False,
                        "error": "未解析到可执行命令",
                    }
                ],
            )

        executions: list[dict[str, Any]] = []
        service: BookuMemoryService | None = None

        for segment in segments:
            try:
                op, options = _parse_segment(segment)
                if op != "help" and service is None:
                    service = _service(self.plugin)
                result = await self._execute_single(service=service, operation=op, options=options)
                executions.append({"command": segment, "success": True, "result": result})
            except Exception as error:  # noqa: BLE001
                logger.error(f"memory_command 执行失败: {error}", exc_info=True)
                executions.append({"command": segment, "success": False, "error": str(error)})
                return True, self._build_command_result(ok=False, executions=executions)

        return True, self._build_command_result(ok=True, executions=executions)

    @staticmethod
    def _build_command_result(*, ok: bool, executions: list[dict[str, Any]]) -> dict[str, Any]:
        """构造 memory_command 的统一结果对象。"""

        return {
            "action": "memory_command",
            "ok": ok,
            "executed": len(executions),
            "results": executions,
        }

    async def _execute_single(
        self,
        *,
        service: BookuMemoryService | None,
        operation: str,
        options: dict[str, list[str]],
    ) -> dict[str, Any]:
        """执行单条命令。"""

        if operation == "help":
            return {
                "action": "help",
                "content": BOOKU_MEMORY_COMMAND_MANUAL,
            }

        if service is None:
            raise ValueError("记忆服务不可用")

        if operation == "search":
            top_n = _parse_int(_pick_option(options, "topn", "top_n", "n"), 10)
            core_tags, diffusion_tags, opposing_tags = _pick_tag_triplet(options)
            return await service.search_memory_entries(
                top_n=top_n,
                query_text=_pick_option(options, "query", "q"),
                memory_type=_pick_option(options, "type", "memory_type"),
                status=_pick_option(options, "status"),
                person_id=_pick_option(options, "person_id"),
                relation_of=_pick_option(options, "relation_of", "related_to"),
                include_archived=_parse_bool(_pick_option(options, "include_archived"), False),
                include_knowledge=_parse_bool(_pick_option(options, "include_knowledge"), True),
                include_related=_parse_bool(_pick_option(options, "include_related", "related"), False),
                core_tags=core_tags,
                diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
            )

        if operation == "read":
            ids = _pick_ids(options)
            if not ids:
                raise ValueError("read 命令需要提供 -id 或 -ids")
            return await service.read_full_content(memory_ids=ids)

        if operation == "create":
            title = (_pick_option(options, "title") or "").strip()
            content = (_pick_option(options, "content", "body") or "").strip()
            if not title:
                raise ValueError("create 命令缺少 -title")
            if not content:
                raise ValueError("create 命令缺少 -content")

            memory_type = (_pick_option(options, "type", "memory_type") or "knowledge").strip().lower()
            status = (_pick_option(options, "status") or "active").strip().lower()
            person_id = (_pick_option(options, "person_id") or "").strip() or None
            if memory_type == "person" and not person_id:
                raise ValueError("人物记忆必须提供 -person_id，格式为 platform:id")

            core_tags, diffusion_tags, opposing_tags = _pick_tag_triplet(
                options,
                required=True,
            )
            relation_memory_ids = _parse_csv_list(_pick_option(options, "relation_ids", "relation_memory_ids"))
            relation_aliases = _parse_csv_list(_pick_option(options, "relation_aliases"))
            related_people = _parse_csv_list(_pick_option(options, "related_people"))

            bucket = "archived" if status in {"archived", "expired"} else "emergent"
            return await service.create_memory(
                title=title,
                content=content,
                bucket=bucket,
                core_tags=core_tags,
                diffusion_tags=diffusion_tags,
                opposing_tags=opposing_tags,
                memory_type=memory_type,
                status=status,
                person_id=person_id,
                relation_memory_ids=relation_memory_ids,
                relation_aliases=relation_aliases,
                event_start_at=_parse_float(_pick_option(options, "event_start_at", "start_at"), 0.0),
                event_end_at=_parse_float(_pick_option(options, "event_end_at", "end_at"), 0.0),
                related_people=related_people,
                knowledge_type=(_pick_option(options, "knowledge_type") or "").strip().lower(),
                address_or_coord=(_pick_option(options, "address_or_coord", "address") or "").strip(),
                place_type=(_pick_option(options, "place_type") or "").strip().lower(),
                asset_type=(_pick_option(options, "asset_type") or "").strip().lower(),
                disposition_status=(_pick_option(options, "disposition_status") or "").strip().lower(),
                procedure_type=(_pick_option(options, "procedure_type") or "").strip().lower(),
            )

        if operation == "update":
            memory_id = (_pick_option(options, "id", "memory_id") or "").strip()
            if not memory_id:
                raise ValueError("update 命令需要 -id")

            core_tags, diffusion_tags, opposing_tags = _pick_tag_triplet(options)
            result = await service.update_memory_by_id(
                memory_id=memory_id,
                title=_pick_option(options, "title"),
                content=_pick_option(options, "content", "body"),
                core_tags=core_tags or None,
                diffusion_tags=diffusion_tags or None,
                opposing_tags=opposing_tags or None,
                memory_type=_pick_option(options, "type", "memory_type"),
                status=_pick_option(options, "status"),
                person_id=_pick_option(options, "person_id"),
                relation_memory_ids=_parse_csv_list(_pick_option(options, "relation_ids", "relation_memory_ids")) or None,
                relation_aliases=_parse_csv_list(_pick_option(options, "relation_aliases")) or None,
                event_start_at=_parse_float(_pick_option(options, "event_start_at", "start_at"), 0.0)
                if _pick_option(options, "event_start_at", "start_at") is not None
                else None,
                event_end_at=_parse_float(_pick_option(options, "event_end_at", "end_at"), 0.0)
                if _pick_option(options, "event_end_at", "end_at") is not None
                else None,
                related_people=_parse_csv_list(_pick_option(options, "related_people")) or None,
                knowledge_type=_pick_option(options, "knowledge_type"),
                address_or_coord=_pick_option(options, "address_or_coord", "address"),
                place_type=_pick_option(options, "place_type"),
                asset_type=_pick_option(options, "asset_type"),
                disposition_status=_pick_option(options, "disposition_status"),
                procedure_type=_pick_option(options, "procedure_type"),
            )
            return result

        if operation == "delete":
            ids = _pick_ids(options)
            if not ids:
                raise ValueError("delete 命令需要提供 -id 或 -ids")
            return await service.delete_memories(
                memory_ids=ids,
                hard=_parse_bool(_pick_option(options, "hard"), False),
            )

        raise ValueError(f"不支持的命令: {operation}，可先执行 help 查看支持的命令与参数说明")


class BookuTemporaryMemoTool(BaseTool):
    """短期临时备忘录工具。"""

    tool_name: str = "temporary_memo"
    tool_description: str = (
        "记录短期关键便签的临时备忘录工具。"
        "主要用来保存短期关键信息，并会自动过期。"
        "由于你看不到跨聊天流的消息，所以通常用于记录当前聊天流的关键信息，这在跨聊天流保持重要信息连续性时非常有用。"
        "例如当你在群里发表了某些观点，或者某人说了一些信息，你就应当使用这个工具记录下来，这会在你在其他聊天流里（例如私聊）保持连贯性起到非常重要的作用。"
        "要记录的信息包括：xx喜欢什么不喜欢什么，xx想要做什么，爱称昵称，xx的偏好爱好，xx做过什么，xx希望做什么不希望做什么，等等。"
    )

    async def execute(
        self,
        content: Annotated[
            str,
            "要记录的短期关键内容。注意这不是长期记忆，只用于临时便签提醒。",
        ],
        expire_hours: Annotated[
            float,
            "相对过期时间，单位小时，默认 2.0。到期后会自动失效。",
        ] = 2.0,
    ) -> tuple[bool, str | dict]:
        """写入或刷新一条短期临时备忘录。"""

        service = _service(self.plugin)
        try:
            result = await service.create_temporary_memo(
                content=content,
                expire_hours=expire_hours,
                stream_id=self.get_current_stream_id(),
            )
        except Exception as error:  # noqa: BLE001
            logger.error(f"temporary_memo 执行失败: {error}", exc_info=True)
            return True, {
                "action": "create_temporary_memo",
                "ok": False,
                "error": str(error),
            }

        return True, {
            "action": "create_temporary_memo",
            "ok": True,
            "result": result,
        }


__all__ = ["BookuMemoryCommandTool", "BookuTemporaryMemoTool"]
