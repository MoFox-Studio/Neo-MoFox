"""非生成供应商探测的轻量值与能力契约，不导入任何适配器或领域配置。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class ProviderProbeResult:
    """仅携带安全诊断、覆盖计数与从 1 开始的定位序号。

    前四个字段保持位置构造兼容。entry_index 定位无效输入条目，group_index
    定位去重后的传输组；不携带配置、凭据、SDK 原文或网络资源。
    """

    status: Literal["available", "unverified", "unsupported"]
    detail: str
    covered_models: int = 0
    groups: int = 0
    entry_index: int | None = None
    group_index: int | None = None


class ProviderProbe(Protocol):
    """只接收合并后的技术条目，通过显式能力声明输入需求。"""

    requires_model_entries: bool
    requires_provider_entry: bool
    allows_anonymous_probe: bool

    async def check(
        self, entries: list[dict[str, Any]], timeout: float, *, provider_only: bool = False,
    ) -> list[ProviderProbeResult]:
        """执行非生成探测，普通失败返回安全结果，外部取消原样传播。

        Args:
            entries: 调用方按能力要求准备的技术条目，不从此处读取领域配置。
            timeout: 单组探测时间上限，单位为秒。
            provider_only: 是否仅检查默认传输，不计入模型覆盖数。

        Returns:
            仅含安全状态、诊断和计数的结果；返回前等待自有临时资源关闭。
            不支持探测的实现不访问条目，也不创建网络资源。
        """
        ...
