"""流消费内部的关闭结果与主异常仲裁，不管理请求或后台生命周期。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CloseOutcome:
    """记录关闭故障及本次 await 是否实际收到新的控制异常。"""

    error: BaseException | None = None
    fresh_control: bool = False


@runtime_checkable
class _CloseResultSource(Protocol):
    """仅供受管流识别缓存控制异常的内部显式契约。"""

    async def _close_outcome(self) -> CloseOutcome:
        """等待一次真实关闭；缓存重放必须令 fresh_control 为 False。"""
        ...


async def close_outcome(resource: Any) -> CloseOutcome:
    """关闭一个资源并记录本次异常来源，兼容只有 aclose 的传统源。"""
    try:
        if isinstance(resource, _CloseResultSource):
            return await resource._close_outcome()
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
    except BaseException as error:
        return CloseOutcome(error, not isinstance(error, Exception))
    return CloseOutcome()


def select_close_error(primary: BaseException | None, outcome: CloseOutcome) -> BaseException | None:
    """保留真实主错及新控制流；生成器正常放弃不能掩盖唯一关闭故障。"""
    if outcome.error is None:
        return primary
    if outcome.fresh_control:
        return outcome.error
    if primary is None or isinstance(primary, GeneratorExit):
        return outcome.error
    if primary is not outcome.error:
        primary.add_note("LLM stream cleanup failed: stage=close; error_type="
                         + type(outcome.error).__name__ + "; handoff_blocked=true")
    return primary


@asynccontextmanager
async def closing_iterator(iterator: Any) -> AsyncIterator[Any]:
    """为嵌套生成器关闭提供同一主错仲裁，避免 contextlib finally 覆盖。"""
    primary: BaseException | None = None
    try:
        yield iterator
    except BaseException as error:
        primary = error
        raise
    finally:
        selected = select_close_error(primary, await close_outcome(iterator))
        if selected is not None and selected is not primary:
            raise selected
