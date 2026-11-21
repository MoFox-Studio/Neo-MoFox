"""供应商非生成探测与默认自有资源关闭的稳定技术入口。"""
from __future__ import annotations

from typing import Any

import httpx

from . import gemini_client
from .probe_contract import ProviderProbe, ProviderProbeResult
from .registry import resolve_client_type


class _LegacyModelsProbe:
    """保留既有 Bearer /models GET 行为，不宣称支持生成或增加 SDK。"""

    requires_model_entries = False
    allows_anonymous_probe = True

    async def check(
        self, entries: list[dict[str, Any]], timeout: float, *, provider_only: bool = False,
    ) -> list[ProviderProbeResult]:
        """逐项返回 HTTP 状态；4xx/5xx 与旧日志一样不是请求异常。"""
        results: list[ProviderProbeResult] = []
        for entry in entries:
            try:
                key = entry.get("api_key") or ""
                headers = {"Authorization": f"Bearer {key}"} if key else {}
                extra = entry.get("extra_params") or {}
                trust = bool(extra.get("trust_env", True))
                async with httpx.AsyncClient(timeout=timeout, trust_env=trust) as client:
                    response = await client.get(str(entry.get("base_url", "")).rstrip("/") + "/models", headers=headers)
                    try:
                        status = response.status_code
                    finally:
                        await response.aclose()
                results.append(ProviderProbeResult("available", f"HTTP {status}（非生成检查）", groups=1))
            except Exception as error:
                results.append(ProviderProbeResult("unverified", f"HTTP 预检失败（{type(error).__name__}），未验证"))
        return results


def get_provider_probe(client_type: object) -> ProviderProbe:
    """按已识别的客户端类型选择探测能力。

    Args:
        client_type: 原始类型值，与请求分发使用同一套类型和别名。

    Returns:
        Gemini 原生探测或既有 /models 探测，不创建网络资源。

    Raises:
        LLMProviderUnavailableError: 类型无效或不受支持，不能选择探测器。
    """
    if resolve_client_type(client_type) == "gemini":
        return gemini_client.GeminiProviderProbe()
    return _LegacyModelsProbe()


async def close_default_model_clients() -> None:
    """仅回收明确自有的默认资源，不创建默认实例、不遍历注入注册表。

    当前只委托旧 Gemini 关闭入口；历史 OpenAI/Anthropic 缓存不因此纳入回收。
    """
    await gemini_client.close_gemini_clients()
