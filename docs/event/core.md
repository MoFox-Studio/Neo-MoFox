# Event 模块核心实现

本文档对应 `src/kernel/event/core.py` 的当前实现，描述 EventBus 的内部结构、发布流程和约束检查逻辑。

---

## 模块结构

`src/kernel/event` 公开：

1. `EventBus`
2. `EventDecision`
3. `get_event_bus()`

全局总线通过懒加载单例创建：

```python
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(name="global")
    return _event_bus
```

---

## 关键类型

```python
EventParams = dict[str, Any]
EventHandlerResult = tuple[EventDecision, EventParams]
EventHandlerCallable = Callable[[str, EventParams], EventHandlerResult | Awaitable[EventHandlerResult]]
```

决策枚举：

```python
class EventDecision(str, Enum):
    SUCCESS = "SUCCESS"
    STOP = "STOP"
    PASS = "PASS"
```

---

## 内部数据结构

`EventBus` 内部有三类状态：

1. `_subscribers: dict[str, dict[handler, _Subscriber]]`
2. `_handler_subscriptions: dict[handler, set[event_name]]`
3. `_subscribe_order: int`

`_Subscriber`：

```python
@dataclass(frozen=True)
class _Subscriber:
    handler: EventHandlerCallable
    priority: int
    order: int
```

含义：

- `priority` 决定先后。
- `order` 保证同优先级下稳定顺序。

---

## 订阅流程

### `subscribe()`

行为要点：

1. 校验 `event_name` 与 `handler`。
2. 若重复订阅：更新 `priority`，保留旧 `order`。
3. 新订阅：`_subscribe_order += 1` 生成新序号。
4. 双向登记到 `_subscribers` 和 `_handler_subscriptions`。
5. 返回闭包 `unsubscribe()`。

伪代码：

```python
existing = self._subscribers[event_name].get(handler)
if existing is None:
    self._subscribe_order += 1
    sub = _Subscriber(handler, int(priority), self._subscribe_order)
else:
    sub = _Subscriber(handler, int(priority), existing.order)

self._subscribers[event_name][handler] = sub
self._handler_subscriptions[handler].add(event_name)
```

---

## 取消订阅流程

### `unsubscribe(event_name, handler) -> bool`

1. 事件不存在返回 `False`。
2. 处理器不存在返回 `False`。
3. 移除订阅。
4. 清理空容器，防止残留空键。

### `unsubscribe_all(handler) -> int`

1. 从 `_handler_subscriptions` 取出该处理器订阅的全部事件。
2. 逐个调用 `unsubscribe()`。
3. 返回移除成功数。

---

## 发布主流程

### `publish(event_name, params)`

执行阶段：

1. 入参校验
2. 读取订阅者并排序
3. 逐个执行处理器
4. 规范化处理器返回值
5. 应用决策并更新链参数
6. 返回最终结果

### 1) 入参校验

校验失败抛 `ValueError`：

1. `event_name` 必须是非空字符串
2. `params` 必须是 `dict`
3. `params` 的 key 必须全是字符串

### 2) 订阅者排序

```python
subs = sorted(
    self._subscribers[event_name].values(),
    key=lambda s: (-s.priority, s.order),
)
```

排序规则：优先级高在前，同优先级按订阅顺序。

### 3) 执行处理器

通过 `_execute_handler()` 统一支持同步/异步：

```python
result = sub.handler(event_name, params)
if inspect.isawaitable(result):
    return await result
return result
```

处理器抛异常时：

- 记录 error 日志
- 当前处理器按 `PASS` 处理
- 继续下一处理器

### 4) 结果规范化

`_normalize_handler_result()` 会检查：

1. 是否是长度为 2 的 tuple
2. `decision` 是否可转 `EventDecision`
3. `next_params` 是否为 `dict`
4. `next_params` 的 key 是否全为 `str`
5. `next_params` 的 key 集合是否与初始 key 集合一致

若任一步失败：返回 `(PASS, current_params)`。

### 5) 决策应用

1. `PASS`: 忽略 `next_params`，继续
2. `SUCCESS`: 提交 `next_params`，继续
3. `STOP`: 提交 `next_params`，并终止循环

### 6) 返回值

返回 `(last_decision, current_params)`。

---

## 后台发布

### `publish_sync(event_name, params)`

内部调用：

```python
task = get_task_manager().create_task(self.publish(event_name, params))
return task.task
```

说明：

1. 不是阻塞调用。
2. 返回 `asyncio.Task`，调用方可选择 await 或忽略。
3. 遵循项目约束：通过 `task_manager` 管理异步任务。

---

## 统计与运维接口

### 属性

1. `subscribed_events`：事件名集合
2. `handler_count`：处理器总数
3. `event_count`：事件总数

### 工具方法

1. `clear()`：清空所有订阅（常用于测试隔离）
2. `get_subscribers(event_name)`：按执行顺序返回处理器列表
3. `__repr__()`：调试输出总线概要信息

---

## 协议设计取舍

key 集合严格一致带来的效果：

1. 防止处理器“偷偷改变契约”导致下游崩溃。
2. 让事件链参数结构可预期，调试成本更低。
3. 要求调用方在初始 params 中预留字段。

建议实践：

1. 为每个事件定义 params schema（文档或 TypedDict）。
2. 避免在处理器中新增/删除 key，只更新 value。

---

## 相关资源

- [主文档](./README.md) - 使用总览
- [高级模式](./advanced.md) - 架构模式与避坑
- [源码实现](../../src/kernel/event/core.py) - 最终真值
