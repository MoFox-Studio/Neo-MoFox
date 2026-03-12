# Event 模块高级模式

## 概述

本文档介绍 Event 事件总线的高级使用模式、优化技巧和常见的架构模式。

---

## 1. 事件链设计模式

### 模式 1A: 验证链

多个验证器逐级检查数据。

```python
# 定义验证阶段（优先级从高到低）
async def validate_format(event_name, params):
    """验证数据格式"""
    data = params["data"]
    
    if not isinstance(data, dict):
        logger.error("数据格式错误")
        return (EventDecision.STOP, params)
    
    return (EventDecision.SUCCESS, params)

async def validate_required_fields(event_name, params):
    """验证必填字段"""
    data = params["data"]
    required = {"name", "email"}
    
    if not required.issubset(data.keys()):
        logger.error("缺少必填字段")
        return (EventDecision.STOP, params)
    
    return (EventDecision.SUCCESS, params)

async def validate_business_rules(event_name, params):
    """验证业务规则"""
    data = params["data"]
    
    # 检查邮箱格式
    if "@" not in data.get("email", ""):
        logger.error("邮箱格式无效")
        return (EventDecision.STOP, params)
    
    return (EventDecision.SUCCESS, params)

# 注册验证链
bus.subscribe("user_create", validate_format, priority=100)
bus.subscribe("user_create", validate_required_fields, priority=90)
bus.subscribe("user_create", validate_business_rules, priority=80)

# 发布事件
await bus.publish("user_create", {"data": {...}})
```

**执行顺序**：format (100) → fields (90) → rules (80)

### 模式 1B: 处理链

数据流经多个处理器。

```python
async def extract_data(event_name, params):
    """提取数据"""
    raw_input = params["raw_input"]
    params["extracted"] = extract(raw_input)
    return (EventDecision.SUCCESS, params)

async def transform_data(event_name, params):
    """转换数据"""
    extracted = params["extracted"]
    params["transformed"] = transform(extracted)
    return (EventDecision.SUCCESS, params)

async def enrich_data(event_name, params):
    """丰富数据"""
    transformed = params["transformed"]
    params["enriched"] = await fetch_additional_info(transformed)
    return (EventDecision.SUCCESS, params)

async def persist_data(event_name, params):
    """持久化数据"""
    enriched = params["enriched"]
    await db.save(enriched)
    params["persisted"] = True
    return (EventDecision.SUCCESS, params)

# 处理链：提取 → 转换 → 丰富 → 保存
bus.subscribe("data_process", extract_data, priority=100)
bus.subscribe("data_process", transform_data, priority=75)
bus.subscribe("data_process", enrich_data, priority=50)
bus.subscribe("data_process", persist_data, priority=25)
```

---

## 2. 条件执行模式

### 模式 2A: 条件分支

根据参数决策是否执行。

```python
async def check_user_status(event_name, params):
    """检查用户状态"""
    user_id = params["user_id"]
    user = await db.get_user(user_id)
    
    params["user_status"] = user.status
    return (EventDecision.SUCCESS, params)

async def send_notification(event_name, params):
    """仅在用户活跃时发送通知"""
    if params.get("user_status") != "active":
        return (EventDecision.PASS, params)
    
    await send_email(params["user_id"])
    return (EventDecision.SUCCESS, params)

async def update_cache(event_name, params):
    """总是更新缓存"""
    cache.set(f"user:{params['user_id']}", params["user_status"])
    return (EventDecision.SUCCESS, params)

bus.subscribe("user_event", check_user_status, priority=10)
bus.subscribe("user_event", send_notification, priority=5)
bus.subscribe("user_event", update_cache, priority=1)
```

### 模式 2B: 提前终止

某些条件满足时中止链。

```python
async def rate_limit_check(event_name, params):
    """检查速率限制"""
    user_id = params["user_id"]
    
    if is_rate_limited(user_id):
        logger.warning(f"用户 {user_id} 超出限制")
        return (EventDecision.STOP, params)
    
    return (EventDecision.SUCCESS, params)

async def process_request(event_name, params):
    """处理请求（仅在不限流时执行）"""
    # 这个处理器只在未限流时才会被调用
    await do_something(params)
    return (EventDecision.SUCCESS, params)

bus.subscribe("request", rate_limit_check, priority=100)
bus.subscribe("request", process_request, priority=50)
```

---

## 3. 异步协调模式

### 模式 3A: 并行任务

多个独立的后台任务。

```python
from src.kernel.concurrency import get_task_manager

async def send_email_async(event_name, params):
    """后台发送邮件"""
    task_mgr = get_task_manager()
    
    async def send():
        await slow_email_service.send(params["email"])
    
    task = task_mgr.create_task(send())
    # 立即返回，不阻塞事件链
    return (EventDecision.SUCCESS, params)

async def update_analytics_async(event_name, params):
    """后台更新分析数据"""
    task_mgr = get_task_manager()
    
    async def update():
        await analytics.record(params["action"])
    
    task = task_mgr.create_task(update())
    return (EventDecision.SUCCESS, params)

async def log_event(event_name, params):
    """同步记录日志"""
    logger.info(f"事件: {params}")
    return (EventDecision.SUCCESS, params)

# 发布时：log_event 同步执行，两个异步任务并行运行
bus.subscribe("user_action", log_event, priority=100)
bus.subscribe("user_action", send_email_async, priority=50)
bus.subscribe("user_action", update_analytics_async, priority=50)
```

