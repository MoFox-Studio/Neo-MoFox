"""Event bus 模块的单元测试。

本项目的 Event 协议（硬性约束）：
- 订阅者签名统一为 `(event_name, params)`，其中 `params` 为 `dict[str, Any]`
- 订阅者返回 `(EventDecision, next_params)`
- `next_params` 的 key 集合必须与入参 params 完全一致，否则丢弃该订阅者影响
"""

from __future__ import annotations

import asyncio

import pytest

import src.kernel.event.core as event_core
from src.core.components.types import EventType
from src.kernel.event import EventBus, EventDecision


class TestEventBusBasics:
    def test_event_bus_initialization(self) -> None:
        bus = EventBus(name="test_bus")
        assert bus.name == "test_bus"
        assert bus.event_count == 0
        assert bus.handler_count == 0
        assert len(bus.subscribed_events) == 0

    def test_event_bus_default_name(self) -> None:
        bus = EventBus()
        assert bus.name == "default"

    def test_subscribe_and_unsubscribe(self) -> None:
        bus = EventBus()

        async def handler(event_name: str, params: dict):
            return (EventDecision.SUCCESS, params)

        unsubscribe = bus.subscribe("test_event", handler)
        assert bus.event_count == 1
        assert bus.handler_count == 1
        assert "test_event" in bus.subscribed_events
        assert handler in bus.get_subscribers("test_event")

        unsubscribe()
        assert bus.event_count == 0
        assert bus.handler_count == 0

    def test_unsubscribe_all(self) -> None:
        bus = EventBus()

        async def handler(event_name: str, params: dict):
            return (EventDecision.SUCCESS, params)

        bus.subscribe("a", handler)
        bus.subscribe("b", handler)
        assert bus.handler_count == 2

        removed = bus.unsubscribe_all(handler)
        assert removed == 2
        assert bus.handler_count == 0
        assert bus.event_count == 0

    def test_priority_ordering(self) -> None:
        bus = EventBus()
        seen: list[str] = []

        async def low(event_name: str, params: dict):
            seen.append("low")
            return (EventDecision.SUCCESS, params)

        async def high(event_name: str, params: dict):
            seen.append("high")
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", low, priority=0)
        bus.subscribe("e", high, priority=10)

        # get_subscribers 应按 priority 从高到低
        subs = bus.get_subscribers("e")
        assert subs == [high, low]


