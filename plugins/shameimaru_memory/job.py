"""Shameimaru Memory 周期任务。

三个事件：
- 摘要更新（SummaryJob）：按配置间隔从每个群聊的聊天流生成摘要并持久化。
- 新闻记录（NewsJob）：按配置间隔读取全部群聊摘要，整理总结性记忆条目。
- Dreaming 整理（DreamJob）：按配置间隔对每个群聊执行知识整理，将值得
  持久化的新闻知识化写入 booku_memory_store，并删除对应新闻。

人物层更新：新闻条目因达到上限被删除、或因被知识化而删除时，
逐人物增量更新本地持久化的人物背景信息表。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from src.app.plugin_system.api import log_api, service_api, stream_api
from src.core.models.message import Message

from .models import NewsEntry, PersonRef, SummaryEntry
from .prompts import (
    DREAM_PROMPT,
    DREAM_PROMPT_NAME,
    NEWS_PROMPT,
    NEWS_PROMPT_NAME,
    NO_MEANINGFUL_CONTENT_TOKEN,
    PERSONA_PROMPT,
    PERSONA_PROMPT_NAME,
    SUMMARY_PROMPT,
    SUMMARY_PROMPT_NAME,
)
from .store import ShameimaruMemoryStore, shared_store
from .sub_agent import call_sub_agent, extract_json_array, resolve_prompt
from .utils import format_local_time, message_time, person_id_of, person_name_of

logger = log_api.get_logger("shameimaru_memory.job")


def _get_config(plugin: Any) -> Any:
    """读取插件配置。"""
    return getattr(plugin, "config", None)


def _build_store(plugin: Any) -> ShameimaruMemoryStore:
    """获取插件级共享存储实例（所有周期任务共用一个 store，避免并发写文件互相覆盖）。"""
    return shared_store(plugin, lambda: _get_config(plugin))


def _llm_task(config: Any, attr: str) -> str:
    """读取插件配置中的模型任务名。"""
    try:
        return str(getattr(getattr(config, "llm", None), attr, "") or "actor")
    except Exception:  # noqa: BLE001
        return "actor"


def _collect_participants(messages: list[Message]) -> list[PersonRef]:
    """从消息列表提取去重后的人物引用（排除 bot 自身消息）。"""
    refs: list[PersonRef] = []
    seen: set[str] = set()
    for message in messages:
        if str(getattr(message, "sender_role", "") or "").lower() == "bot":
            continue
        person_id = person_id_of(message)
        if not person_id or person_id in seen:
            continue
        seen.add(person_id)
        refs.append(PersonRef(person_id=person_id, name=person_name_of(message)))
    return refs


def _build_chat_flow(messages: list[Message]) -> str:
    """将消息列表格式化为聊天记录文本（按时间先后）。

    排除 bot 自身消息，避免摘要/新闻以 Bot 为主角，导致人物信息被错误归属。
    """
    lines: list[str] = []
    for message in messages:
        if str(getattr(message, "sender_role", "") or "").lower() == "bot":
            continue
        text = str(getattr(message, "processed_plain_text", "") or "").strip()
        if not text:
            continue
        sender = person_name_of(message)
        clock = format_local_time(message_time(message))
        prefix = f"[{clock}] " if clock else ""
        lines.append(f"{prefix}{sender}: {text}")
    return "\n".join(lines)


def _parse_participants(raw: Any) -> list[PersonRef]:
    """从子 agent 返回的 participants 字段解析人物引用。"""
    refs: list[PersonRef] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return refs
    for item in raw:
        if not isinstance(item, dict):
            continue
        person_id = str(item.get("person_id") or "").strip()
        if not person_id or person_id in seen:
            continue
        seen.add(person_id)
        refs.append(PersonRef(person_id=person_id, name=str(item.get("name") or "")))
    return refs


def _resolve_participants(
    raw: Any,
    roster_by_id: dict[str, PersonRef],
    roster_by_name: dict[str, PersonRef],
) -> list[PersonRef]:
    """把子 agent 返回的 participants 映射回真实人物引用。

    子 agent 只能看到摘要正文中的人物名，无法获知真实 person_id，
    因此其返回的 person_id 可能是编造的。这里按「先 id 后 name」从
    摘要条目的真实参与人物清单（roster）中匹配：

    - 匹配成功：使用清单中的真实 person_id（name 保留子 agent 的写法）；
    - 匹配失败：视为编造的人物，直接丢弃。

    Args:
        raw: 子 agent 返回的 participants 原始字段。
        roster_by_id: person_id -> 真实人物引用。
        roster_by_name: 人物名 -> 真实人物引用。

    Returns:
        list[PersonRef]: 解析后的真实人物引用列表。
    """
    resolved: list[PersonRef] = []
    seen: set[str] = set()
    for parsed in _parse_participants(raw):
        real = roster_by_id.get(parsed.person_id) or roster_by_name.get(parsed.name)
        if real is None or real.person_id in seen:
            continue
        seen.add(real.person_id)
        resolved.append(
            PersonRef(person_id=real.person_id, name=parsed.name or real.name)
        )
    return resolved


# ----------------------------------------------------------------------
# 摘要层：摘要更新事件
# ----------------------------------------------------------------------


async def run_summary_job(plugin: Any) -> dict[str, Any]:
    """摘要更新事件：按摘要文件中出现的群聊依次生成摘要并持久化。

    只对摘要文件中已注册的群聊发起 LLM 调用（新群聊由回复前注入器
    在发生对话时注册），避免对数据库中全部（含长期不活跃）群聊调用。

    Args:
        plugin: 插件实例。

    Returns:
        dict[str, Any]: 统计信息。
    """
    config = _get_config(plugin)
    store = _build_store(plugin)
    summary_cfg = config.summary
    task = _llm_task(config, "summary_task")

    groups = await store.list_group_summaries()
    stats: dict[str, Any] = {"groups": len(groups), "summarized": 0, "skipped": 0}

    for group in groups:
        try:
            ok = await _summarize_group(store, group.stream_id, summary_cfg, task)
            stats["summarized" if ok else "skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(f"摘要生成失败 stream_id={group.stream_id}: {exc}")
            stats["skipped"] += 1

    logger.info(f"摘要更新完成: {stats}")
    return stats


async def _summarize_group(
    store: ShameimaruMemoryStore,
    stream_id: str,
    summary_cfg: Any,
    task: str,
) -> bool:
    """为单个群聊生成并持久化一条摘要。无有意义内容时跳过。"""
    group = await store.get_group_summary(stream_id)
    limit = int(summary_cfg.max_messages_per_run)

    messages = await stream_api.get_stream_messages(stream_id, limit=limit)
    chat_flow = _build_chat_flow(messages)
    if not messages or not chat_flow:
        return False

    group_name = group.group_name
    platform = group.platform
    group_id = group.group_id

    stream = await stream_api.get_stream(stream_id)
    if stream is not None:
        if not platform:
            platform = str(getattr(stream, "platform", "") or "")
        if not group_name:
            group_name = str(getattr(stream, "stream_name", "") or "")

    # ChatStream 对象不携带 group_id，从数据库流记录补全 platform/group_id/group_name
    info = await stream_api.get_stream_info(stream_id)
    if info is not None:
        if not platform:
            platform = str(info.get("platform") or "")
        if not group_id:
            group_id = str(info.get("group_id") or "")
        if not group_name:
            group_name = str(info.get("group_name") or "")

    # 最后兜底：从消息中提取
    for message in messages:
        if not platform:
            platform = str(getattr(message, "platform", "") or "")
        if not group_id:
            extra = getattr(message, "extra", None)
            group_id = str((extra or {}).get("group_id") or "")
        if platform and group_id:
            break

    system = resolve_prompt(SUMMARY_PROMPT_NAME, SUMMARY_PROMPT)
    user = f"群聊名称：{group_name or stream_id}\n聊天记录（按时间先后排列）：\n{chat_flow}"
    result = await call_sub_agent(
        task=task,
        request_name="shameimaru_summary",
        system=system,
        user=user,
        stream_id=stream_id,
    )
    if not result or result == NO_MEANINGFUL_CONTENT_TOKEN:
        return False

    entry = SummaryEntry(
        timestamp=time.time(),
        content=result,
        participants=_collect_participants(messages),
    )
    await store.append_summary(
        stream_id=stream_id,
        entry=entry,
        platform=platform,
        group_id=group_id,
        group_name=group_name,
        max_entries=int(summary_cfg.max_entries_per_group),
    )
    return True


# ----------------------------------------------------------------------
# 新闻层：新闻记录事件
# ----------------------------------------------------------------------


async def run_news_job(plugin: Any) -> dict[str, Any]:
    """新闻记录事件：对每个群聊分别执行新闻整理。

    每个群聊的摘要单独分组，各自调用一次子 agent（不混合所有群）。
    只消费未废弃的摘要条目；消费后的摘要被标记为废弃而非删除，
    供知识层（Dreaming）读取了解群聊主题。

    Args:
        plugin: 插件实例。

    Returns:
        dict[str, Any]: 统计信息。
    """
    config = _get_config(plugin)
    store = _build_store(plugin)
    news_cfg = config.news
    news_task = _llm_task(config, "news_task")
    persona_task = _llm_task(config, "persona_task")
    max_text_length = int(getattr(config.persona, "max_text_length", 0) or 0)

    groups = await store.list_group_summaries()
    stats: dict[str, Any] = {
        "groups": len(groups),
        "processed": 0,
        "created": 0,
        "evicted": 0,
        "skipped": 0,
    }

    for group in groups:
        if not any(not entry.deprecated for entry in group.entries):
            continue
        try:
            created, evicted = await _news_for_group(
                store, group, news_cfg, news_task
            )
            stats["processed"] += 1
            stats["created"] += created
            stats["evicted"] += len(evicted)
            if evicted:
                await _update_personas_for_removed(
                    store, evicted, persona_task, max_text_length
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"新闻整理失败 stream_id={group.stream_id}: {exc}")
            stats["skipped"] += 1

    logger.info(f"新闻记录完成: {stats}")
    return stats


async def _news_for_group(
    store: ShameimaruMemoryStore,
    group: Any,
    news_cfg: Any,
    task: str,
) -> tuple[int, list[NewsEntry]]:
    """为单个群聊的未废弃摘要执行一次新闻整理，随后标记已消费摘要为废弃。

    Args:
        store: 存储层实例。
        group: 群聊摘要记录。
        news_cfg: 新闻层配置节。
        task: 新闻层子 agent 使用的模型任务名。

    Returns:
        tuple[int, list[NewsEntry]]: (创建的新闻条数, 因上限被淘汰的新闻条目)。
    """
    entries = [entry for entry in group.entries if not entry.deprecated]
    if not entries:
        return 0, []
    cap = int(getattr(news_cfg, "max_input_summaries", 0))
    if cap > 0 and len(entries) > cap:
        entries = entries[-cap:]

    # 构建本群可用人物清单（真实 person_id），供子 agent 选择，防止编造 ID
    roster_by_id: dict[str, PersonRef] = {}
    roster_by_name: dict[str, PersonRef] = {}
    for entry in entries:
        for ref in entry.participants:
            roster_by_id.setdefault(ref.person_id, ref)
            if ref.name:
                roster_by_name.setdefault(ref.name, ref)
    roster_lines = [
        f"- {ref.person_id}（{ref.name or '无名称'}）"
        for ref in sorted(roster_by_id.values(), key=lambda item: item.person_id)
    ]
    roster_text = "\n".join(roster_lines) if roster_lines else "（无）"

    lines: list[str] = []
    for entry in entries:
        clock = format_local_time(entry.timestamp)
        lines.append(f"【{clock}】\n{entry.content}")
    summaries_text = "\n\n".join(lines)

    system = resolve_prompt(NEWS_PROMPT_NAME, NEWS_PROMPT)
    user = (
        f"群聊名称：{group.group_name or group.stream_id}\n"
        f"群聊摘要：\n{summaries_text}\n\n"
        f"可用人物清单（participants 的 person_id 必须从中选择，不得编造）：\n{roster_text}"
    )
    result = await call_sub_agent(
        task=task,
        request_name="shameimaru_news",
        system=system,
        user=user,
        stream_id=group.stream_id,
    )
    # 调用失败（LLM 异常/空响应）时保留摘要，等待下轮重试，避免摘要数据永久丢失
    if not result:
        return 0, []
    items = extract_json_array(result)

    now = time.time()
    created = 0
    evicted: list[NewsEntry] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not content:
            continue
        participants = _resolve_participants(
            item.get("participants"), roster_by_id, roster_by_name
        )
        entry = NewsEntry(
            id=f"news-{uuid.uuid4().hex}",
            timestamp=now,
            title=title,
            content=content,
            participants=participants,
        )
        evicted.extend(await store.append_news(entry, int(news_cfg.max_entries)))
        created += 1

    # 参与处理的摘要已消费，标记为废弃（保留供 Dreaming 读取，新闻层不再消费）
    await store.deprecate_group_summaries(group.stream_id)
    return created, evicted


# ----------------------------------------------------------------------
# 知识层：Dreaming 整理事件
# ----------------------------------------------------------------------


async def run_dream_job(plugin: Any) -> dict[str, Any]:
    """Dreaming 整理事件：对每个群聊分别执行知识整理。

    观察群聊摘要了解内容主题，阅读全部新闻，将值得持久化的新闻
    整理成知识条目写入 booku_memory_store，并删除被知识化的新闻。

    Args:
        plugin: 插件实例。

    Returns:
        dict[str, Any]: 统计信息。
    """
    config = _get_config(plugin)
    store = _build_store(plugin)
    knowledge_cfg = config.knowledge

    service = service_api.get_service(str(knowledge_cfg.service_signature))
    if service is None:
        logger.warning(
            f"知识库服务不可用: {knowledge_cfg.service_signature}，Dreaming 整理已跳过"
        )
        return {"created": 0, "deleted": 0, "skipped": True}

    groups = await store.list_group_summaries()
    news_list = await store.get_news()
    persona_task = _llm_task(config, "persona_task")
    knowledge_task = _llm_task(config, "knowledge_task")
    max_text_length = int(getattr(config.persona, "max_text_length", 0) or 0)
    stats: dict[str, Any] = {"groups": len(groups), "created": 0, "deleted": 0}

    for group in groups:
        if not news_list:
            break
        try:
            created_ids = await _dream_group(
                service, group, news_list, knowledge_cfg, knowledge_task
            )
            stats["created"] += len(created_ids)
            if created_ids:
                removed = await store.remove_news(created_ids)
                news_list = await store.get_news()
                stats["deleted"] += len(removed)
                await _update_personas_for_removed(
                    store, removed, persona_task, max_text_length
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Dreaming 知识整理失败 stream_id={group.stream_id}: {exc}")

    logger.info(f"Dreaming 整理完成: {stats}")
    return stats


async def _dream_group(
    service: Any,
    group: Any,
    news_list: list[NewsEntry],
    knowledge_cfg: Any,
    knowledge_task: str,
) -> list[str]:
    """对单个群聊执行一次知识整理，返回被知识化的新闻 ID 列表。

    群聊摘要（含已被新闻层消费、标记为废弃的条目）用于了解该群聊的
    内容主题：废弃摘要不会被新闻层重复消费，但这里仍会被读取。
    """
    group_summary_lines = [
        f"【{format_local_time(entry.timestamp)}】\n{entry.content}"
        for entry in group.entries[-int(knowledge_cfg.max_group_summaries_input):]
    ]
    group_summary_text = "\n\n".join(group_summary_lines) or "（该群聊暂无摘要）"

    news_payload = json.dumps(
        [
            {
                "id": entry.id,
                "timestamp": format_local_time(entry.timestamp),
                "title": entry.title,
                "content": entry.content,
                "participants": [ref.to_dict() for ref in entry.participants],
            }
            for entry in news_list
        ],
        ensure_ascii=False,
        indent=1,
    )

    system = resolve_prompt(DREAM_PROMPT_NAME, DREAM_PROMPT)
    user = (
        f"群聊摘要（用于了解该群聊的内容主题）：\n{group_summary_text}\n\n"
        f"当前新闻列表：\n{news_payload}"
    )
    result = await call_sub_agent(
        task=knowledge_task,
        request_name="shameimaru_dream",
        system=system,
        user=user,
        stream_id=group.stream_id,
    )
    items = extract_json_array(result)

    created_ids: list[str] = []
    seen_ids: set[str] = set()
    news_by_id = {entry.id: entry for entry in news_list}
    for item in items:
        news_id = str(item.get("news_id") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not news_id or not title or not content:
            continue
        # 同一响应中重复出现的 news_id 只处理一次，避免重复写入知识库
        if news_id in seen_ids:
            continue
        seen_ids.add(news_id)
        target = news_by_id.get(news_id)
        if target is None:
            continue
        knowledge_type = str(item.get("knowledge_type") or "").strip()
        try:
            await service.create(
                title=title,
                content=content,
                folder_id=str(knowledge_cfg.folder_id),
                bucket="knowledge",
                memory_type="knowledge",
                related_people=[ref.person_id for ref in target.participants],
                knowledge_type=knowledge_type,
                event_start_at=target.timestamp,
                event_end_at=target.timestamp,
                source="shameimaru_memory",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"知识条目写入失败 news_id={news_id}: {exc}")
            continue
        created_ids.append(news_id)
    return created_ids


# ----------------------------------------------------------------------
# 人物层：被删除新闻的人物背景信息增量更新
# ----------------------------------------------------------------------


async def _update_personas_for_removed(
    store: ShameimaruMemoryStore,
    removed: list[NewsEntry],
    task: str,
    max_text_length: int,
) -> None:
    """根据被删除的新闻条目，逐个增量更新涉及人物的背景信息。

    Args:
        store: 存储层实例。
        removed: 被删除的新闻条目列表。
        task: 人物层子 agent 使用的模型任务名。
        max_text_length: 单个人物背景信息的最大长度，<=0 表示不限制。
    """
    by_person: dict[str, list[NewsEntry]] = {}
    for entry in removed:
        for ref in entry.participants:
            if not ref.person_id:
                continue
            by_person.setdefault(ref.person_id, []).append(entry)

    if not by_person:
        return

    system = resolve_prompt(PERSONA_PROMPT_NAME, PERSONA_PROMPT)
    if max_text_length > 0:
        system = (
            f"{system}\n\n"
            f"重要约束：输出的人物信息总长度请控制在 {max_text_length} 字以内，"
            "不要输出过长的文本。"
        )
    for person_id, entries in by_person.items():
        try:
            await _update_persona(
                store, system, person_id, entries, task, max_text_length
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"人物背景更新失败，该人物的本次增量信息将永久丢失 "
                f"person_id={person_id}: {exc}"
            )


async def _update_persona(
    store: ShameimaruMemoryStore,
    system: str,
    person_id: str,
    entries: list[NewsEntry],
    task: str,
    max_text_length: int,
) -> None:
    """更新单个人物的背景信息。

    将旧文本与新内容一并交给 LLM 融合生成新文本（而非机械拼接），
    写入前按 ``max_text_length`` 硬截断兜底。
    提示词中携带该人物在新闻中的名字，并强调以该人物本人为视角，
    防止 LLM 把内容主角（如 Bot）误当作被维护的人物。
    """
    current = await store.get_persona(person_id)
    person_name = ""
    for entry in sorted(entries, key=lambda item: item.timestamp):
        for ref in entry.participants:
            if ref.person_id == person_id and ref.name:
                person_name = ref.name
                break
        if person_name:
            break
    content_lines = [
        f"- {entry.title}: {entry.content}"
        for entry in sorted(entries, key=lambda item: item.timestamp)
    ]
    user = (
        f"人物 ID：{person_id}\n"
        f"人物名字：{person_name or '（未知，请根据新内容推断）'}\n"
        f"现有背景信息：\n{current or '（无）'}\n\n"
        f"关于该人物的新内容：\n" + "\n".join(content_lines)
    )
    new_text = await call_sub_agent(
        task=task,
        request_name="shameimaru_persona",
        system=system,
        user=user,
    )
    if not new_text:
        return
    if max_text_length > 0 and len(new_text) > max_text_length:
        new_text = new_text[:max_text_length]
    await store.set_persona(person_id, new_text)
