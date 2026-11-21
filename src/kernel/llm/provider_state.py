"""保存供应商续接快照，不依赖任何供应商 SDK 对象。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def payload_fingerprint(content: list[Any]) -> str:
    """对标准助手内容 content 计算指纹，禁止编辑后的内容复用原签名。"""
    view: list[dict[str, Any]] = []
    for part in content:
        if hasattr(part, "args") and hasattr(part, "name"):
            view.append({"type": "tool", "id": part.id, "name": part.name, "args": part.args})
        elif hasattr(part, "text"):
            kind = type(part).__name__
            # 归并器可能合并相邻分片；按语义文本计算指纹，而非绑定分片方式。
            if view and kind == "ReasoningText" and view[-1].get("type") == kind:
                view[-1]["text"] += part.text
            else:
                view.append({"type": kind, "text": part.text})
        else:
            view.append({"type": type(part).__name__, "value": getattr(part, "value", None)})
    raw = json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderReplayState:
    """绑定来源和可见内容的不可变续接数据，原始 Parts 不进入 repr 输出。"""

    provider: str
    target: tuple[str, str, str]
    parts_json: str = field(repr=False)
    content_fingerprint: str
    call_ids: tuple[tuple[str, str | None], ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        """验证快照形状，错误信息不暴露 Parts 或签名内容。"""
        try:
            parts = json.loads(self.parts_json)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid provider replay snapshot") from exc
        if not isinstance(parts, list) or self.schema_version != 1 or len(self.target) != 3:
            raise ValueError("Unsupported provider replay snapshot")

    def matches(self, content: list[Any]) -> bool:
        """判断标准内容 content 是否仍与当前快照指纹一致。"""
        return self.content_fingerprint == payload_fingerprint(content)
