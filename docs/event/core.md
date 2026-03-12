# Event 模块核心实现

## 概述

event 模块提供轻量级的发布/订阅（Pub/Sub）事件总线实现。核心特点：

- **最小化设计**：只提供必要的事件机制
- **异步友好**：基于 asyncio，支持同步/异步处理器
- **优先级支持**：按优先级有序执行订阅者
- **链式参数**：处理器可修改参数传递给下一个
- **严格协议**：处理器返回值经过规范化检查

---

## EventBus 架构

### 内部结构

```python
class EventBus:
    _subscribers: dict[str, dict[EventHandlerCallable, _Subscriber]]
    """事件名 -> {处理器 -> 订阅元数据}"""
    
    _handler_subscriptions: dict[EventHandlerCallable, set[str]]
    """处理器 -> 订阅的事件集合（便于快速清理）"""
    
    _subscribe_order: int
    """全局递增的订阅计数器（用于稳定排序）"""
```

### _Subscriber 元数据

```python
@dataclass(frozen=True)
class _Subscriber:
    handler: EventHandlerCallable      # 处理器函数
    priority: int                      # 优先级（高->低）
    order: int                         # 订阅序号（保证稳定排序）
```

---

## 核心方法详解

### subscribe()

订阅事件的完整流程。

```python
def subscribe(
    self,
    event_name: str,
    handler: EventHandlerCallable,
    priority: int = 0,
) -> Callable[[], None]:
    """
    订阅事件。
    
    1. 验证输入（event_name 非空，handler 可调用）
    2. 检查重复订阅（更新 priority，保持 order）
    3. 创建订阅元数据（_Subscriber）
    4. 注册到两个字典
    5. 返回取消订阅函数
    """
```

**流程图**：

```
输入验证
    ↓
重复检查 ──→ 已存在 ──→ 更新 priority，保持 order
    ↓
    新订阅 ──→ 创建 _Subscriber（递增 order）
    ↓
注册到 _subscribers[event_name][handler]
    ↓
注册到 _handler_subscriptions[handler].add(event_name)
    ↓
日志记录
    ↓
返回取消函数
```

**关键特点**：

1. **重复订阅处理**：同一处理器多次订阅同一事件时，更新优先级但保持原始 order
   ```python
   if existing is None:
       # 新订阅
       sub = _Subscriber(handler, priority, new_order)
   else:
       # 重复订阅：保持原 order，更新 priority
       sub = _Subscriber(handler, priority, existing.order)
   ```

2. **稳定排序**：使用 `(priority, order)` 二元组排序
   ```python
   # 优先级高的先，相同优先级按订阅顺序
   sorted(subs, key=lambda s: (-s.priority, s.order))
   ```

3. **取消订阅函数闭包**：
   ```python
   def unsubscribe() -> None:
       self.unsubscribe(event_name, handler)
   return unsubscribe
   ```

---

### unsubscribe()

取消单个订阅。

```python
def unsubscribe(
    self,
    event_name: str,
    handler: EventHandlerCallable,
) -> bool:
    """
    1. 检查事件是否存在
    2. 检查处理器是否已订阅
    3. 从 _subscribers[event_name] 移除
    4. 从 _handler_subscriptions[handler] 移除
    5. 清理空集合
    6. 返回是否成功
    """
```

**清理逻辑**：

```python
# 移除订阅
self._subscribers[event_name].pop(handler, None)
self._handler_subscriptions[handler].discard(event_name)

# 清理空容器
if not self._subscribers[event_name]:
    del self._subscribers[event_name]
if not self._handler_subscriptions[handler]:
    del self._handler_subscriptions[handler]
```

---

### unsubscribe_all()

从所有事件中取消单个处理器。

```python
def unsubscribe_all(self, handler: EventHandlerCallable) -> int:
    """
    1. 获取处理器订阅的所有事件
    2. 依次调用 unsubscribe()
    3. 返回成功移除的订阅数
    """
    event_names = list(self._handler_subscriptions[handler])
    count = 0
    for event_name in event_names:
        if self.unsubscribe(event_name, handler):
            count += 1
    return count
```

**使用场景**：模块卸载或对象析构时清理所有事件订阅

---

### publish()

发布事件的核心流程。

```python
async def publish(
    self,
    event_name: str,
    params: EventParams,
) -> EventHandlerResult:
    """
    1. 验证输入
    2. 获取订阅者列表并排序
    3. 链式调用处理器
    4. 返回最终决策和参数
    """
```

**详细流程**：

