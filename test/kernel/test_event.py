"""Event bus 模块的单元测试。

测试覆盖Event类和EventBus类的所有功能。
"""

import asyncio

import pytest

from src.kernel.event import Event, EventBus, EventDecision


class TestEvent:
    """测试Event类。"""

    def test_event_creation_with_name_only(self):
        """测试仅使用名称创建事件。"""
        event = Event(name="test_event")
        assert event.name == "test_event"
        assert event.data is None
        assert event.source is None

    def test_event_creation_with_data(self):
        """测试创建带有数据的事件。"""
        data = {"user_id": "123", "action": "login"}
        event = Event(name="user_login", data=data)
        assert event.name == "user_login"
        assert event.data == data
        assert event.source is None

    def test_event_creation_with_source(self):
        """测试创建带有源标识的事件。"""
        event = Event(name="test", data="value", source="plugin_a")
        assert event.name == "test"
        assert event.data == "value"
        assert event.source == "plugin_a"

    def test_event_with_empty_name_raises_error(self):
        """测试空事件名称引发错误。"""
        with pytest.raises(ValueError, match="事件名称必须是非空字符串"):
            Event(name="")

    def test_event_with_none_name_raises_error(self):
        """测试None事件名称引发错误。"""
        with pytest.raises(ValueError, match="事件名称必须是非空字符串"):
            Event(name=None)

    def test_event_with_non_string_name_raises_error(self):
        """测试非字符串事件名称引发错误。"""
        with pytest.raises(ValueError, match="事件名称必须是非空字符串"):
            Event(name=123)


