"""LayerSplit 分阶段检索路由。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LayerSplitStage(StrEnum):
    """记忆处理阶段。"""

    CUE = "CUE"
    ASSOCIATION = "ASSOCIATION"
    RECONSTRUCTION = "RECONSTRUCTION"
    CONFIRMATION = "CONFIRMATION"


@dataclass(frozen=True, slots=True)
class LayerSplitDecision:
    """一次分阶段路由结果。"""

    stage: LayerSplitStage
    candidate_ids: tuple[str, ...]
    budget: int


class LayerSplitRouter:
    """把候选处理拆成线索、关联、重构和确认四个明确阶段。"""

    def __init__(self, *, association_budget: int = 16, reconstruction_budget: int = 5) -> None:
        """创建有界路由器。"""
        if association_budget <= 0 or reconstruction_budget <= 0:
            raise ValueError("LayerSplit budget 必须大于 0")
        self.association_budget = association_budget
        self.reconstruction_budget = reconstruction_budget

    def route(self, stage: LayerSplitStage, candidate_ids: tuple[str, ...]) -> LayerSplitDecision:
        """按阶段截断候选，不在路由器内修改来源事实。"""
        unique = tuple(dict.fromkeys(item for item in candidate_ids if item.strip()))
        budget = self.reconstruction_budget if stage is LayerSplitStage.RECONSTRUCTION else self.association_budget
        return LayerSplitDecision(stage, unique[:budget], budget)