```python
# 1. 输入验证
if not event_name or not isinstance(event_name, str):
    raise ValueError("事件名称必须是非空字符串")
if not isinstance(params, dict):
    raise ValueError("params 必须是 dict")
if any(not isinstance(k, str) for k in params.keys()):
    raise ValueError("params 的 key 必须全部为 str")

# 2. 获取订阅者
if event_name not in self._subscribers:
    return (EventDecision.SUCCESS, dict(params))  # 无订阅者

subs = sorted(
    self._subscribers[event_name].values(),
    key=lambda s: (-s.priority, s.order),
)

# 3. 链式执行处理器
expected_keys = set(params.keys())
current_params = dict(params)
last_decision = EventDecision.SUCCESS

for sub in subs:
    try:
        raw_result = await self._execute_handler(
            sub, event_name, dict(current_params)
        )
    except Exception as e:
        logger.error(f"处理器失败: {e}")
        last_decision = EventDecision.PASS
        continue

    # 规范化返回值
    decision, next_params = self._normalize_handler_result(...)
    last_decision = decision

    # 根据决策处理参数
    if decision == EventDecision.PASS:
        continue  # 不更新参数
    
    current_params = next_params

    if decision == EventDecision.STOP:
        break  # 中止链

# 4. 返回结果
return (last_decision, current_params)
```

**参数传递过程**：

```
初始参数: {"user_id": "123", "active": true}
    ↓
处理器1 (优先级10): 修改为 {"user_id": "123", "active": true}
    ↓ SUCCESS
处理器2 (优先级5): 修改为 {"user_id": "123", "active": true, ...}
    ↓ SUCCESS
处理器3 (优先级1): 返回 PASS（不更新）
    ↓ PASS
最终参数: {"user_id": "123", "active": true, ...}
```

---

### _execute_handler()

执行单个处理器（同步或异步）。

```python
async def _execute_handler(
    self,
    sub: _Subscriber,
    event_name: str,
    params: EventParams,
) -> Any:
    """
    自动检测处理器是否为 coroutine 并正确执行。
    """
    result = sub.handler(event_name, params)
    
    if inspect.isawaitable(result):
        return await result  # 异步处理器
    
    return result  # 同步处理器
```

**支持的处理器类型**：

```python
# 异步处理器
async def async_handler(event_name, params):
    await do_something()
    return (EventDecision.SUCCESS, params)

# 同步处理器
def sync_handler(event_name, params):
    do_something()
    return (EventDecision.SUCCESS, params)

# 两者都支持
```

---

### _normalize_handler_result()

规范化处理器返回值，确保符合协议。

```python
def _normalize_handler_result(
    self,
    result: Any,
    *,
    current_params: EventParams,
    expected_keys: set[str],
    handler_name: str,
) -> EventHandlerResult:
    """
    检查返回值是否符合协议。如果不符合，返回 (PASS, current_params)。
    
    检查项：
    1. 是否为二元组
    2. decision 是否合法
    3. next_params 是否为 dict
    4. next_params 的 key 是否为 str
    5. next_params 的 key 集合是否与 params 完全一致
    """
```

**检查流程**：

```python
# 1. 检查二元组
if not (isinstance(result, tuple) and len(result) == 2):
    logger.warning("返回值不是二元组")
    return (EventDecision.PASS, current_params)

raw_decision, next_params = result

# 2. 检查决策
try:
    decision = EventDecision(str(raw_decision))
except Exception:
    logger.warning("decision 不合法")
    return (EventDecision.PASS, current_params)

# 3. 检查 params 类型
if not isinstance(next_params, dict):
    logger.warning("next_params 必须是 dict")
    return (EventDecision.PASS, current_params)

# 4. 检查 key 类型
if any(not isinstance(k, str) for k in next_params.keys()):
    logger.warning("params key 必须为 str")
    return (EventDecision.PASS, current_params)

# 5. 检查 key 集合一致性
if set(next_params.keys()) != expected_keys:
    logger.warning("next_params key 集合不一致")
    return (EventDecision.PASS, current_params)

return (decision, next_params)
```

**防御原则**：

- 任何违反协议的处理器，其影响被丢弃
- 事件链继续执行，不会崩溃
- 错误被记录以便调试

---

### publish_sync()

后台异步发布（即发即弃）。

```python
def publish_sync(
    self,
    event_name: str,
    params: EventParams,
) -> asyncio.Task[EventHandlerResult]:
    """
    为事件发布创建后台任务。
    
    适用场景：
    - 不需要等待处理结果
    - 发布方立即继续执行
    - 处理器在后台异步进行
    """
    task = get_task_manager().create_task(
        self.publish(event_name, params)
    )
    return task.task  # 返回底层 asyncio.Task
```

**使用示例**：

```python
# 立即返回，后台异步处理
task = bus.publish_sync("event", {"data": "..."})

# 稍后检查状态
if task.done():
    result = task.result()
else:
    print("还在处理...")

# 等待完成
try:
    decision, params = await task
except asyncio.CancelledError:
    print("任务被取消")
```

---

## 优先级排序机制

### 两级排序