class TestEventBus:
    """测试EventBus类。"""

    def test_event_bus_initialization(self):
        """测试事件总线初始化。"""
        bus = EventBus(name="test_bus")
        assert bus.name == "test_bus"
        assert bus.event_count == 0
        assert bus.handler_count == 0
        assert len(bus.subscribed_events) == 0

    def test_event_bus_default_name(self):
        """测试默认名称的事件总线。"""
        bus = EventBus()
        assert bus.name == "default"

    def test_subscribe_handler(self):
        """测试订阅处理器。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        unsubscribe = bus.subscribe("test_event", handler)
        assert bus.event_count == 1
        assert bus.handler_count == 1
        assert "test_event" in bus.subscribed_events
        assert handler in bus.get_subscribers("test_event")
        assert callable(unsubscribe)

    def test_subscribe_multiple_handlers(self):
        """测试订阅多个处理器到同一事件。"""
        bus = EventBus()

        async def handler1(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        async def handler2(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler1)
        bus.subscribe("test_event", handler2)

        assert bus.event_count == 1
        assert bus.handler_count == 2
        assert len(bus.get_subscribers("test_event")) == 2

    def test_subscribe_multiple_events(self):
        """测试订阅多个不同事件。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("event1", handler)
        bus.subscribe("event2", handler)

        assert bus.event_count == 2
        assert bus.handler_count == 2

    @pytest.mark.asyncio
    async def test_publish_event(self):
        """测试发布事件。"""
        bus = EventBus()
        received_events = []

        async def handler(event: Event, shared):
            received_events.append(event)
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler)
        decision, shared = await bus.publish(Event(name="test_event", data="test"), shared={})

        assert decision == EventDecision.SUCCESS
        assert shared == {}
        assert len(received_events) == 1
        assert received_events[0].name == "test_event"
        assert received_events[0].data == "test"

    @pytest.mark.asyncio
    async def test_publish_to_multiple_handlers(self):
        """测试向多个处理器发布事件。"""
        bus = EventBus()
        results = []

        async def handler1(event: Event, shared):
            results.append("handler1")
            return (EventDecision.SUCCESS, shared)

        async def handler2(event: Event, shared):
            results.append("handler2")
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler1)
        bus.subscribe("test_event", handler2)

        decision, shared = await bus.publish(Event(name="test_event"), shared=None)

        assert decision == EventDecision.SUCCESS
        assert shared is None
        assert results == ["handler1", "handler2"]

    @pytest.mark.asyncio
    async def test_publish_event_no_subscribers(self):
        """测试发布到没有订阅者的事件。"""
        bus = EventBus()
        decision, shared = await bus.publish(Event(name="nonexistent"), shared={"x": 1})
        assert decision == EventDecision.SUCCESS
        assert shared == {"x": 1}

    @pytest.mark.asyncio
    async def test_publish_with_sync_handler(self):
        """测试使用同步处理器发布事件。"""
        bus = EventBus()
        results = []

        def sync_handler(event: Event, shared):
            results.append(event.name)
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", sync_handler)
        decision, shared = await bus.publish(Event(name="test_event"), shared=None)

        assert decision == EventDecision.SUCCESS
        assert shared is None
        assert len(results) == 1
        assert results[0] == "test_event"

    @pytest.mark.asyncio
    async def test_publish_with_mixed_handlers(self):
        """测试使用混合的同步和异步处理器发布事件。"""
        bus = EventBus()
        results = []

        def sync_handler(event: Event, shared):
            results.append("sync")
            return (EventDecision.SUCCESS, shared)

        async def async_handler(event: Event, shared):
            results.append("async")
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", sync_handler)
        bus.subscribe("test_event", async_handler)

        decision, shared = await bus.publish(Event(name="test_event"), shared=None)

        assert decision == EventDecision.SUCCESS
        assert shared is None
        assert len(results) == 2
        assert "sync" in results
        assert "async" in results

    def test_unsubscribe_handler(self):
        """测试取消订阅处理器。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler)
        assert bus.handler_count == 1

        result = bus.unsubscribe("test_event", handler)
        assert result is True
        assert bus.handler_count == 0
        assert bus.event_count == 0

    def test_unsubscribe_nonexistent_event(self):
        """测试从不存在的事件取消订阅。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        result = bus.unsubscribe("nonexistent", handler)
        assert result is False

    def test_unsubscribe_nonexistent_handler(self):
        """测试取消订阅不存在的处理器。"""
        bus = EventBus()

        async def handler1(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        async def handler2(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler1)
        result = bus.unsubscribe("test_event", handler2)

        assert result is False
        assert bus.handler_count == 1

    def test_unsubscribe_all(self):
        """测试从所有事件取消订阅处理器。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("event1", handler)
        bus.subscribe("event2", handler)
        bus.subscribe("event3", handler)

        assert bus.handler_count == 3

        count = bus.unsubscribe_all(handler)
        assert count == 3
        assert bus.handler_count == 0
        assert bus.event_count == 0

    def test_unsubscribe_all_no_subscriptions(self):
        """测试取消订阅没有订阅的处理器。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        count = bus.unsubscribe_all(handler)
        assert count == 0

    def test_unsubscribe_function(self):
        """测试使用返回的取消订阅函数。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        unsubscribe = bus.subscribe("test_event", handler)
        assert bus.handler_count == 1

        unsubscribe()
        assert bus.handler_count == 0

    @pytest.mark.asyncio
    async def test_handler_exception_is_logged(self):
        """测试处理器异常被记录但不阻止其他处理器。"""
        bus = EventBus()
        results = []

        async def failing_handler(event: Event, shared):
            raise RuntimeError("Test error")

        async def working_handler(event: Event, shared):
            results.append("success")
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", failing_handler)
        bus.subscribe("test_event", working_handler)

        decision, shared = await bus.publish(Event(name="test_event"), shared={})

        # 即使一个失败了，也不阻止后续处理器
        assert decision == EventDecision.SUCCESS
        assert shared == {}
        assert len(results) == 1
        assert results[0] == "success"

    @pytest.mark.asyncio
    async def test_publish_sync(self):
        """测试同步发布方法。"""
        bus = EventBus()
        results = []

        async def handler(event: Event, shared):
            results.append(event.name)
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler)
        task = bus.publish_sync(Event(name="test_event"))

        assert isinstance(task, asyncio.Task)
        # 等待任务完成
        decision, shared = await task
        assert decision == EventDecision.SUCCESS
        assert shared is None
        assert len(results) == 1

    def test_clear_subscriptions(self):
        """测试清除所有订阅。"""
        bus = EventBus()

        async def handler1(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        async def handler2(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("event1", handler1)
        bus.subscribe("event2", handler2)

        assert bus.event_count == 2
        assert bus.handler_count == 2

        bus.clear()

        assert bus.event_count == 0
        assert bus.handler_count == 0

    def test_get_subscribers(self):
        """测试获取事件的订阅者列表。"""
        bus = EventBus()

        async def handler1(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        async def handler2(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler1)
        bus.subscribe("test_event", handler2)

        subscribers = bus.get_subscribers("test_event")
        assert len(subscribers) == 2
        assert handler1 in subscribers
        assert handler2 in subscribers

    def test_get_subscribers_nonexistent_event(self):
        """测试获取不存在事件的订阅者。"""
        bus = EventBus()
        subscribers = bus.get_subscribers("nonexistent")
        assert len(subscribers) == 0

    def test_event_count_property(self):
        """测试事件数量属性。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        assert bus.event_count == 0

        bus.subscribe("event1", handler)
        assert bus.event_count == 1

        bus.subscribe("event2", handler)
        assert bus.event_count == 2

    def test_handler_count_property(self):
        """测试处理器数量属性。"""
        bus = EventBus()

        async def handler1(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        async def handler2(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        assert bus.handler_count == 0

        bus.subscribe("event1", handler1)
        assert bus.handler_count == 1

        bus.subscribe("event1", handler2)
        assert bus.handler_count == 2

        bus.subscribe("event2", handler1)
        assert bus.handler_count == 3

    def test_subscribed_events_property(self):
        """测试已订阅事件属性。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("event1", handler)
        bus.subscribe("event2", handler)
        bus.subscribe("event3", handler)

        events = bus.subscribed_events
        assert len(events) == 3
        assert "event1" in events
        assert "event2" in events
        assert "event3" in events

    def test_repr(self):
        """测试字符串表示。"""
        bus = EventBus(name="test_bus")

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("event1", handler)
        bus.subscribe("event2", handler)

        repr_str = repr(bus)
        assert "EventBus" in repr_str
        assert "test_bus" in repr_str
        assert "events=2" in repr_str
        assert "handlers=2" in repr_str

    def test_subscribe_empty_event_name_raises_error(self):
        """测试订阅空事件名称引发错误。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        with pytest.raises(ValueError, match="事件名称不能为空"):
            bus.subscribe("", handler)

    def test_subscribe_non_callable_raises_error(self):
        """测试订阅不可调用对象引发错误。"""
        bus = EventBus()

        with pytest.raises(ValueError, match="处理器必须是可调用对象"):
            bus.subscribe("test_event", "not_callable")

    @pytest.mark.asyncio
    async def test_publish_non_event_raises_error(self):
        """测试发布非Event对象引发错误。"""
        bus = EventBus()

        with pytest.raises(ValueError, match="必须发布Event实例"):
            await bus.publish("not_an_event")

    @pytest.mark.asyncio
    async def test_event_data_passed_correctly(self):
        """测试事件数据正确传递。"""
        bus = EventBus()
        received_data = []

        async def handler(event: Event, shared):
            received_data.append(event.data)
            return (EventDecision.SUCCESS, shared)

        test_data = {"key": "value", "number": 123}
        bus.subscribe("test_event", handler)
        await bus.publish(Event(name="test_event", data=test_data), shared=None)

        assert len(received_data) == 1
        assert received_data[0] == test_data

    @pytest.mark.asyncio
    async def test_event_source_passed_correctly(self):
        """测试事件源正确传递。"""
        bus = EventBus()
        received_sources = []

        async def handler(event: Event, shared):
            received_sources.append(event.source)
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler)
        await bus.publish(
            Event(name="test_event", data="test", source="test_plugin")
        )

        assert len(received_sources) == 1
        assert received_sources[0] == "test_plugin"

    @pytest.mark.asyncio
    async def test_handler_can_return_value(self):
        """测试处理器可以返回值。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return "result"  # 兼容旧式返回值

        bus.subscribe("test_event", handler)
        # 即使处理器返回非二元组，publish 也应该正常工作
        decision, shared = await bus.publish(Event(name="test_event"), shared={"x": 1})
        assert decision == EventDecision.SUCCESS
        assert shared == {"x": 1}

    @pytest.mark.asyncio
    async def test_handler_can_be_coroutine_function(self):
        """测试处理器可以是协程函数。"""
        bus = EventBus()
        results = []

        async def async_handler(event: Event, shared):
            await asyncio.sleep(0.01)
            results.append("async_result")
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", async_handler)
        decision, shared = await bus.publish(Event(name="test_event"), shared=None)

        assert decision == EventDecision.SUCCESS
        assert shared is None
        assert len(results) == 1
        assert results[0] == "async_result"

    @pytest.mark.asyncio
    async def test_concurrent_publish(self):
        """测试并发发布事件。"""
        bus = EventBus()
        results = []

        async def handler(event: Event, shared):
            results.append(event.data)
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", handler)

        # 并发发布多个事件
        tasks = [
            bus.publish(Event(name="test_event", data=i), shared=None) for i in range(10)
        ]
        results_list = await asyncio.gather(*tasks)

        assert all(decision == EventDecision.SUCCESS and shared is None for decision, shared in results_list)
        assert len(results) == 10
        assert set(results) == set(range(10))

    @pytest.mark.asyncio
    async def test_handler_exception_during_coroutine(self):
        """测试协程处理器中的异常处理。"""
        bus = EventBus()

        async def failing_handler(event: Event, shared):
            await asyncio.sleep(0.01)
            raise ValueError("Coroutine error")

        async def working_handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("test_event", failing_handler)
        bus.subscribe("test_event", working_handler)

        decision, shared = await bus.publish(Event(name="test_event"), shared=None)
        assert decision == EventDecision.SUCCESS
        assert shared is None

    def test_handler_subscriptions_tracking(self):
        """测试处理器订阅跟踪。"""
        bus = EventBus()

        async def handler(event: Event, shared):
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("event1", handler)
        bus.subscribe("event2", handler)
        bus.subscribe("event3", handler)

        # 取消订阅应该清理跟踪
        bus.unsubscribe("event2", handler)

        assert bus.handler_count == 2
        assert "event1" in bus.subscribed_events
        assert "event3" in bus.subscribed_events

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        bus = EventBus()
        seen: list[str] = []

        async def h_low(event: Event, shared):
            seen.append("low")
            return (EventDecision.SUCCESS, shared)

        async def h_high(event: Event, shared):
            seen.append("high")
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("e", h_low, priority=0)
        bus.subscribe("e", h_high, priority=10)

        await bus.publish(Event(name="e"), shared=None)
        assert seen == ["high", "low"]

    @pytest.mark.asyncio
    async def test_pass_ignores_next_shared(self):
        bus = EventBus()

        async def h1(event: Event, shared: dict):
            return (EventDecision.PASS, {"ignored": True})

        async def h2(event: Event, shared: dict):
            shared["x"] = 1
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("e", h1, priority=10)
        bus.subscribe("e", h2, priority=0)

        decision, shared = await bus.publish(Event(name="e"), shared={})
        assert decision == EventDecision.SUCCESS
        assert shared == {"x": 1}

    @pytest.mark.asyncio
    async def test_stop_terminates_chain(self):
        bus = EventBus()
        seen: list[str] = []

        async def h1(event: Event, shared: dict):
            seen.append("h1")
            return (EventDecision.SUCCESS, shared)

        async def h2(event: Event, shared: dict):
            seen.append("h2")
            shared["stop"] = True
            return (EventDecision.STOP, shared)

        async def h3(event: Event, shared: dict):
            seen.append("h3")
            shared["should_not_run"] = True
            return (EventDecision.SUCCESS, shared)

        bus.subscribe("e", h1, priority=30)
        bus.subscribe("e", h2, priority=20)
        bus.subscribe("e", h3, priority=10)

        decision, shared = await bus.publish(Event(name="e"), shared={})
        assert decision == EventDecision.STOP
        assert shared == {"stop": True}
        assert seen == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_handler_signature_event_only_is_supported(self):
        bus = EventBus()

        async def handler(event: Event):
            return (EventDecision.SUCCESS, {"ok": True})

        bus.subscribe("e", handler)
        decision, shared = await bus.publish(Event(name="e"), shared=None)
        assert decision == EventDecision.SUCCESS
        assert shared == {"ok": True}
