"""请求体监视器插件 API。

为插件提供读取、清理当前进程请求体快照和获取相关统计数据的统一入口。
"""

from __future__ import annotations

from typing import Any

API_VERSION = "1.0.0"

__all__ = [
    "API_VERSION",
    "get_request_inspector_snapshot",
    "list_request_inspector_requests",
    "get_request_inspector_request",
    "clear_request_inspector_requests",
    "get_request_inspector_analytics",
    "render_request_inspector_payload",
]


def get_request_inspector_snapshot() -> dict[str, Any]:
    """获取请求监视器的原子快照，包含版本、摘要列表和缓冲统计。"""
    from src.kernel.llm.request_inspector import get_inspector

    return get_inspector().snapshot()


def list_request_inspector_requests() -> list[dict[str, Any]]:
    """获取当前进程捕获的请求摘要列表。"""
    from src.kernel.llm.request_inspector import get_inspector

    return get_inspector().list_summaries()


def get_request_inspector_request(request_id: int) -> dict[str, Any] | None:
    """获取指定捕获请求的完整详情；记录不存在时返回 ``None``。"""
    from src.kernel.llm.request_inspector import get_inspector

    return get_inspector().get_record(request_id)


def clear_request_inspector_requests() -> dict[str, Any]:
    """清空当前进程捕获的请求记录，不会取消正在执行的模型调用。"""
    from src.kernel.llm.request_inspector import get_inspector

    return get_inspector().clear_snapshot()


async def get_request_inspector_analytics() -> dict[str, Any]:
    """获取请求体检视器页面使用的 LLM 统计数据。"""
    from src.kernel.llm.stats import get_llm_stats_collector

    collector = get_llm_stats_collector()
    return {
        "summary": await collector.get_summary(),
        "by_model": await collector.get_by_model(),
        "by_request_name": await collector.get_by_request_name(),
        "by_stream": await collector.get_by_stream(),
    }


def render_request_inspector_payload(
    *,
    api_name: str,
    model: str,
    params: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将请求参数转换为请求体检视器使用的结构化视图。"""
    from src.kernel.llm.request_inspector import build_render_view

    return build_render_view(api_name, model, params, metadata)
