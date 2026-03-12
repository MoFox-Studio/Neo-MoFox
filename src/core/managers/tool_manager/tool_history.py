"""工具调用历史记录器。

本模块提供 ToolHistory 类，用于记录和管理工具调用的历史信息。
支持缓存机制，避免重复调用相同参数的工具。
"""

import time
from dataclasses import dataclass, field
from typing import Any

from src.kernel.logger import get_logger


logger = get_logger("tool_history")


@dataclass
class ToolCallRecord:
    """工具调用记录。

    Attributes:
        tool_name: 工具名称
        args: 调用参数
        result: 执行结果
        status: 执行状态 (success/error/timeout)
        error_message: 错误信息
        execution_time: 执行耗时（秒）
        timestamp: 调用时间戳
        cache_hit: 是否命中缓存
    """

    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any] | None
    status: str = "success"  # success/error/timeout
    error_message: str | None = None
    execution_time: float = 0.0
    timestamp: float = field(default_factory=time.time)
    cache_hit: bool = False


@dataclass
class CachedResult:
    """缓存结果。

    Attributes:
        result: 执行结果
        timestamp: 缓存时间戳
        ttl: 生存时间（秒）
    """

    result: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    ttl: int = 3600  # 默认 1 小时

    def is_expired(self) -> bool:
        """检查缓存是否过期。

        Returns:
            bool: 是否过期
        """
        return time.time() - self.timestamp > self.ttl


