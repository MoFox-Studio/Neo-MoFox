"""扁平化消息 API。

为插件提供消息查询、计数与可读格式化接口。
接口能力参考旧版设计，但数据结构完全采用 Neo-MoFox 最新模型语义。
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, cast

from src.core.models.sql_alchemy import Messages, PersonInfo
from src.kernel.db import QueryBuilder


def _get_adapter_manager():
	"""延迟获取 AdapterManager，避免导入时循环依赖。

	Returns:
		适配器管理器实例
	"""
	from src.core.managers.adapter_manager import get_adapter_manager

	return get_adapter_manager()


def _get_command_manager():
	"""延迟获取 CommandManager，避免导入时循环依赖。

	Returns:
		命令管理器实例
	"""
	from src.core.managers.command_manager import get_command_manager

	return get_command_manager()


def _validate_timestamp(value: float, name: str) -> None:
	"""校验时间戳参数。

	Args:
		value: 时间戳数值
		name: 参数名称

	Returns:
		None
	"""
	if not isinstance(value, int | float):
		raise ValueError(f"{name} 必须是数字类型")


def _validate_limit(limit: int) -> None:
	"""校验 limit 参数。

	Args:
		limit: 限制数量

	Returns:
		None
	"""
	if not isinstance(limit, int) or limit < 0:
		raise ValueError("limit 必须是非负整数")


def _validate_stream_id(stream_id: str) -> None:
	"""校验 stream_id 参数。

	Args:
		stream_id: 聊天流 ID

	Returns:
		None
	"""
	if not isinstance(stream_id, str):
		raise ValueError("stream_id 必须是字符串类型")
	if not stream_id:
		raise ValueError("stream_id 不能为空")


def _validate_limit_mode(limit_mode: str) -> None:
	"""校验 limit_mode 参数。

	Args:
		limit_mode: 排序模式

	Returns:
		None
	"""
	if limit_mode not in {"earliest", "latest"}:
		raise ValueError("limit_mode 必须是 'earliest' 或 'latest'")


def _resolve_ordering(limit_mode: str) -> tuple[str, str]:
	"""根据 limit_mode 返回查询排序与最终输出排序。

	Args:
		limit_mode: 排序模式

	Returns:
		查询排序字段与结果排序字段
	"""
	_validate_limit_mode(limit_mode)
	if limit_mode == "earliest":
		return "time", "time"
	return "-time", "time"


async def _load_person_info_map(person_ids: list[str]) -> dict[str, dict[str, Any]]:
	"""批量加载 person_info，用于补全发送者字段。

	Args:
		person_ids: person_id 列表

	Returns:
		person_id 到人员信息的映射
	"""
	valid_ids = [person_id for person_id in person_ids if person_id]
	if not valid_ids:
		return {}

	rows = await QueryBuilder(PersonInfo).filter(person_id__in=list(set(valid_ids))).all(
		as_dict=True
	)
	return {str(row.get("person_id")): row for row in rows}


def _is_command_message(message: dict[str, Any]) -> bool:
	"""判断消息是否为命令消息。

	Args:
		message: 消息字典

	Returns:
		是否为命令消息
	"""
	text = str(
		message.get("processed_plain_text")
		or message.get("content")
		or ""
	)
	return _get_command_manager().is_command(text)


async def _rows_to_message_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""将数据库消息行映射为最新消息字典结构。

	Args:
		rows: 数据库消息行列表

	Returns:
		消息字典列表
	"""
	person_map = await _load_person_info_map(
		[str(row.get("person_id") or "") for row in rows]
	)

	result: list[dict[str, Any]] = []
	for row in rows:
		person_id = str(row.get("person_id") or "")
		person = person_map.get(person_id, {})
		sender_id = str(person.get("user_id") or person_id or "system")
		sender_name = str(
			person.get("nickname")
			or person.get("cardname")
			or sender_id
			or "未知用户"
		)

		item = {
			"id": row.get("id"),
			"message_id": row.get("message_id"),
			"time": float(row.get("time") or 0.0),
			"stream_id": str(row.get("stream_id") or ""),
			"person_id": person_id,
			"sender_id": sender_id,
			"sender_name": sender_name,
			"sender_cardname": person.get("cardname"),
			"message_type": str(row.get("message_type") or "text"),
			"content": row.get("content") or "",
			"processed_plain_text": row.get("processed_plain_text")
			or row.get("content")
			or "",
			"reply_to": row.get("reply_to"),
			"platform": row.get("platform") or "",
			"extra": {},
		}
		result.append(item)

	return result


