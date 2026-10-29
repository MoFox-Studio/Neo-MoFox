"""Provider-owned replay snapshots, independent of any vendor SDK objects."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def payload_fingerprint(content: list[Any]) -> str:
    """Hash the standard assistant view so edited content cannot reuse signatures."""
    view: list[dict[str, Any]] = []
    for part in content:
        if hasattr(part, "args") and hasattr(part, "name"):
            view.append({"type": "tool", "id": part.id, "name": part.name, "args": part.args})
        elif hasattr(part, "text"):
            kind = type(part).__name__
            # Reducers may coalesce adjacent chunks; hash semantic text, not chunking.
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
    """Immutable, redacted replay data tied to its source and visible content."""

    provider: str
    target: tuple[str, str, str]
    parts_json: str = field(repr=False)
    content_fingerprint: str
    call_ids: tuple[tuple[str, str | None], ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Validate the snapshot shape without exposing its contents in errors."""
        try:
            parts = json.loads(self.parts_json)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid provider replay snapshot") from exc
        if not isinstance(parts, list) or self.schema_version != 1 or len(self.target) != 3:
            raise ValueError("Unsupported provider replay snapshot")

    def matches(self, content: list[Any]) -> bool:
        """Return whether the standard view still matches this snapshot."""
        return self.content_fingerprint == payload_fingerprint(content)
