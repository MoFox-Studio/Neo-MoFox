"""使用真实 HTTPX 验证 Jev transport 的调用方所有权及内部回收。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from src.kernel.concurrency import get_task_manager
from src.kernel.jev import (
    JevAuthenticationError, JevClient, JevClientConfig, JevResponse,
    JevResponseError, JevTransportError, noul,
)


class CountingTransport(httpx.AsyncBaseTransport):
    """记录关闭次数并在关闭后拒绝请求的离线传输；Event 固定并发时序。"""

    def __init__(self, *, mode: str = "success", concurrent: bool = False) -> None:
        """按模式和并发门控参数初始化计数器及事件，无返回值。"""
        self.mode = mode
        self.concurrent = concurrent
        self.calls = 0
        self.closed = 0
        self.entered = asyncio.Event()
        self.both_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """处理离线请求并返回固定响应；关闭后或错误模式按约定抛出异常。"""
        if self.closed:
            raise RuntimeError("transport already closed")
        self.calls += 1
        self.entered.set()
        if self.calls >= 2:
            self.both_entered.set()
        if self.concurrent or self.mode == "blocked":
            await self.release.wait()
        if self.mode == "network":
            raise httpx.ConnectError("offline", request=request)
        if self.mode == "http":
            return httpx.Response(401, json={"error": "offline rejected"})
        if self.mode == "malformed":
            return httpx.Response(200, json={"answers": []})
        return httpx.Response(200, json={
            "model": "jev-offline", "answers": {"q": {"type": "noul", "noul": 0.5}},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    async def aclose(self) -> None:
        """标记底层关闭；后续任何请求都应拒绝。"""
        self.closed += 1


def _client(transport: httpx.AsyncBaseTransport | None = None) -> JevClient:
    """以假密钥和零重试构造客户端；传输由调用者可选注入。"""
    return JevClient(JevClientConfig(
        api_key="fake-key", base_url="https://offline.invalid", max_retries=0,
        trust_env=False,
    ), transport=transport)


async def _evaluate(client: JevClient) -> JevResponse:
    """对给定客户端执行固定问题，返回真实解析的响应。"""
    return await client.evaluate(state="offline", questions={"q": noul("ok?")})


async def test_injected_transport_survives_two_evaluations() -> None:
    """两次真实 HTTPX 上下文不关闭借用传输，只有调用者最终关闭一次。"""
    transport = CountingTransport()
    client = _client(transport)
    try:
        assert (await _evaluate(client)).answer("q").noul == 0.5
        assert (await _evaluate(client)).answer("q").noul == 0.5
        assert transport.calls == 2
        assert transport.closed == 0
    finally:
        await transport.aclose()
    assert transport.closed == 1
    with pytest.raises(RuntimeError, match="already closed"):
        await _evaluate(client)


@pytest.mark.parametrize("mode,expected", [
    ("network", JevTransportError), ("http", JevAuthenticationError),
    ("malformed", JevResponseError),
])
async def test_injected_transport_survives_failure(mode: str, expected: type[Exception]) -> None:
    """网络、HTTP 和解析失败均不回收外部传输，失败后仍可再次请求。"""
    transport = CountingTransport(mode=mode)
    client = _client(transport)
    try:
        with pytest.raises(expected):
            await _evaluate(client)
        assert transport.closed == 0
        transport.mode = "success"
        assert (await _evaluate(client)).answer("q").noul == 0.5
        assert transport.closed == 0
    finally:
        await transport.aclose()


async def test_injected_transport_survives_cancellation() -> None:
    """Event 确认请求进入后取消；控制异常传播且借用传输可继续使用。"""
    transport = CountingTransport(mode="blocked")
    client = _client(transport)
    info = get_task_manager().create_task(_evaluate(client), name="jev-borrowed-cancel")
    task = info.task
    assert task is not None
    try:
        await asyncio.wait_for(transport.entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.closed == 0
        transport.mode = "success"
        assert (await _evaluate(client)).answer("q").noul == 0.5
        assert transport.closed == 0
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await transport.aclose()


async def test_two_clients_share_borrowed_transport_concurrently() -> None:
    """两个客户端同时借用同一底层传输，任一上下文退出均不关闭它。"""
    transport = CountingTransport(concurrent=True)
    clients = [_client(transport), _client(transport)]
    tasks = [get_task_manager().create_task(
        _evaluate(client), name=f"jev-borrowed-shared-{index}",
    ).task for index, client in enumerate(clients)]
    assert all(task is not None for task in tasks)
    try:
        await asyncio.wait_for(transport.both_entered.wait(), timeout=5)
        transport.release.set()
        results = await asyncio.gather(*tasks)
        assert all(result.answer("q").noul == 0.5 for result in results)
        assert transport.closed == 0
        assert (await _evaluate(clients[0])).answer("q").noul == 0.5
        assert transport.calls == 3
        assert transport.closed == 0
    finally:
        transport.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await transport.aclose()


@pytest.mark.parametrize("mode", ["success", "http", "network", "malformed"])
async def test_internally_created_transport_is_closed(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    """内部构造的传输仍由真实 HTTPX 上下文在成功或失败后回收。"""
    created: list[CountingTransport] = []

    def build(**kwargs: Any) -> httpx.AsyncBaseTransport:
        """模拟网络工厂并记录内部传输，返回一个新的离线实例。"""
        transport = CountingTransport(mode=mode)
        created.append(transport)
        return transport

    monkeypatch.setattr("src.kernel.jev.client.build_httpx_transport", build)
    client = _client()
    if mode == "success":
        await _evaluate(client)
        await _evaluate(client)
    else:
        expected = {"http": JevAuthenticationError, "network": JevTransportError,
                    "malformed": JevResponseError}[mode]
        with pytest.raises(expected):
            await _evaluate(client)
    assert len(created) == (2 if mode == "success" else 1)
    assert all(transport.closed == 1 for transport in created)


async def test_internally_created_transport_closes_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内部拥有的传输在 Event 固定的请求取消后仍由 HTTPX 关闭一次。"""
    transport = CountingTransport(mode="blocked")

    def build(**kwargs: Any) -> httpx.AsyncBaseTransport:
        """返回本次请求内部拥有的离线传输，无需触碰实际网络。"""
        return transport

    monkeypatch.setattr("src.kernel.jev.client.build_httpx_transport", build)
    info = get_task_manager().create_task(_evaluate(_client()), name="jev-owned-cancel")
    task = info.task
    assert task is not None
    try:
        await asyncio.wait_for(transport.entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.closed == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