class TestEventBusPublish:
    @pytest.mark.asyncio
    async def test_publish_no_subscribers_returns_success_and_copies_params(self) -> None:
        bus = EventBus()
        params = {"x": 1}
        decision, out = await bus.publish("nope", params)
        assert decision == EventDecision.SUCCESS
        assert out == {"x": 1}
        assert out is not params

    @pytest.mark.asyncio
    async def test_publish_valid_chain_success_pass_stop(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        async def step1(event_name: str, params: dict):
            calls.append("step1")
            params["v"] += 1
            return (EventDecision.SUCCESS, params)

        async def step2(event_name: str, params: dict):
            calls.append("step2")
            params["ignored"] = True
            return (EventDecision.PASS, params)

        async def step3(event_name: str, params: dict):
            calls.append("step3")
            params["v"] += 10
            params["stop"] = True
            return (EventDecision.STOP, params)

        async def step4(event_name: str, params: dict):
            calls.append("step4")
            params["v"] += 1000
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", step4, priority=0)
        bus.subscribe("e", step3, priority=10)
        bus.subscribe("e", step2, priority=20)
        bus.subscribe("e", step1, priority=30)

        decision, out = await bus.publish("e", {"v": 0, "ignored": False, "stop": False})
        assert calls == ["step1", "step2", "step3"]
        assert decision == EventDecision.STOP
        assert out["v"] == 11
        assert out["ignored"] is False  # PASS 不更新链式 params
        assert out["stop"] is True

    @pytest.mark.asyncio
    async def test_publish_invalid_return_is_discarded_and_does_not_mutate_chain(self) -> None:
        bus = EventBus()

        def mutating_but_invalid(event_name: str, params: dict):
            params["v"] = 999
            return None  # 非二元组：应丢弃影响

        async def next_handler(event_name: str, params: dict):
            params["v"] += 1
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", mutating_but_invalid, priority=20)   # type: ignore[misc]
        bus.subscribe("e", next_handler, priority=10)

        decision, out = await bus.publish("e", {"v": 0})
        assert decision == EventDecision.SUCCESS
        assert out == {"v": 1}

    @pytest.mark.asyncio
    async def test_publish_invalid_next_params_keys_discarded(self) -> None:
        bus = EventBus()

        async def bad_keys(event_name: str, params: dict):
            return (EventDecision.SUCCESS, {"other": 1})

        async def good(event_name: str, params: dict):
            params["x"] += 1
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", bad_keys, priority=20)
        bus.subscribe("e", good, priority=10)

        decision, out = await bus.publish("e", {"x": 1})
        assert decision == EventDecision.SUCCESS
        assert out == {"x": 2}

    @pytest.mark.asyncio
    async def test_publish_handler_exception_is_ignored(self) -> None:
        bus = EventBus()

        async def boom(event_name: str, params: dict):
            raise RuntimeError("boom")

        async def ok(event_name: str, params: dict):
            params["x"] = 2
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", boom, priority=20)
        bus.subscribe("e", ok, priority=10)

        decision, out = await bus.publish("e", {"x": 1})
        assert decision == EventDecision.SUCCESS
        assert out == {"x": 2}

    @pytest.mark.asyncio
    async def test_publish_handler_timeout_is_ignored(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bus = EventBus()

        async def hung(event_name: str, params: dict):
            await asyncio.Event().wait()
            return (EventDecision.SUCCESS, params)

        async def ok(event_name: str, params: dict):
            params["x"] = 2
            return (EventDecision.SUCCESS, params)

        monkeypatch.setattr(event_core, "EVENT_HANDLER_TIMEOUT_SECONDS", 0.01)

        bus.subscribe("e", hung, priority=20)
        bus.subscribe("e", ok, priority=10)

        decision, out = await bus.publish("e", {"x": 1})
        assert decision == EventDecision.SUCCESS
        assert out == {"x": 2}

    @pytest.mark.asyncio
    async def test_publish_input_validation(self) -> None:
        bus = EventBus()

        with pytest.raises(ValueError, match="事件名称必须是非空字符串"):
            await bus.publish("", {"x": 1})

        with pytest.raises(ValueError, match="params 必须是 dict"):
            await bus.publish("e", None)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="key 必须全部为 str"):
            await bus.publish("e", {1: "x"})  # type: ignore[dict-item]

    @pytest.mark.asyncio
    async def test_publish_accepts_event_type_str_enum(self) -> None:
        bus = EventBus()

        async def handler(event_name: str, params: dict):
            params["handled"] = event_name
            return (EventDecision.SUCCESS, params)

        bus.subscribe(EventType.ON_ALL_PLUGIN_LOADED, handler)

        decision, out = await bus.publish(
            EventType.ON_ALL_PLUGIN_LOADED,
            {"handled": ""},
        )
        assert decision == EventDecision.SUCCESS
        assert out == {"handled": EventType.ON_ALL_PLUGIN_LOADED.value}

    @pytest.mark.asyncio
    async def test_publish_sync_returns_task(self) -> None:
        bus = EventBus()

        async def handler(event_name: str, params: dict):
            params["x"] += 1
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", handler)
        task = bus.publish_sync("e", {"x": 1})
        assert isinstance(task, asyncio.Task)
        decision, out = await task
        assert decision == EventDecision.SUCCESS
        assert out == {"x": 2}

    @pytest.mark.asyncio
    async def test_concurrent_publish_isolated_params(self) -> None:
        bus = EventBus()
        seen: list[int] = []

        async def handler(event_name: str, params: dict):
            # 每次发布都应独立，不应互相串改
            seen.append(params["i"])
            return (EventDecision.SUCCESS, params)

        bus.subscribe("e", handler)
        tasks = [bus.publish("e", {"i": i}) for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert all(decision == EventDecision.SUCCESS for decision, _ in results)
        assert sorted(seen) == list(range(10))
