"""在项目根目录运行 python -m examples.src.kernel.concurrency.cleanup_example。"""

import asyncio
from contextlib import suppress

from src.kernel.concurrency.cleanup import await_cancellation_safe


async def main() -> None:
    """取消调用方后，仍等待已经开始的资源操作完成。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def release_resource() -> None:
        """模拟异步回滚或连接关闭。"""
        nonlocal finished
        entered.set()
        await release.wait()
        finished = True

    caller = asyncio.create_task(await_cancellation_safe(release_resource()))
    await entered.wait()
    caller.cancel()
    release.set()
    with suppress(asyncio.CancelledError):
        await caller
    assert finished
    print("资源操作已完成，调用方取消已正常传播")


if __name__ == "__main__":
    asyncio.run(main())
