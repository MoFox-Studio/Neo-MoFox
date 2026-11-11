"""离线演示领域预检与可替换技术能力，不读取运行配置或访问供应商。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# 与相邻内核示例一致，支持从仓库根目录直接运行此文件。
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.core.config.model_config import APIProviderSection, ModelConfig  # noqa: E402
from src.core.config.jev import resolve_jev_config  # noqa: E402
from src.core.managers.llm_runtime import preflight_llm_providers  # noqa: E402
from src.kernel.llm.model_client.runtime import ProviderProbeResult, close_default_model_clients  # noqa: E402


class OfflineProbe:
    """仅验证接口调用，不构造 SDK、连接池或网络资源。"""

    requires_model_entries = False
    requires_provider_entry = False
    allows_anonymous_probe = False

    async def check(
        self, entries: list[dict[str, Any]], timeout: float, *, provider_only: bool = False,
    ) -> list[ProviderProbeResult]:
        """接收技术输入与预算，返回固定非生成结果，无资源关闭责任。"""
        return [ProviderProbeResult("unsupported", "离线示例：未执行真实服务探测")]


def offline_probe(client_type: object) -> OfflineProbe:
    """用同一稳定协议替换技术能力，不让领域调用方了解 SDK 或池。"""
    return OfflineProbe()


async def main() -> None:
    """以内存配置演示调用与默认资源关闭，不读取真实密钥。"""
    config = ModelConfig(api_providers=[APIProviderSection(
        name="offline", api_key="unused-offline-key", client_type="jev",
    )], models=[])
    # 领域桥接只生成公开配置对象，不读取真实配置、不创建网络客户端。
    with patch("src.core.config.get_model_config", lambda: config):
        typed_config = resolve_jev_config(provider_name="offline")
    assert typed_config.trust_env is True
    with patch("src.core.managers.llm_runtime.get_provider_probe", offline_probe):
        results = await preflight_llm_providers(config, timeout=1, trust_env=False)
    for result in results:
        print(result.status, result.detail)
    # 此入口不创建默认实例，也不会回收外部注入客户端；本例没有活动默认资源。
    await close_default_model_clients()


if __name__ == "__main__":
    asyncio.run(main())
