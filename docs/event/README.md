# Event 事件总线

事件总线模块提供了 Neo-MoFox 框架的事件驱动架构基础。使用发布/订阅模式（Pub/Sub）实现系统间的**松耦合通信**。

## 核心特性

### 1. 发布/订阅模式

- **发布者**（Publisher）发出事件，**订阅者**（Subscriber）接收处理
- 一个发布者，多个订阅者
- 事件异步处理

### 2. 优先级处理

- 订阅者支持**优先级**设置
- 高优先级处理器优先执行
- 相同优先级按订阅顺序执行

### 3. 链式参数传递

- 每个处理器可以**修改事件参数**
- 修改后的参数传递给下一个处理器
- 支持中断链（STOP 决策）

### 4. 决策控制

三种处理结果：
- `SUCCESS` - 正常完成，参数生效，继续处理
- `PASS` - 跳过，不更新参数，继续处理
- `STOP` - 终止链，不再处理后续订阅者

### 5. 异步支持

- 支持同步和异步处理器
- 自动检测并正确执行
- 完全基于 asyncio

---

## 快速开始

### 基本用法

```python
from src.kernel.event import get_event_bus, EventDecision

# 获取全局事件总线
bus = get_event_bus()

# 定义处理器
async def on_user_login(event_name: str, params: dict):
    """处理用户登录事件"""
    user_id = params["user_id"]
    print(f"用户 {user_id} 登录")
    
    # 修改参数供下一个处理器使用
    params["login_time"] = "2024-01-01 10:00:00"
    
    return (EventDecision.SUCCESS, params)

# 订阅事件
unsub = bus.subscribe("user_login", on_user_login, priority=10)

# 发布事件
await bus.publish("user_login", {"user_id": "123"})

# 取消订阅
unsub()
```

### 优先级示例

```python
# 高优先级处理器先执行
bus.subscribe("order_created", handler_email, priority=10)
bus.subscribe("order_created", handler_notify, priority=5)
bus.subscribe("order_created", handler_log, priority=1)

# 执行顺序：handler_email → handler_notify → handler_log
```

### 决策控制示例

```python
async def validate_user(event_name: str, params: dict):
    """验证用户"""
    user_id = params["user_id"]
    
    if not user_id:
        # 验证失败，中断链
        return (EventDecision.STOP, params)
    
    # 验证通过，继续
    params["validated"] = True
    return (EventDecision.SUCCESS, params)

async def process_user(event_name: str, params: dict):
    """处理用户（只在验证通过时执行）"""
    if not params.get("validated"):
        # 跳过处理
        return (EventDecision.PASS, params)
    
    # 处理用户
    return (EventDecision.SUCCESS, params)

bus.subscribe("process", validate_user, priority=10)
bus.subscribe("process", process_user, priority=5)
```

---

## 核心概念

### EventBus 类

事件总线是系统中所有事件的枢纽。

```python
class EventBus:
    def __init__(self, name: str = "default"):
        """初始化事件总线"""
    
    def subscribe(event_name, handler, priority=0) -> Callable:
        """订阅事件"""
    
    def unsubscribe(event_name, handler) -> bool:
        """取消订阅"""
    
    def unsubscribe_all(handler) -> int:
        """从所有事件中取消订阅"""
    
    async def publish(event_name, params) -> EventHandlerResult:
        """发布事件"""
    
    def publish_sync(event_name, params) -> Task:
        """同步发布（后台异步执行）"""
```

### EventDecision 枚举

处理器返回的决策类型。

```python
class EventDecision(str, Enum):
    SUCCESS = "SUCCESS"  # 正常完成
    STOP = "STOP"        # 中止链
    PASS = "PASS"        # 跳过
```

### 处理器协议

订阅者处理器的签名和返回值有硬性要求。

```python
# 处理器签名
async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
    """
    Args:
        event_name: 事件名称
        params: 事件参数（可修改）
    
    Returns:
        (decision, next_params)
        - decision: EventDecision 枚举值
        - next_params: 修改后的参数，key 集合必须与 params 完全一致
    """
    pass
```

**关键约束**：
1. `params` 必须是 `dict[str, Any]`
2. 返回值必须是 `(EventDecision, dict)` 二元组
3. 返回的参数 **key 集合必须与输入 params 完全一致**（不能添加或删除 key）
4. 如果违反约束，处理器的影响会被丢弃，事件链继续

---

## 常见使用场景

### 场景 1: 用户登录链

```python
async def check_credentials(event_name, params):
    """验证用户凭证"""
    if is_valid_password(params["password"]):
        return (EventDecision.SUCCESS, params)
    return (EventDecision.STOP, params)

async def record_login(event_name, params):
    """记录登录"""
    await db.create_login_log(
        user_id=params["user_id"],
        timestamp=now()
    )
    return (EventDecision.SUCCESS, params)

async def send_notification(event_name, params):
    """发送通知"""
    await send_email(
        to=params["user_email"],
        subject="登录通知"
    )
    return (EventDecision.SUCCESS, params)

bus.subscribe("login", check_credentials, priority=10)
bus.subscribe("login", record_login, priority=5)
bus.subscribe("login", send_notification, priority=1)

await bus.publish("login", {
    "user_id": "123",
    "user_email": "user@example.com",
    "password": "secret"
})
```

