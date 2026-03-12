"""Tool invocation implementation.

本模块提供 ToolUse 类，负责 Tool 组件的执行、历史记录管理和结果缓存。
"""

import time
import json
from typing import Any, TYPE_CHECKING

from src.kernel.logger import get_logger
from src.core.components.registry import get_global_registry

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers.tool_manager.tool_history import ToolHistory
    from src.core.models.message import Message

logger = get_logger("tool_use")


class ToolUse:
    """Tool 调用管理器。

    负责 Tool 组件的执行、历史记录管理和结果缓存。
    类似于 action_manager 的 execute_action，但专门用于 Tool 组件。
    支持 ToolHistory 集成和结果缓存机制。

    Attributes:
        _tool_history: 工具调用历史记录器
        _tool_cache: 结果缓存字典（可选）

    Examples:
        >>> tool_use = ToolUse()
        >>> success, result = await tool_use.execute_tool(
        ...     "my_plugin:tool:calculator",
        ...     plugin,
        ...     message,
        ...     expression="2+2"
        ... )
    """

    def __init__(self) -> None:
        """初始化 Tool 调用管理器。"""
        # 导入并初始化 ToolHistory
        from .tool_history import ToolHistory

        self._tool_history = ToolHistory()
        self._cache_enabled: bool = False
        logger.debug("Tool 调用管理器初始化完成")

    async def execute_tool(
        self,
        signature: str,
        plugin: "BasePlugin",
        message: "Message",
        **kwargs: Any,
    ) -> tuple[bool, Any]:
        """执行 Tool 并记录历史。

        创建 Tool 实例并调用其 execute 方法，同时记录执行历史。
        支持结果缓存机制，避免重复调用相同参数的工具。

        Args:
            signature: Tool 组件签名
            plugin: 所属插件实例
            message: 触发的消息
            **kwargs: 传递给 execute 方法的参数

        Returns:
            tuple[bool, Any]: (是否成功, 返回结果)

        Raises:
            ValueError: 如果 Tool 类未找到
            RuntimeError: 如果 Tool 执行失败

        Examples:
            >>> success, result = await tool_use.execute_tool(
            ...     "my_plugin:tool:calculator",
            ...     plugin,
            ...     message,
            ...     expression="2+2"
            ... )
            >>> True, "4"
        """
        start_time = time.time()

        # 从注册表获取 Tool 类
        registry = get_global_registry()
        tool_cls = registry.get(signature)

        from src.core.components.base.tool import BaseTool

        if not tool_cls or not issubclass(tool_cls, BaseTool):
            raise ValueError(f"Tool 类未找到: {signature}")

        # 创建 Tool 实例
        tool_instance = tool_cls(plugin)

        # 检查缓存
        cached_result: dict[str, Any] | None = None
        if self._cache_enabled:
            args_dict = kwargs.copy()
            cached_result = self._tool_history.get_cached(
                tool_instance.tool_name,
                args_dict
            )

            if cached_result is not None:
                # 记录缓存的调用
                execution_time = time.time() - start_time
                self._tool_history.add_call(
                    tool_name=tool_instance.tool_name,
                    args=args_dict,
                    result=cached_result,
                    execution_time=execution_time,
                    cache_hit=True
                )
                logger.debug(f"工具执行缓存命中: {tool_instance.tool_name}")
                return True, cached_result

        # 执行 Tool
        try:
            # 剥离 LLM 自动注入的 reason 参数，避免传入 execute() 时签名不匹配
            kwargs.pop("reason", None)

            # 记录开始执行
            logger.debug(f"开始执行工具: {tool_instance.tool_name}, 参数: {kwargs}")

            # 调用 Tool 的 execute 方法
            success, result = await tool_instance.execute(**kwargs)

            # 格式化结果（确保是字典形式以兼容 ToolHistory）
            if isinstance(result, str):
                formatted_result = {"content": result}
            elif isinstance(result, dict):
                formatted_result = result
            else:
                formatted_result = {"content": str(result)}

            # 计算执行时间
            execution_time = time.time() - start_time

            # 记录到历史
            self._tool_history.add_call(
                tool_name=tool_instance.tool_name,
                args={k: v for k, v in kwargs.items()},
                result=formatted_result,
                status="success" if success else "error",
                error_message=None if success else str(result),
                execution_time=execution_time,
                cache_hit=False
            )

            # 如果成功且开启了缓存，则缓存结果
            if success and self._cache_enabled:
                self._tool_history.cache_result(
                    tool_name=tool_instance.tool_name,
                    args={k: v for k, v in kwargs.items()},
                    result=formatted_result
                )
                logger.debug(f"工具结果已缓存: {tool_instance.tool_name}")

            # 记录执行完成
            status_emoji = "✅" if success else "❌"
            logger.info(f"{status_emoji} 工具执行完成: {tool_instance.tool_name}, 耗时: {execution_time:.2f}s")

            return success, result

        except Exception as e:
            # 记录异常
            execution_time = time.time() - start_time
            error_result = {"content": f"工具执行失败: {str(e)}"}

            self._tool_history.add_call(
                tool_name=tool_instance.tool_name,
                args={k: v for k, v in kwargs.items()},
                result=error_result,
                status="error",
                error_message=str(e),
                execution_time=execution_time,
                cache_hit=False
            )

            logger.error(f"工具执行失败 ({tool_instance.tool_name}): {e}")
            raise RuntimeError(f"Tool 执行失败: {e}") from e

    def get_tool_history(self) -> "ToolHistory":
        """获取工具调用历史记录器。

        Returns:
            ToolHistory: 工具调用历史记录器实例

        Examples:
            >>> history = tool_use.get_tool_history()
            >>> records = history.get_recent_history(count=10)
        """
        return self._tool_history

    def enable_caching(self, enabled: bool = True) -> None:
        """启用或禁用结果缓存。

        Args:
            enabled: 是否启用缓存

        Examples:
            >>> tool_use.enable_caching(True)
        """
        if self._cache_enabled != enabled:
            self._cache_enabled = enabled
            status = "启用" if enabled else "禁用"
            logger.debug(f"工具结果缓存已{status}")

    def clear_cache(self) -> None:
        """清除所有缓存。

        Examples:
            >>> tool_use.clear_cache()
        """
        self._tool_history.clear_cache()
        logger.debug("工具缓存已清除")

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息。

        Returns:
            dict[str, Any]: 缓存统计信息

        Examples:
            >>> stats = tool_use.get_cache_stats()
        """
        return self._tool_history.get_stats()

    def _build_cache_key(self, tool_name: str, args: dict[str, Any]) -> str:
        """构建缓存键。

        Args:
            tool_name: 工具名称
            args: 调用参数

        Returns:
            str: 缓存键
        """
        args_str = json.dumps(args, sort_keys=True)
        return f"{tool_name}:{args_str}"


# 全局 Tool 调用管理器实例
_global_tool_use: ToolUse | None = None


def get_tool_use() -> ToolUse:
    """获取全局 Tool 调用管理器实例。

    Returns:
        ToolUse: 全局 Tool 调用管理器单例

    Examples:
        >>> tool_use = get_tool_use()
        >>> success, result = await tool_use.execute_tool(...)
    """
    global _global_tool_use
    if _global_tool_use is None:
        _global_tool_use = ToolUse()
    return _global_tool_use