### 模式 3B: 通知链

多事件级联触发。

```python
# 用户创建 → 发送欢迎邮件 → 发送系统通知 → ...

async def on_user_created(event_name, params):
    """用户创建后的处理"""
    await db.save_user(params["user"])
    
    # 触发后续事件
    bus.publish_sync("user_welcome", {
        "user_id": params["user"]["id"],
        "email": params["user"]["email"]
    })
    
    return (EventDecision.SUCCESS, params)

async def on_user_welcome(event_name, params):
    """发送欢迎邮件"""
    await send_welcome_email(params["email"])
    
    # 触发通知
    bus.publish_sync("send_system_notification", {
        "user_id": params["user_id"],
        "message": "欢迎加入"
    })
    
    return (EventDecision.SUCCESS, params)
```

**优势**：
- 事件解耦，每个模块只关心自己的事件
- 易于扩展：添加新处理器无需修改现有代码
- 便于测试：每个处理器独立可测

---

## 4. 聚合模式

### 模式 4A: 多源信息聚合

```python
async def collect_user_info(event_name, params):
    """收集用户信息"""
    user_id = params["user_id"]
    
    # 从多个源获取信息
    user = await db.get_user(user_id)
    profile = await get_user_profile(user_id)
    preferences = await get_user_preferences(user_id)
    
    # 聚合到参数中
    params["user"] = user
    params["profile"] = profile
    params["preferences"] = preferences
    
    return (EventDecision.SUCCESS, params)

async def analyze_user(event_name, params):
    """分析用户数据"""
    user = params["user"]
    profile = params["profile"]
    
    params["risk_score"] = calculate_risk(user, profile)
    params["recommendation"] = generate_recommendation(user)
    
    return (EventDecision.SUCCESS, params)

bus.subscribe("user_analysis", collect_user_info, priority=10)
bus.subscribe("user_analysis", analyze_user, priority=5)

# 发布事件
await bus.publish("user_analysis", {"user_id": 123})
```

### 模式 4B: 统计聚合

```python
async def collect_metrics(event_name, params):
    """收集指标"""
    params["metrics"] = {
        "cpu": get_cpu_usage(),
        "memory": get_memory_usage(),
        "disk": get_disk_usage(),
    }
    return (EventDecision.SUCCESS, params)

async def aggregate_metrics(event_name, params):
    """聚合指标"""
    metrics = params["metrics"]
    
    total_usage = (
        metrics["cpu"] * 0.4 +
        metrics["memory"] * 0.4 +
        metrics["disk"] * 0.2
    )
    
    params["total_usage"] = total_usage
    params["health_status"] = "good" if total_usage < 70 else "warning"
    
    return (EventDecision.SUCCESS, params)

bus.subscribe("system_health", collect_metrics, priority=10)
bus.subscribe("system_health", aggregate_metrics, priority=5)
```

---

## 5. 拦截器模式

### 模式 5A: 预处理和后处理

```python
async def preprocess(event_name, params):
    """预处理：规范化输入"""
    params["input"] = str(params["input"]).strip()
    params["timestamp"] = now()
    return (EventDecision.SUCCESS, params)

async def main_handler(event_name, params):
    """主处理逻辑"""
    result = process(params["input"])
    params["result"] = result
    return (EventDecision.SUCCESS, params)

async def postprocess(event_name, params):
    """后处理：格式化输出"""
    params["formatted_result"] = format_output(params["result"])
    params["duration"] = now() - params["timestamp"]
    return (EventDecision.SUCCESS, params)

async def logging_handler(event_name, params):
    """日志记录"""
    logger.info(f"处理完成: {params['formatted_result']} ({params['duration']}ms)")
    return (EventDecision.SUCCESS, params)

bus.subscribe("process", preprocess, priority=100)
bus.subscribe("process", main_handler, priority=50)
bus.subscribe("process", postprocess, priority=25)
bus.subscribe("process", logging_handler, priority=10)
```

### 模式 5B: 异常处理包装

```python
async def safe_handler(event_name, params):
    """安全的处理器包装"""
    try:
        result = await risky_operation(params)
        params["result"] = result
        params["error"] = None
        return (EventDecision.SUCCESS, params)
    except SpecificError as e:
        logger.warning(f"已知错误: {e}")
        params["error"] = str(e)
        params["error_code"] = "KNOWN_ERROR"
        return (EventDecision.SUCCESS, params)
    except Exception as e:
        logger.error(f"未知错误: {e}")
        params["error"] = str(e)
        params["error_code"] = "UNKNOWN_ERROR"
        return (EventDecision.PASS, params)
```

---

## 6. 性能优化