### 场景 2: 异步后台处理

```python
async def immediate_response(event_name, params):
    """立即响应"""
    return (EventDecision.SUCCESS, params)

async def background_processing(event_name, params):
    """后台处理"""
    result = await expensive_operation(params["data"])
    await cache.set(f"result:{params['id']}", result)
    return (EventDecision.SUCCESS, params)

# 优先级：立即响应 → 后台处理
bus.subscribe("process", immediate_response, priority=10)
bus.subscribe("process", background_processing, priority=1)

# 调用方可能在处理器1完成后就返回，不必等待处理器2
await bus.publish("process", {"id": "abc", "data": {...}})
```

### 场景 3: 条件链

```python
async def validate(event_name, params):
    """验证数据"""
    if validate_data(params["payload"]):
        params["valid"] = True
        return (EventDecision.SUCCESS, params)
    
    params["valid"] = False
    return (EventDecision.STOP, params)

async def transform(event_name, params):
    """仅在验证通过时转换"""
    if not params.get("valid"):
        return (EventDecision.PASS, params)
    
    params["transformed"] = transform_data(params["payload"])
    return (EventDecision.SUCCESS, params)

async def persist(event_name, params):
    """仅在有转换结果时持久化"""
    if "transformed" not in params:
        return (EventDecision.PASS, params)
    
    await save_to_db(params["transformed"])
    return (EventDecision.SUCCESS, params)

bus.subscribe("data_process", validate, priority=10)
bus.subscribe("data_process", transform, priority=5)
bus.subscribe("data_process", persist, priority=1)
```

---

## API 参考

### get_event_bus()

获取全局事件总线单例。

```python
def get_event_bus() -> EventBus:
    """获取全局事件总线（懒加载）"""
    pass
```

**使用示例**：

```python
bus = get_event_bus()
bus.subscribe("event", handler)
```

### subscribe()

订阅事件。

```python
def subscribe(
    event_name: str,
    handler: EventHandlerCallable,
    priority: int = 0,
) -> Callable[[], None]:
    """
    Args:
        event_name: 事件名称（必需）
        handler: 处理器函数（必需）
        priority: 优先级，值越大越先执行（默认 0）
    
    Returns:
        取消订阅函数
    
    Raises:
        ValueError: event_name 为空或 handler 不可调用
    """
    pass
```

**使用示例**：

```python
# 基本订阅
bus.subscribe("event_name", my_handler)

# 带优先级
bus.subscribe("event_name", my_handler, priority=10)

# 保存取消订阅函数
unsub = bus.subscribe("event_name", my_handler)

# 稍后取消
unsub()
```

### unsubscribe()

取消订阅单个事件。

```python
def unsubscribe(event_name: str, handler: EventHandlerCallable) -> bool:
    """
    Args:
        event_name: 事件名称
        handler: 处理器函数
    
    Returns:
        True 如果成功移除，False 如果未找到
    """
    pass
```

### unsubscribe_all()

从所有事件中取消订阅处理器。

```python
def unsubscribe_all(handler: EventHandlerCallable) -> int:
    """
    Args:
        handler: 处理器函数
    
    Returns:
        取消订阅的事件数
    """
    pass
```

### publish()

异步发布事件。

```python
async def publish(
    event_name: str,
    params: dict[str, Any],
) -> tuple[EventDecision, dict[str, Any]]:
    """
    Args:
        event_name: 事件名称
        params: 事件参数字典
    
    Returns:
        (最后的决策, 最终参数)
    
    Raises:
        ValueError: event_name 或 params 非法
    """
    pass
```

**使用示例**：

```python
decision, final_params = await bus.publish("login", {
    "user_id": "123",
    "timestamp": "2024-01-01"
})

if decision == EventDecision.STOP:
    print("事件被中止")
```

### publish_sync()

同步发布（后台异步执行）。

```python
def publish_sync(
    event_name: str,
    params: dict[str, Any],
) -> asyncio.Task:
    """
    为事件发布创建后台任务，立即返回。
    
    Args:
        event_name: 事件名称
        params: 事件参数字典
    
    Returns:
        asyncio.Task 对象
    """
    pass
```

**使用示例**：

```python
# 立即返回，不等待处理
task = bus.publish_sync("event", {"data": "..."})

# 稍后检查结果
try:
    decision, params = await task
except asyncio.CancelledError:
    print("事件处理被取消")
```

### get_subscribers()

获取事件的所有订阅者列表。

```python
def get_subscribers(event_name: str) -> list[EventHandlerCallable]:
    """
    Args:
        event_name: 事件名称
    
    Returns:
        订阅者处理器列表（按优先级排序）
    """
    pass
```

### 属性