async def _query_messages(
	*,
	start_time: float | None = None,
	end_time: float | None = None,
	inclusive: bool = False,
	before_time: float | None = None,
	stream_id: str | None = None,
	person_ids: list[str] | None = None,
	limit: int = 0,
	limit_mode: str = "latest",
	filter_command: bool = False,
) -> list[dict[str, Any]]:
	"""按条件查询消息并返回最新结构字典列表。

	Args:
		start_time: 起始时间戳，可选
		end_time: 结束时间戳，可选
		inclusive: 是否包含边界
		before_time: 指定时间戳之前的消息，可选
		stream_id: 聊天流 ID，可选
		person_ids: 用户 person_id 列表，可选
		limit: 限制数量
		limit_mode: 排序模式
		filter_command: 是否过滤命令消息

	Returns:
		消息字典列表
	"""
	order_for_query, order_for_result = _resolve_ordering(limit_mode)

	query = QueryBuilder(Messages)

	if stream_id:
		query = query.filter(stream_id=stream_id)

	if start_time is not None:
		op = "gte" if inclusive else "gt"
		query = query.filter(**{f"time__{op}": start_time})

	if end_time is not None:
		op = "lte" if inclusive else "lt"
		query = query.filter(**{f"time__{op}": end_time})

	if before_time is not None:
		query = query.filter(time__lt=before_time)

	if person_ids:
		query = query.filter(person_id__in=person_ids)

	query = query.order_by(order_for_query)
	if limit > 0:
		query = query.limit(limit)

	rows = cast(list[dict[str, Any]], await query.all(as_dict=True))
	messages = await _rows_to_message_dicts(rows)

	if filter_command:
		messages = [msg for msg in messages if not _is_command_message(msg)]

	if order_for_result == "time":
		messages.sort(key=lambda item: float(item.get("time") or 0.0))

	return messages


