# Event 事件总线

`event` 模块提供 Neo-MoFox 的内核级发布/订阅（Pub/Sub）能力，用于在模块间进行低耦合通信。

它的设计目标是：

1. 简单直接：一套最小 API 覆盖大部分事件驱动场景。
2. 可控可预测：优先级 + 订阅顺序，保证执行顺序稳定。
3. 协议严格：处理器返回值不合规时，自动降级为 PASS，避免污染事件链。

---

## 核心特性

### 1. 发布/订阅模型

- 发布者只关心事件名和参数，不依赖订阅者实现。
- 一个事件可有多个订阅者。
- 同一处理器可订阅多个事件。

### 2. 优先级执行

- `priority` 越大越先执行。
- 同优先级下按最早订阅顺序执行。
- 重复订阅同一事件 + 同一处理器时，会更新优先级，但保持原始订阅顺序。

### 3. 链式参数传递

- 每个处理器会收到当前参数副本。
- `SUCCESS` / `STOP` 会更新链上的共享参数。
- `PASS` 不更新共享参数。

### 4. 决策控制

- `EventDecision.SUCCESS`：正常继续并提交参数。
- `EventDecision.PASS`：跳过并继续，不提交参数。
- `EventDecision.STOP`：提交参数后终止后续处理器。

### 5. 同步/异步处理器混用

- 支持 `def` 与 `async def` 处理器。
- 内部自动判断并 `await` 可等待对象。

### 6. 后台发布

- `publish_sync()` 通过 `task_manager` 创建后台任务，立即返回任务对象。

---

## 快速开始

```python
from src.kernel.event import get_event_bus, EventDecision

bus = get_event_bus()


async def validate_login(event_name: str, params: dict):
    # key 集合必须保持不变，因此用既有字段传递状态
    if not params["user_id"]:
        params["ok"] = False
        return (EventDecision.STOP, params)

    params["ok"] = True
    return (EventDecision.SUCCESS, params)


async def write_audit_log(event_name: str, params: dict):
    if not params["ok"]:
        return (EventDecision.PASS, params)
    # do log
    return (EventDecision.SUCCESS, params)


unsub = bus.subscribe("user_login", validate_login, priority=100)
bus.subscribe("user_login", write_audit_log, priority=10)

decision, final_params = await bus.publish(
    "user_login",
    {
        "user_id": "u-1001",
        "ok": False,
    },
)

unsub()
```

---

## 严格处理器协议

处理器签名：

```python
def handler(event_name: str, params: dict[str, object]) -> tuple[EventDecision, dict[str, object]]:
    ...
```

硬约束：

1. `params` 必须是 `dict`。
2. 返回值必须是 `(decision, next_params)` 二元组。
3. `decision` 必须可转换为 `EventDecision`。
4. `next_params` 必须是 `dict`，且 key 全为 `str`。
5. `next_params` 的 key 集合必须与初始 `params` 完全一致。

当处理器违反协议时：

- 该处理器影响被丢弃。
- 总线记录 warning。
- 链继续执行（按 `PASS` 处理）。

---

## API 速览

### `get_event_bus()`

获取全局事件总线（懒加载）。

### `EventBus.subscribe(event_name, handler, priority=0)`

- 订阅事件。
- 返回取消订阅函数。
- `event_name` 为空或 handler 不可调用会抛 `ValueError`。

### `EventBus.unsubscribe(event_name, handler)`

- 取消某事件上的某处理器。
- 返回 `bool` 表示是否成功移除。

### `EventBus.unsubscribe_all(handler)`

- 从全部事件中移除某处理器。
- 返回移除数量。

### `await EventBus.publish(event_name, params)`

- 异步发布。
- 返回 `(last_decision, final_params)`。
- 入参非法会抛 `ValueError`。

### `EventBus.publish_sync(event_name, params)`

- 创建后台任务并立即返回 `asyncio.Task`。
- 适用于即发即弃场景。

### 只读属性

- `subscribed_events`: 当前有订阅者的事件名集合。
- `handler_count`: 总订阅处理器数量。
- `event_count`: 当前有订阅者的事件数量。

---

## 执行顺序示例

```python
bus.subscribe("order_created", handler_a, priority=100)
bus.subscribe("order_created", handler_b, priority=100)
bus.subscribe("order_created", handler_c, priority=50)
```

执行顺序：

1. `handler_a`（更早订阅）
2. `handler_b`
3. `handler_c`

---

## 常见误区

1. 误区：处理器可以随意新增参数 key。
2. 事实：key 集合必须保持一致；建议预先定义占位字段。

1. 误区：`publish_sync()` 是“阻塞同步调用”。
2. 事实：它是后台异步任务封装，立即返回。

1. 误区：处理器抛异常会终止整个事件链。
2. 事实：异常会被记录，链继续执行后续处理器。

---

## 最佳实践

1. 用命名规范区分事件域：如 `user.login`, `plugin.loaded`。
2. 把“是否成功”这类状态字段预置在 params 中，避免临时加 key。
3. 订阅时显式写 priority，不依赖默认值。
4. 非关键副作用任务用 `publish_sync()`，关键链路用 `await publish()`。
5. 模块卸载时调用 `unsubscribe_all(handler)` 做清理。

---

## 相关资源

- [核心实现](./core.md) - 内部数据结构与流程
- [高级模式](./advanced.md) - 复杂场景设计与避坑
- [示例代码](../../examples/src/kernel/event/event_example.py) - 可运行示例