```python
# 获取所有有订阅的事件名称集合
events = bus.subscribed_events  # set[str]

# 获取所有订阅的处理器总数
count = bus.handler_count  # int

# 获取有订阅的唯一事件数
num_events = bus.event_count  # int
```

---

## 最佳实践

### 1. 遵守处理器协议

❌ **错误**：返回值不正确

```python
async def bad_handler(event_name, params):
    # 缺少返回值
    await do_something()

async def bad_handler2(event_name, params):
    # 返回值类型错误
    return params  # 应该返回 (EventDecision, dict)

async def bad_handler3(event_name, params):
    # 修改了 key 集合
    params["new_key"] = "value"  # 错误！
    return (EventDecision.SUCCESS, params)
```

✅ **正确**：遵守协议

```python
async def good_handler(event_name, params):
    # 正确的返回值
    await do_something()
    return (EventDecision.SUCCESS, params)

async def good_handler2(event_name, params):
    # 只修改 value，不改 key
    params["user_id"] = str(params["user_id"]).upper()
    return (EventDecision.SUCCESS, params)
```

### 2. 正确使用决策

```python
# SUCCESS - 处理完毕，参数生效
return (EventDecision.SUCCESS, params)

# PASS - 跳过处理，不改参数
return (EventDecision.PASS, params)

# STOP - 中止链，不处理后续
return (EventDecision.STOP, params)
```

### 3. 优先级设计

```python
# 优先级约定（可根据需要调整）
CRITICAL = 100    # 验证、安全检查
HIGH = 50         # 核心业务逻辑
NORMAL = 0        # 默认
LOW = -50         # 日志、监控
VERY_LOW = -100   # 后台异步任务

bus.subscribe("login", auth_check, priority=CRITICAL)
bus.subscribe("login", update_user, priority=HIGH)
bus.subscribe("login", send_email, priority=LOW)
```

### 4. 错误处理

```python
async def resilient_handler(event_name, params):
    try:
        result = await risky_operation()
        params["result"] = result
        return (EventDecision.SUCCESS, params)
    except Exception as e:
        # 记录错误但不中断链
        logger.error(f"处理失败: {e}")
        return (EventDecision.PASS, params)
```

### 5. 避免递归事件

```python
# ❌ 危险：会造成事件循环
async def bad_handler(event_name, params):
    await bus.publish("another_event", {"data": "..."})
    return (EventDecision.SUCCESS, params)

# ✅ 安全：使用同步发布（后台异步）
async def good_handler(event_name, params):
    # 立即返回，不阻塞当前链
    bus.publish_sync("another_event", {"data": "..."})
    return (EventDecision.SUCCESS, params)
```

### 6. 资源清理

```python
# 订阅时保存取消函数
unsub = bus.subscribe("event", handler)

# 在不需要时取消
unsub()

# 或者一次取消所有订阅
bus.unsubscribe_all(handler)
```

---

## 与其他模块集成

### 与 Concurrency 模块

事件可能需要创建后台任务：

```python
from src.kernel.concurrency import get_task_manager

async def background_task_handler(event_name, params):
    task_mgr = get_task_manager()
    
    # 创建后台任务
    task = task_mgr.create_task(
        expensive_operation(params["data"])
    )
    
    # 立即返回
    return (EventDecision.SUCCESS, params)
```

### 与 Logger 模块

事件处理器应该记录关键操作：

```python
from src.kernel.logger import get_logger

logger = get_logger("event_handlers")

async def logged_handler(event_name, params):
    logger.info(f"处理事件 {event_name}")
    
    try:
        result = await process(params)
        logger.debug(f"结果: {result}")
        return (EventDecision.SUCCESS, params)
    except Exception as e:
        logger.error(f"失败: {e}")
        return (EventDecision.PASS, params)
```

---

## 故障排除

### 问题：处理器未执行

**原因**：
1. 事件名称拼写错误
2. 处理器协议不符（返回值错误）

**解决**：
```python
# 检查订阅
handlers = bus.get_subscribers("event_name")
print(f"订阅者数: {len(handlers)}")

# 检查返回值格式
assert isinstance(result, tuple) and len(result) == 2
assert isinstance(result[0], EventDecision)
assert isinstance(result[1], dict)
```

### 问题：参数在处理器间丢失

**原因**：处理器返回的 key 集合与输入不一致

**解决**：
```python
# 正确方式
async def handler(event_name, params):
    # 只修改 value，不改 key
    for key in params:
        params[key] = transform(params[key])
    
    return (EventDecision.SUCCESS, params)
```

### 问题：事件链被提前中止

**原因**：某个处理器返回了 STOP 决策

**解决**：
```python
# 检查执行的处理器
decision, final_params = await bus.publish(event_name, params)

if decision == EventDecision.STOP:
    logger.warning("事件链被中止")
    # 检查哪个处理器返回了 STOP
```

---

## 相关资源

- [核心实现细节](./core.md) - EventBus 内部实现
- [与 Concurrency 集成](../concurrency/README.md) - 后台任务管理
- [日志和监控](../logger/README.md) - 事件处理日志
