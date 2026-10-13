"""可训练的 Mnemosyne 记忆控制器。

该模块只学习候选记忆的激活、冲突与巩固分数，不保存事实正文。
PyTorch 是可选依赖；未安装时本模块保持可导入，并在构造控制器时给出明确错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import torch
    from torch import Tensor, nn
except ImportError:  # pragma: no cover - exercised in environments without torch
    torch = None  # type: ignore[assignment]
    Tensor = Any  # type: ignore[misc,assignment]

    class _MissingTorch:
        """占位命名空间，让类型和模块导入在可选依赖缺失时保持安全。"""

        class Module:  # noqa: D101
            pass

    nn = _MissingTorch()  # type: ignore[assignment]


MODEL_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class MemoryControllerOutput:
    """Transformer 控制器的一次结构化输出。"""

    recall_logits: Tensor
    conflict_logits: Tensor
    consolidation_logits: Tensor

    def probabilities(self) -> dict[str, Tensor]:
        """返回三个决策头的 sigmoid 概率。"""
        _require_torch()
        return {
            "recall": torch.sigmoid(self.recall_logits),
            "conflict": torch.sigmoid(self.conflict_logits),
            "consolidation": torch.sigmoid(self.consolidation_logits),
        }


if torch is not None:

    class TrainableMemoryTransformer(nn.Module):
        """作用于查询与候选槽位的真正可训练 Transformer Encoder。"""

        def __init__(
            self,
            *,
            input_dim: int = 128,
            d_model: int = 192,
            nhead: int = 6,
            num_layers: int = 3,
            dim_feedforward: int = 768,
            dropout: float = 0.1,
            max_candidates: int = 32,
        ) -> None:
            """创建控制器，不执行数据库或模型 I/O。"""
            super().__init__()
            if input_dim <= 0 or d_model <= 0 or max_candidates <= 0:
                raise ValueError("input_dim、d_model 和 max_candidates 必须大于 0")
            if d_model % nhead != 0:
                raise ValueError("d_model 必须能被 nhead 整除")
            if num_layers <= 0 or dim_feedforward <= 0:
                raise ValueError("Transformer 层数和 FFN 宽度必须大于 0")
            self.input_dim = input_dim
            self.d_model = d_model
            self.nhead = nhead
            self.num_layers = num_layers
            self.dim_feedforward = dim_feedforward
            self.dropout = dropout
            self.max_candidates = max_candidates
            self.input_projection = nn.Linear(input_dim, d_model)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=dim_feedforward,
                dropout=dropout,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.query_norm = nn.LayerNorm(d_model)
            self.recall_head = nn.Linear(d_model, 1)
            self.conflict_head = nn.Linear(d_model, 1)
            self.consolidation_head = nn.Linear(d_model, 1)

        def forward(
            self,
            features: Tensor,
            *,
            candidate_mask: Tensor | None = None,
        ) -> MemoryControllerOutput:
            """对 `[query, candidate...]` 特征序列输出三个决策头。"""
            if features.ndim != 3:
                raise ValueError("features 必须是 [batch, sequence, input_dim]")
            if features.shape[-1] != self.input_dim:
                raise ValueError("features 最后一维与 input_dim 不一致")
            if features.shape[1] != self.max_candidates + 1:
                raise ValueError("sequence 必须包含一个 query 和 max_candidates 个候选")
            padding_mask = None
            if candidate_mask is not None:
                expected_mask_shape = (features.shape[0], self.max_candidates)
                if tuple(candidate_mask.shape) != expected_mask_shape:
                    raise ValueError("candidate_mask 必须是 [batch, max_candidates]")
                if candidate_mask.dtype is not torch.bool:
                    candidate_mask = candidate_mask.to(dtype=torch.bool)
                query_padding = torch.zeros(
                    (candidate_mask.shape[0], 1),
                    dtype=torch.bool,
                    device=candidate_mask.device,
                )
                padding_mask = torch.cat((query_padding, ~candidate_mask), dim=1)
            encoded = self.encoder(
                self.input_projection(features),
                src_key_padding_mask=padding_mask,
            )
            query = self.query_norm(encoded[:, :1, :])
            candidates = encoded[:, 1:, :] + query
            return MemoryControllerOutput(
                recall_logits=self.recall_head(candidates).squeeze(-1),
                conflict_logits=self.conflict_head(candidates).squeeze(-1),
                consolidation_logits=self.consolidation_head(candidates).squeeze(-1),
            )

        def architecture(self) -> dict[str, int | float]:
            """返回可写入数据库的结构元数据。"""
            return {
                "input_dim": self.input_dim,
                "d_model": self.d_model,
                "nhead": self.nhead,
                "num_layers": self.num_layers,
                "dim_feedforward": self.dim_feedforward,
                "dropout": self.dropout,
                "max_candidates": self.max_candidates,
            }

else:

    class TrainableMemoryTransformer:  # type: ignore[no-redef]
        """PyTorch 缺失时的明确失败占位。"""

        def __init__(self, **kwargs: object) -> None:
            """提示安装可选训练依赖。"""
            del kwargs
            _require_torch()


def _require_torch() -> None:
    """确保调用训练或推理前已经安装 PyTorch。"""
    if torch is None:
        raise RuntimeError(
            "Mnemosyne learning requires PyTorch; install the optional "
            "dependency with `uv pip install torch`"
        )


__all__ = [
    "MODEL_FORMAT_VERSION",
    "MemoryControllerOutput",
    "TrainableMemoryTransformer",
]
