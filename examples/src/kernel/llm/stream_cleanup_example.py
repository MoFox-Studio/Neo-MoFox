"""离线演示公开响应在业务失败与关闭失败叠加时保留原业务异常。

从仓库根运行：.venv/Scripts/python.exe -B -X utf8 examples/src/kernel/llm/stream_cleanup_example.py
仅使用内存模型流，不创建供应商客户端或访问配置、网络、密钥。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
from typing import Any, AsyncIterator

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from src.kernel.llm.model_client.base import StreamEvent  # noqa: E402
from src.kernel.llm.policy.base import ModelStep  # noqa: E402
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream  # noqa: E402
from src.kernel.llm.response import LLMResponse  # noqa: E402


class OfflineSource:
    """只提供一块内存文本，关闭时模拟普通资源故障。"""

    def __aiter__(self) -> AsyncIterator[StreamEvent]:
        """返回有限事件生成器。"""
        return self.events()

    async def events(self) -> AsyncIterator[StreamEvent]:
        """发出不会触发任何网络请求的文本。"""
        yield StreamEvent(text_delta="offline")

    async def aclose(self) -> None:
        """模拟资源关闭失败，不携带供应商原文。"""
        raise RuntimeError("offline close failure")


class OfflinePolicySession:
    """该示例不允许重试，只暴露所需的策略会话方法。"""

    def first(self) -> ModelStep:
        """返回本地占位步骤，不发出请求。"""
        return ModelStep(model=None)

    def next_after_error(self, error: BaseException) -> ModelStep:
        """业务 callback 不应触发策略推进。"""
        raise AssertionError("callback 不得推进 provider 策略")

    def record_success(self, *, latency: float = 0.0, tokens: int = 0) -> None:
        """示例不记录外部统计。"""
        return None


async def main() -> None:
    """公开 callback 抛错时，响应清理后仍得到同一个业务异常。"""
    source = OfflineSource()

    async def terminal(
        error: BaseException | None, usage: dict[str, Any] | None,
        latency: float, phase: str,
    ) -> None:
        """示例不输出终态原文或任何不透明业务字段。"""
        return None

    async def resume(context: AttemptContext) -> Any:
        """该示例不得创建替代请求。"""
        raise AssertionError("不得重试")

    context = AttemptContext(session=OfflinePolicySession(), step=ModelStep(model=None))
    # 将有限生成器与其显式关闭责任绑定到一个对象，便于示例观察。
    iterator = source.events()

    class OwnedSource:
        """同时转发读取和显式关闭的最小内存源。"""

        def __aiter__(self) -> OwnedSource:
            """返回当前源。"""
            return self

        async def __anext__(self) -> StreamEvent:
            """委托有限生成器。"""
            return await anext(iterator)

        async def aclose(self) -> None:
            """结束生成器后模拟关闭失败。"""
            await iterator.aclose()
            await source.aclose()

    stream = ManagedStream(
        OwnedSource(), timeout=None, started_at=time.perf_counter(),
        context=context, terminal=terminal, resume=resume,
    )
    response = LLMResponse(
        _stream=stream, _upper=None, _auto_append_response=False,
        payloads=[], model_set=[], context_manager=None,
    )
    primary = ValueError("offline callback failure")

    async def callback(text: str) -> None:
        """模拟调用方失败，不输出文本。"""
        raise primary

    try:
        await response.stream_with_callback(callback)
    except ValueError as error:
        assert error is primary
        print("已保留业务主异常；关闭失败仅作为安全次要诊断。")
    else:
        raise AssertionError("预期业务异常未传播")


if __name__ == "__main__":
    asyncio.run(main())