async def _apply_filter_bot(
	messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	"""按当前激活适配器 Bot 信息过滤机器人自身消息。

	Args:
		messages: 消息字典列表

	Returns:
		过滤后的消息字典列表
	"""
	if not messages:
		return []

	platform_set = {
		str(msg.get("platform") or "")
		for msg in messages
		if msg.get("platform")
	}
	bot_ids: set[str] = set()

	adapter_manager = _get_adapter_manager()
	for platform in platform_set:
		try:
			bot_info = await adapter_manager.get_bot_info_by_platform(platform)
		except Exception:
			bot_info = None
		if bot_info and bot_info.get("bot_id"):
			bot_ids.add(str(bot_info["bot_id"]))

	if not bot_ids:
		return messages

	return [msg for msg in messages if str(msg.get("sender_id") or "") not in bot_ids]


async def get_messages_by_time(
	start_time: float,
	end_time: float,
	limit: int = 0,
	limit_mode: str = "latest",
	filter_bot: bool = False,
) -> list[dict[str, Any]]:
	"""获取指定时间范围内的消息。

	Args:
		start_time: 起始时间戳
		end_time: 结束时间戳
		limit: 限制数量
		limit_mode: 排序模式
		filter_bot: 是否过滤机器人消息

	Returns:
		消息字典列表
	"""
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")
	_validate_limit(limit)

	messages = await _query_messages(
		start_time=float(start_time),
		end_time=float(end_time),
		inclusive=False,
		limit=limit,
		limit_mode=limit_mode,
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def get_messages_by_time_in_chat(
	stream_id: str,
	start_time: float,
	end_time: float,
	limit: int = 0,
	limit_mode: str = "latest",
	filter_bot: bool = False,
	filter_command: bool = False,
) -> list[dict[str, Any]]:
	"""获取指定 stream 中指定时间范围内的消息。

	Args:
		stream_id: 聊天流 ID
		start_time: 起始时间戳
		end_time: 结束时间戳
		limit: 限制数量
		limit_mode: 排序模式
		filter_bot: 是否过滤机器人消息
		filter_command: 是否过滤命令消息

	Returns:
		消息字典列表
	"""
	_validate_stream_id(stream_id)
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")
	_validate_limit(limit)

	messages = await _query_messages(
		stream_id=stream_id,
		start_time=float(start_time),
		end_time=float(end_time),
		inclusive=False,
		limit=limit,
		limit_mode=limit_mode,
		filter_command=filter_command,
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def get_messages_by_time_in_chat_inclusive(
	stream_id: str,
	start_time: float,
	end_time: float,
	limit: int = 0,
	limit_mode: str = "latest",
	filter_bot: bool = False,
	filter_command: bool = False,
) -> list[dict[str, Any]]:
	"""获取指定 stream 中指定时间范围内的消息（包含边界）。

	Args:
		stream_id: 聊天流 ID
		start_time: 起始时间戳
		end_time: 结束时间戳
		limit: 限制数量
		limit_mode: 排序模式
		filter_bot: 是否过滤机器人消息
		filter_command: 是否过滤命令消息

	Returns:
		消息字典列表
	"""
	_validate_stream_id(stream_id)
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")
	_validate_limit(limit)

	messages = await _query_messages(
		stream_id=stream_id,
		start_time=float(start_time),
		end_time=float(end_time),
		inclusive=True,
		limit=limit,
		limit_mode=limit_mode,
		filter_command=filter_command,
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def get_messages_by_time_in_chat_for_users(
	stream_id: str,
	start_time: float,
	end_time: float,
	person_ids: list[str],
	limit: int = 0,
	limit_mode: str = "latest",
) -> list[dict[str, Any]]:
	"""获取指定 stream 中指定用户在时间范围内的消息。

	Args:
		stream_id: 聊天流 ID
		start_time: 起始时间戳
		end_time: 结束时间戳
		person_ids: 用户 person_id 列表
		limit: 限制数量
		limit_mode: 排序模式

	Returns:
		消息字典列表
	"""
	_validate_stream_id(stream_id)
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")
	_validate_limit(limit)

	return await _query_messages(
		stream_id=stream_id,
		start_time=float(start_time),
		end_time=float(end_time),
		person_ids=person_ids,
		limit=limit,
		limit_mode=limit_mode,
	)


async def get_random_chat_messages(
	start_time: float,
	end_time: float,
	limit: int = 0,
	limit_mode: str = "latest",
	filter_bot: bool = False,
) -> list[dict[str, Any]]:
	"""随机选择一个 stream，返回该 stream 在时间范围内的消息。

	Args:
		start_time: 起始时间戳
		end_time: 结束时间戳
		limit: 限制数量
		limit_mode: 排序模式
		filter_bot: 是否过滤机器人消息

	Returns:
		消息字典列表
	"""
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")
	_validate_limit(limit)

	candidates = cast(
		list[dict[str, Any]],
		await QueryBuilder(Messages).filter(
		time__gt=float(start_time),
		time__lt=float(end_time),
	).all(as_dict=True),
	)

	stream_ids = list(
		{
			str(row.get("stream_id") or "")
			for row in candidates
			if row.get("stream_id")
		}
	)
	if not stream_ids:
		return []

	selected_stream_id = random.choice(stream_ids)
	messages = await _query_messages(
		stream_id=selected_stream_id,
		start_time=float(start_time),
		end_time=float(end_time),
		inclusive=False,
		limit=limit,
		limit_mode=limit_mode,
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def get_messages_by_time_for_users(
	start_time: float,
	end_time: float,
	person_ids: list[str],
	limit: int = 0,
	limit_mode: str = "latest",
) -> list[dict[str, Any]]:
	"""获取指定用户在所有 stream 中指定时间范围内的消息。

	Args:
		start_time: 起始时间戳
		end_time: 结束时间戳
		person_ids: 用户 person_id 列表
		limit: 限制数量
		limit_mode: 排序模式

	Returns:
		消息字典列表
	"""
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")
	_validate_limit(limit)

	return await _query_messages(
		start_time=float(start_time),
		end_time=float(end_time),
		person_ids=person_ids,
		limit=limit,
		limit_mode=limit_mode,
	)


async def get_messages_before_time(
	timestamp: float,
	limit: int = 0,
	filter_bot: bool = False,
) -> list[dict[str, Any]]:
	"""获取指定时间戳之前的消息。

	Args:
		timestamp: 时间戳
		limit: 限制数量
		filter_bot: 是否过滤机器人消息

	Returns:
		消息字典列表
	"""
	_validate_timestamp(timestamp, "timestamp")
	_validate_limit(limit)

	messages = await _query_messages(
		before_time=float(timestamp),
		limit=limit,
		limit_mode="latest",
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def get_messages_before_time_in_chat(
	stream_id: str,
	timestamp: float,
	limit: int = 0,
	filter_bot: bool = False,
) -> list[dict[str, Any]]:
	"""获取指定 stream 中指定时间戳之前的消息。

	Args:
		stream_id: 聊天流 ID
		timestamp: 时间戳
		limit: 限制数量
		filter_bot: 是否过滤机器人消息

	Returns:
		消息字典列表
	"""
	_validate_stream_id(stream_id)
	_validate_timestamp(timestamp, "timestamp")
	_validate_limit(limit)

	messages = await _query_messages(
		stream_id=stream_id,
		before_time=float(timestamp),
		limit=limit,
		limit_mode="latest",
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def get_messages_before_time_for_users(
	timestamp: float,
	person_ids: list[str],
	limit: int = 0,
) -> list[dict[str, Any]]:
	"""获取指定用户在指定时间戳之前的消息。

	Args:
		timestamp: 时间戳
		person_ids: 用户 person_id 列表
		limit: 限制数量

	Returns:
		消息字典列表
	"""
	_validate_timestamp(timestamp, "timestamp")
	_validate_limit(limit)

	return await _query_messages(
		before_time=float(timestamp),
		person_ids=person_ids,
		limit=limit,
		limit_mode="latest",
	)


async def get_recent_messages(
	stream_id: str,
	hours: float = 24.0,
	limit: int = 100,
	limit_mode: str = "latest",
	filter_bot: bool = False,
) -> list[dict[str, Any]]:
	"""获取指定 stream 中最近一段时间的消息。

	Args:
		stream_id: 聊天流 ID
		hours: 回溯小时数
		limit: 限制数量
		limit_mode: 排序模式
		filter_bot: 是否过滤机器人消息

	Returns:
		消息字典列表
	"""
	_validate_stream_id(stream_id)
	if not isinstance(hours, int | float) or hours < 0:
		raise ValueError("hours 不能是负数")
	_validate_limit(limit)

	now = time.time()
	start_time = now - float(hours) * 3600
	messages = await _query_messages(
		stream_id=stream_id,
		start_time=start_time,
		end_time=now,
		limit=limit,
		limit_mode=limit_mode,
	)
	if filter_bot:
		return await _apply_filter_bot(messages)
	return messages


async def count_new_messages(
	stream_id: str,
	start_time: float = 0.0,
	end_time: float | None = None,
) -> int:
	"""计算指定 stream 中从开始时间到结束时间的新消息数量。

	Args:
		stream_id: 聊天流 ID
		start_time: 起始时间戳
		end_time: 结束时间戳，可选

	Returns:
		新消息数量
	"""
	_validate_stream_id(stream_id)
	_validate_timestamp(start_time, "start_time")
	if end_time is not None:
		_validate_timestamp(end_time, "end_time")

	query = QueryBuilder(Messages).filter(
		stream_id=stream_id,
		time__gt=float(start_time),
	)
	if end_time is not None:
		query = query.filter(time__lt=float(end_time))
	return await query.count()


async def count_new_messages_for_users(
	stream_id: str,
	start_time: float,
	end_time: float,
	person_ids: list[str],
) -> int:
	"""计算指定 stream 中指定用户在时间范围内的新消息数量。

	Args:
		stream_id: 聊天流 ID
		start_time: 起始时间戳
		end_time: 结束时间戳
		person_ids: 用户 person_id 列表

	Returns:
		新消息数量
	"""
	_validate_stream_id(stream_id)
	_validate_timestamp(start_time, "start_time")
	_validate_timestamp(end_time, "end_time")

	return await QueryBuilder(Messages).filter(
		stream_id=stream_id,
		person_id__in=person_ids,
		time__gt=float(start_time),
		time__lt=float(end_time),
	).count()


def _format_timestamp(
	ts: float,
	timestamp_mode: str,
	now_ts: float,
) -> str:
	"""格式化时间戳。

	Args:
		ts: 时间戳
		timestamp_mode: 时间显示模式
		now_ts: 当前时间戳

	Returns:
		格式化后的时间文本
	"""
	if timestamp_mode == "absolute":
		return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

	delta = max(0.0, now_ts - ts)
	if delta < 60:
		return "刚刚"
	if delta < 3600:
		return f"{int(delta // 60)}分钟前"
	if delta < 86400:
		return f"{int(delta // 3600)}小时前"
	return f"{int(delta // 86400)}天前"


async def build_readable_messages_to_str(
	messages: list[dict[str, Any]],
	replace_bot_name: bool = True,
	merge_messages: bool = False,
	timestamp_mode: str = "relative",
	read_mark: float = 0.0,
	truncate: bool = False,
	show_actions: bool = False,
) -> str:
	"""将消息列表构建为可读字符串。

	Args:
		messages: 消息字典列表
		replace_bot_name: 是否替换机器人名称
		merge_messages: 是否合并同一发送者的连续消息
		timestamp_mode: 时间显示模式
		read_mark: 已读时间戳
		truncate: 是否截断过长文本
		show_actions: 是否包含动作内容

	Returns:
		可读消息文本
	"""
	text, _ = await build_readable_messages_with_details(
		messages=messages,
		replace_bot_name=replace_bot_name,
		merge_messages=merge_messages,
		timestamp_mode=timestamp_mode,
		truncate=truncate,
	)

	if read_mark > 0:
		lines: list[str] = []
		inserted = False
		for message in sorted(messages, key=lambda item: float(item.get("time") or 0.0)):
			ts = float(message.get("time") or 0.0)
			if not inserted and ts >= read_mark:
				lines.append("--- 未读消息 ---")
				inserted = True
			msg_text = str(
				message.get("processed_plain_text")
				or message.get("content")
				or ""
			)
			if truncate and len(msg_text) > 120:
				msg_text = f"{msg_text[:117]}..."
			lines.append(msg_text)
		if lines:
			return "\n".join(lines)

	if show_actions:
		return text
	return text


async def build_readable_messages_with_details(
	messages: list[dict[str, Any]],
	replace_bot_name: bool = True,
	merge_messages: bool = False,
	timestamp_mode: str = "relative",
	truncate: bool = False,
) -> tuple[str, list[tuple[float, str, str]]]:
	"""将消息列表构建为可读字符串并返回详细元组。

	Args:
		messages: 消息字典列表
		replace_bot_name: 是否替换机器人名称
		merge_messages: 是否合并同一发送者的连续消息
		timestamp_mode: 时间显示模式
		truncate: 是否截断过长文本

	Returns:
		可读消息文本与明细列表
	"""
	if timestamp_mode not in {"relative", "absolute"}:
		raise ValueError("timestamp_mode 必须是 'relative' 或 'absolute'")

	ordered_messages = sorted(messages, key=lambda item: float(item.get("time") or 0.0))
	details: list[tuple[float, str, str]] = []
	lines: list[str] = []
	now_ts = time.time()

	pending_sender = ""
	pending_timestamp = 0.0
	pending_content = ""

	def flush_pending() -> None:
		nonlocal pending_sender, pending_timestamp, pending_content
		if not pending_content:
			return
		ts_text = _format_timestamp(pending_timestamp, timestamp_mode, now_ts)
		lines.append(f"[{ts_text}] {pending_sender}: {pending_content}")
		details.append((pending_timestamp, pending_sender, pending_content))
		pending_sender = ""
		pending_timestamp = 0.0
		pending_content = ""

	for message in ordered_messages:
		msg_time = float(message.get("time") or 0.0)
		sender = str(message.get("sender_name") or message.get("sender_id") or "未知用户")
		content = str(message.get("processed_plain_text") or message.get("content") or "")

		if truncate and len(content) > 120:
			content = f"{content[:117]}..."

		if replace_bot_name and sender in {"bot", "Bot", "机器人"}:
			sender = "你"

		if merge_messages and sender == pending_sender:
			pending_content = f"{pending_content} / {content}" if pending_content else content
			continue

		flush_pending()
		pending_sender = sender
		pending_timestamp = msg_time
		pending_content = content

	flush_pending()
	return "\n".join(lines), details


async def get_person_ids_from_messages(messages: list[dict[str, Any]]) -> list[str]:
	"""从消息列表中提取去重后的 person_id 列表。

	Args:
		messages: 消息字典列表

	Returns:
		person_id 列表
	"""
	person_ids = {
		str(message.get("person_id") or "")
		for message in messages
		if message.get("person_id")
	}
	return sorted(person_ids)


async def filter_bot_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""从消息列表中过滤 Bot 自身消息。

	Args:
		messages: 消息字典列表

	Returns:
		过滤后的消息字典列表
	"""
	return await _apply_filter_bot(messages)