```python
# 按 (-priority, order) 排序
sorted_subs = sorted(
    subs,
    key=lambda s: (-s.priority, s.order)
)
```

**效果**：

| Priority | Order | Sort Key | 执行顺序 |
|----------|-------|----------|---------|
| 10       | 1     | (-10, 1) | 1       |
| 10       | 2     | (-10, 2) | 2       |
| 5        | 3     | (-5, 3)  | 3       |
| 5        | 4     | (-5, 4)  | 4       |
| 0        | 5     | (0, 5)   | 5       |

### 重复订阅保持顺序

```python
# 第一次订阅
bus.subscribe("event", handler, priority=5)  # order=1

# 第二次订阅（同一处理器，优先级变为10）
bus.subscribe("event", handler, priority=10)  # order 仍为 1
```

**结果**：同一处理器多次订阅时，保持原始 order，只更新 priority

---

## 决策控制流

### SUCCESS 流程

```
处理器返回 SUCCESS
    ↓
next_params 进行规范化检查
    ↓ 合法
current_params = next_params  # 更新参数
    ↓
继续下一个处理器
```

### PASS 流程

```
处理器返回 PASS
    ↓
next_params 被忽略
    ↓
current_params 保持不变
    ↓
继续下一个处理器
```

### STOP 流程

```
处理器返回 STOP
    ↓
链执行终止
    ↓
返回最终结果
```

---

## 错误处理策略

### 处理器异常

```python
try:
    raw_result = await self._execute_handler(...)
except Exception as e:
    logger.error(f"处理器失败: {e}")
    last_decision = EventDecision.PASS
    continue  # 继续下一个处理器
```

**原则**：
- 处理器异常不中断链
- 异常被记录
- 链继续执行，参数不变

### 返回值异常

```python
# 任何违反协议的返回值都被规范化为 (PASS, current_params)
decision, next_params = self._normalize_handler_result(
    raw_result,
    current_params=current_params,
    ...
)
```

**防御等级**：

1. **类型检查**：二元组、decision 类型、params 类型
2. **结构检查**：key 类型、key 集合
3. **一致性检查**：params key 与输入一致

---

## 内存管理

### 双字典设计的优势

```python
# _subscribers: event -> {handler -> metadata}
# 用于发布时快速查找

# _handler_subscriptions: handler -> {events}
# 用于取消订阅时快速清理
```

**复杂度**：

| 操作 | 复杂度 | 说明 |
|------|--------|------|
| subscribe() | O(1) | 字典插入 |
| unsubscribe() | O(1) | 字典删除 |
| unsubscribe_all() | O(n) | n = 处理器订阅的事件数 |
| publish() | O(m log m) | m = 该事件的订阅者数 |

---

## 与其他模块集成

### Concurrency 模块

```python
# EventBus 使用 TaskManager 进行后台发布
from src.kernel.concurrency import get_task_manager

def publish_sync(self, ...):
    task = get_task_manager().create_task(
        self.publish(event_name, params)
    )
    return task.task
```

### Logger 模块

```python
# EventBus 使用 Logger 记录日志
from src.kernel.logger import get_logger

logger = get_logger(
    "event_bus",
    display="EventBus",
    color=COLOR.MAGENTA,
    enable_event_broadcast=False,  # 防止递归
)
```

**防止递归的关键**：`enable_event_broadcast=False`
- 如果 EventBus 自身的日志也通过事件系统广播，可能出现无限递归
- 通过禁用事件广播来打破循环

---

## 全局单例

### get_event_bus()

```python
_event_bus: EventBus | None = None

def get_event_bus() -> EventBus:
    """获取全局事件总线（懒加载单例）"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(name="global")
    return _event_bus
```

**特点**：
- 懒加载：首次使用时初始化
- 线程安全：Python GIL 保障
- 全局唯一：确保事件统一流向

---

## 性能考虑

### 1. 排序开销

每次发布都需要排序订阅者：

```python
subs = sorted(
    self._subscribers[event_name].values(),
    key=lambda s: (-s.priority, s.order),
)
```

**优化建议**：如果同一事件的订阅者很多且频繁发布，可以缓存排序结果

### 2. 参数复制

```python
# 传递参数副本，避免意外修改原始对象
current_params = dict(params)
await self._execute_handler(sub, event_name, dict(current_params))
```

**权衡**：安全性 vs 性能

### 3. 异步开销

同步处理器被包装为 awaitable：

```python
result = sub.handler(event_name, params)
if inspect.isawaitable(result):
    return await result
return result
```

**影响**：最小化，因为同步函数返回不经过事件循环

---

## 相关资源

- [Event 主文档](./README.md) - API 和最佳实践
- [Concurrency 模块](../concurrency/README.md) - 后台任务
- [Logger 模块](../logger/README.md) - 日志系统