class ToolHistory:
    """工具调用历史记录器。

    负责记录工具调用历史、管理结果缓存、提供查询接口。
    支持基于参数的缓存键生成和过期检查。

    Attributes:
        _history: 工具调用历史列表
        _cache: 结果缓存字典
        _max_history_size: 最大历史记录数量

    Examples:
        >>> history = ToolHistory(max_history_size=100)
        >>> history.add_call("get_weather", {"city": "Beijing"}, {"temp": 20})
        >>> cached = history.get_cached("get_weather", {"city": "Beijing"})
        >>> records = history.get_recent_history(count=10)
        >>> stats = history.get_stats()
    """

    def __init__(self, max_history_size: int = 1000) -> None:
        """初始化工具调用历史记录器。

        Args:
            max_history_size: 最大历史记录数量
        """
        self._history: list[ToolCallRecord] = []
        self._cache: dict[str, CachedResult] = {}
        self._max_history_size = max_history_size

        logger.debug(f"工具调用历史记录器初始化完成，最大历史记录: {max_history_size}")

    def add_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any] | None,
        status: str = "success",
        error_message: str | None = None,
        execution_time: float = 0.0,
        cache_hit: bool = False,
    ) -> None:
        """添加工具调用记录。

        Args:
            tool_name: 工具名称
            args: 调用参数
            result: 执行结果
            status: 执行状态
            error_message: 错误信息
            execution_time: 执行耗时
            cache_hit: 是否命中缓存

        Examples:
            >>> history.add_call(
            ...     "get_weather",
            ...     {"city": "Beijing"},
            ...     {"temp": 20},
            ...     execution_time=0.5
            ... )
        """
        record = ToolCallRecord(
            tool_name=tool_name,
            args=args,
            result=result,
            status=status,
            error_message=error_message,
            execution_time=execution_time,
            cache_hit=cache_hit,
        )

        self._history.append(record)

        # 限制历史记录大小
        if len(self._history) > self._max_history_size:
            self._history.pop(0)

        logger.debug(f"添加工具调用记录: {tool_name}, 状态: {status}, 耗时: {execution_time:.2f}s")

    def cache_result(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        ttl: int = 3600,
    ) -> None:
        """缓存工具执行结果。

        Args:
            tool_name: 工具名称
            args: 调用参数
            result: 执行结果
            ttl: 生存时间（秒）

        Examples:
            >>> history.cache_result(
            ...     "get_weather",
            ...     {"city": "Beijing"},
            ...     {"temp": 20},
            ...     ttl=1800  # 30 分钟
            ... )
        """
        cache_key = self._build_cache_key(tool_name, args)
        self._cache[cache_key] = CachedResult(result=result, ttl=ttl)
        logger.debug(f"缓存工具结果: {tool_name}, TTL: {ttl}s")

    def get_cached(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """获取缓存结果。

        Args:
            tool_name: 工具名称
            args: 调用参数

        Returns:
            dict[str, Any] | None: 缓存的结果，如果未找到或已过期则返回 None

        Examples:
            >>> result = history.get_cached("get_weather", {"city": "Beijing"})
            >>> {"temp": 20}
        """
        cache_key = self._build_cache_key(tool_name, args)

        if cache_key not in self._cache:
            return None

        cached = self._cache[cache_key]

        # 检查是否过期
        if cached.is_expired():
            del self._cache[cache_key]
            logger.debug(f"缓存已过期: {tool_name}")
            return None

        logger.debug(f"缓存命中: {tool_name}")
        return cached.result

    def get_recent_history(self, count: int = 10) -> list[ToolCallRecord]:
        """获取最近的历史记录。

        Args:
            count: 返回的记录数量

        Returns:
            list[ToolCallRecord]: 最近的工具调用记录

        Examples:
            >>> records = history.get_recent_history(count=10)
        """
        return self._history[-count:]

    def get_history_by_tool(self, tool_name: str, count: int = 10) -> list[ToolCallRecord]:
        """获取特定工具的历史记录。

        Args:
            tool_name: 工具名称
            count: 返回的记录数量

        Returns:
            list[ToolCallRecord]: 该工具的最近调用记录

        Examples:
            >>> records = history.get_history_by_tool("get_weather")
        """
        tool_records = [r for r in self._history if r.tool_name == tool_name]
        return tool_records[-count:]

    def format_for_prompt(
        self, max_records: int = 5, include_results: bool = True
    ) -> str:
        """格式化历史记录为提示词。

        Args:
            max_records: 最大记录数
            include_results: 是否包含结果详情

        Returns:
            str: 格式化的提示词文本

        Examples:
            >>> prompt_text = history.format_for_prompt(max_records=5)
        """
        records = self.get_recent_history(count=max_records)

        if not records:
            return "（无工具调用历史）"

        lines = ["## 最近的工具调用历史："]

        for i, record in enumerate(reversed(records), 1):
            status_emoji = "✅" if record.status == "success" else "❌"
            cache_info = " [缓存]" if record.cache_hit else ""

            lines.append(
                f"{i}. {status_emoji} **{record.tool_name}** "
                f"({record.execution_time:.2f}s){cache_info}"
            )

            # 显示参数
            if record.args:
                args_str = ", ".join(f"{k}={v}" for k, v in record.args.items())
                lines.append(f"   参数: {args_str}")

            # 显示结果
            if include_results and record.result:
                content = record.result.get("content", "")
                if isinstance(content, str):
                    content_preview = content[:100] + "..." if len(content) > 100 else content
                    lines.append(f"   结果: {content_preview}")

            # 显示错误
            if record.error_message:
                lines.append(f"   错误: {record.error_message}")

        return "\n".join(lines)

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。

        Returns:
            dict[str, Any]: 统计信息字典

        Examples:
            >>> stats = history.get_stats()
            >>> {
            ...     "total_calls": 100,
            ...     "success_calls": 95,
            ...     "error_calls": 5,
            ...     "cache_hits": 30,
            ...     "avg_execution_time": 0.5,
            ...     "most_used_tools": [...]
            ... }
        """
        if not self._history:
            return {
                "total_calls": 0,
                "success_calls": 0,
                "error_calls": 0,
                "cache_hits": 0,
                "avg_execution_time": 0.0,
                "most_used_tools": [],
            }

        total_calls = len(self._history)
        success_calls = sum(1 for r in self._history if r.status == "success")
        error_calls = sum(1 for r in self._history if r.status == "error")
        timeout_calls = sum(1 for r in self._history if r.status == "timeout")
        cache_hits = sum(1 for r in self._history if r.cache_hit)

        execution_times = [r.execution_time for r in self._history if r.execution_time > 0]
        avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0.0

        # 统计最常用的工具
        tool_counts: dict[str, int] = {}
        for record in self._history:
            tool_counts[record.tool_name] = tool_counts.get(record.tool_name, 0) + 1

        most_used_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total_calls": total_calls,
            "success_calls": success_calls,
            "error_calls": error_calls,
            "timeout_calls": timeout_calls,
            "cache_hits": cache_hits,
            "avg_execution_time": avg_execution_time,
            "most_used_tools": most_used_tools,
            "cache_size": len(self._cache),
        }

    def clear_history(self) -> None:
        """清除所有历史记录。

        Examples:
            >>> history.clear_history()
        """
        self._history.clear()
        logger.debug("工具调用历史已清除")

    def clear_cache(self) -> None:
        """清除所有缓存。

        Examples:
            >>> history.clear_cache()
        """
        self._cache.clear()
        logger.debug("工具结果缓存已清除")

    def clear_expired_cache(self) -> int:
        """清除过期的缓存。

        Returns:
            int: 清除的缓存数量

        Examples:
            >>> count = history.clear_expired_cache()
            >>> 5
        """
        expired_keys = [
            key for key, cached in self._cache.items()
            if cached.is_expired()
        ]

        for key in expired_keys:
            del self._cache[key]

        logger.debug(f"清除了 {len(expired_keys)} 个过期缓存")
        return len(expired_keys)

    def _build_cache_key(self, tool_name: str, args: dict[str, Any]) -> str:
        """构建缓存键。

        Args:
            tool_name: 工具名称
            args: 调用参数

        Returns:
            str: 缓存键
        """
        # 简单的键构建方式，未来可以使用更复杂的哈希算法
        import json

        args_str = json.dumps(args, sort_keys=True)
        return f"{tool_name}:{args_str}"

    def __len__(self) -> int:
        """获取历史记录数量。

        Returns:
            int: 历史记录数量
        """
        return len(self._history)
