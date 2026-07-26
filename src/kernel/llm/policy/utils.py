from __future__ import annotations

import math
import random
from typing import Any

from ..exceptions import LLMRateLimitError

MAX_RETRY_DELAY_SECONDS = 60.0


def normalize_max_retry(value: object) -> int:
    try:
        max_retry = int(value) if value is not None else 2
    except (TypeError, ValueError, OverflowError):
        max_retry = 0
    return max(0, max_retry)


def _normalize_retry_interval(value: object) -> float:
    try:
        delay = float(value) if value is not None else 3.0
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(delay) or delay < 0:
        return 0.0
    return delay


def retry_delay(
    *,
    model: dict[str, Any],
    error: BaseException,
    retry_ordinal: int,
) -> float:
    if isinstance(error, LLMRateLimitError):
        retry_after = error.retry_after
        if (
            isinstance(retry_after, (int, float))
            and not isinstance(retry_after, bool)
            and math.isfinite(retry_after)
            and retry_after >= 0
        ):
            return min(float(retry_after), MAX_RETRY_DELAY_SECONDS)

    base_delay = _normalize_retry_interval(model.get("retry_interval"))
    if base_delay == 0:
        return 0.0
    ordinal = max(1, retry_ordinal)
    exponent = min(ordinal - 1, 63)
    upper_bound = min(
        MAX_RETRY_DELAY_SECONDS,
        base_delay * (2.0 ** exponent),
    )
    return random.uniform(0.0, upper_bound)