### 6.1 减少排序开销

```python
# 问题：每次发布都排序，如果订阅者多且发布频繁会浪费CPU

# 解决方案 1: 固定优先级分组
class EventBusOptimized(EventBus):
    def __init__(self):
        super().__init__()
        self._sorted_subs_cache = {}
    
    async def publish(self, event_name, params):
        # 检查缓存
        if event_name not in self._sorted_subs_cache:
            subs = sorted(
                self._subscribers[event_name].values(),
                key=lambda s: (-s.priority, s.order),
            )
            self._sorted_subs_cache[event_name] = subs
        
        # 使用缓存的有序列表
        subs = self._sorted_subs_cache[event_name]
        # ... 继续处理
```

### 6.2 异步并行处理

```python
# 问题：事件处理器依次执行，总时间是所有处理器之和

# 解决方案：并行处理不相关的处理器
async def publish_parallel(bus, event_name, params):
    """并行执行事件处理器"""
    handlers = bus.get_subscribers(event_name)
    
    # 分组独立处理器
    independent_handlers = [h for h in handlers if is_independent(h)]
    dependent_handlers = [h for h in handlers if not is_independent(h)]
    
    # 并行执行独立处理器
    tasks = [handler(event_name, params) for handler in independent_handlers]
    results = await asyncio.gather(*tasks)
    
    # 顺序执行依赖处理器
    for handler in dependent_handlers:
        await handler(event_name, params)
```

### 6.3 处理器缓存

```python
# 问题：频繁的动态处理器查询开销

# 解决方案：缓存处理器列表
class CachedEventBus(EventBus):
    def __init__(self):
        super().__init__()
        self._handler_cache = {}
    
    def subscribe(self, event_name, handler, priority=0):
        # 订阅时清除缓存
        self._handler_cache.pop(event_name, None)
        return super().subscribe(event_name, handler, priority)
    
    def get_subscribers(self, event_name):
        # 检查缓存
        if event_name not in self._handler_cache:
            self._handler_cache[event_name] = super().get_subscribers(event_name)
        
        return self._handler_cache[event_name]
```

---

## 7. 监控和调试

### 7.1 事件统计

```python
class MonitoredEventBus(EventBus):
    def __init__(self):
        super().__init__()
        self.stats = {
            "total_events": 0,
            "events_by_type": {},
            "handlers_by_event": {},
        }
    
    async def publish(self, event_name, params):
        self.stats["total_events"] += 1
        self.stats["events_by_type"][event_name] = \
            self.stats["events_by_type"].get(event_name, 0) + 1
        
        return await super().publish(event_name, params)
    
    def get_stats(self):
        return self.stats
```

### 7.2 事件追踪

```python
async def traced_handler(event_name, params):
    """带追踪的处理器"""
    trace_id = params.get("_trace_id", generate_trace_id())
    
    logger.debug(f"[{trace_id}] 处理事件: {event_name}")
    
    try:
        result = await do_something(params)
        params["result"] = result
        
        logger.debug(f"[{trace_id}] 处理成功")
        return (EventDecision.SUCCESS, params)
    except Exception as e:
        logger.error(f"[{trace_id}] 处理失败: {e}")
        return (EventDecision.PASS, params)
```

---

## 8. 最佳实践总结

### DO ✓

1. **遵守处理器协议**
   ```python
   # 正确
   return (EventDecision.SUCCESS, params)
   ```

2. **只修改参数值，不改 key**
   ```python
   # 正确
   params["count"] = params["count"] + 1
   ```

3. **使用适当的优先级**
   ```python
   # 验证 > 业务逻辑 > 副作用
   bus.subscribe("event", validate, priority=100)
   bus.subscribe("event", process, priority=50)
   bus.subscribe("event", log, priority=10)
   ```

4. **为后台任务使用 publish_sync**
   ```python
   # 正确
   bus.publish_sync("background_task", params)
   ```

### DON'T ✗

1. **不要在处理器中发起阻塞操作**
   ```python
   # 错误
   time.sleep(10)  # 会阻塞整个事件链
   
   # 正确
   await asyncio.sleep(10)
   ```

2. **不要添加或删除参数 key**
   ```python
   # 错误
   del params["key"]
   params["new_key"] = "value"
   
   # 正确
   params["key"] = None
   params["new_key"] = params.pop("old_key")
   ```

3. **不要造成事件递归**
   ```python
   # 危险（可能递归）
   await bus.publish("event", params)
   
   # 安全（后台异步）
   bus.publish_sync("event", params)
   ```

4. **不要在处理器中忽视异常**
   ```python
   # 错误
   await risky_operation()
   
   # 正确
   try:
       await risky_operation()
   except Exception as e:
       logger.error(f"处理失败: {e}")
       return (EventDecision.PASS, params)
   ```

---

## 相关资源

- [Event 主文档](./README.md) - API 和基本用法
- [核心实现](./core.md) - 内部机制
- [Concurrency 模块](../concurrency/README.md) - 后台任务
- [Logger 模块](../logger/README.md) - 日志记录
