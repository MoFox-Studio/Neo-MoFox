# 高级用法与最佳实践

本文档介绍 Scheduler 模块的高级用法和最佳实践。

## 目录

1. [并发控制](#并发控制)
2. [错误处理](#错误处理)
3. [性能优化](#性能优化)
4. [监控和日志](#监控和日志)
5. [实战案例](#实战案例)

---

## 并发控制

### 限制并发任务数

默认情况下，调度器允许最多 100 个任务并发执行。你可以根据系统资源调整：

```python
from kernel.scheduler import SchedulerConfig, UnifiedScheduler

# 限制只能同时执行 10 个任务
config = SchedulerConfig(
    max_concurrent_tasks=10,
    enable_task_semaphore=True
)

scheduler = UnifiedScheduler(config)
await scheduler.start()
```

### 使用信号量控制资源访问

如果任务涉及有限的资源（如数据库连接），可以通过调整并发数来控制：

```python
# 假设数据库连接池只有 5 个连接
config = SchedulerConfig(
    max_concurrent_tasks=5,
    enable_task_semaphore=True
)

scheduler = UnifiedScheduler(config)

# 创建 20 个任务，但最多只有 5 个会同时运行
for i in range(20):
    await scheduler.create_schedule(
        callback=database_operation,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 1},
        task_name=f"db_task_{i}"
    )
```

### 禁用信号量（不推荐）

如果你确定不需要并发控制，可以禁用信号量以提高性能：

```python
config = SchedulerConfig(
    enable_task_semaphore=False  # 禁用信号量
)
```

**警告**：禁用信号量可能导致系统资源耗尽。仅在确信所有任务都很轻量级时使用。

---

## 错误处理

### 1. 处理任务执行失败

```python
async def fragile_task():
    """容易失败的任务"""
    if random.random() < 0.3:  # 30% 概率失败
        raise Exception("模拟失败")
    print("任务成功执行")

# 创建任务，启用重试
schedule_id = await scheduler.create_schedule(
    callback=fragile_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 1},
    task_name="fragile",
    max_retries=3,  # 失败后最多重试 3 次
    timeout=10.0
)

# 监控执行情况
task = await scheduler.get_schedule(schedule_id)
print(f"成功: {task.success_count}, 失败: {task.failure_count}")

# 查看最后的错误
if task.last_error:
    print(f"最后一次错误: {task.last_error}")
```

### 2. 处理超时

```python
async def slow_task():
    """很慢的任务"""
    await asyncio.sleep(15)  # 睡 15 秒

# 设置较短的超时
schedule_id = await scheduler.create_schedule(
    callback=slow_task,
    trigger_type=TriggerType.TIME,
    trigger_config={"delay_seconds": 1},
    timeout=5.0  # 5 秒超时
)

# 监控超时情况
async def monitor_timeouts():
    while True:
        task = await scheduler.get_schedule(schedule_id)
        if task:
            stats = await scheduler.get_statistics()
            if stats['total_timeouts'] > 0:
                print(f"发生 {stats['total_timeouts']} 次超时")
        await asyncio.sleep(5)

asyncio.create_task(monitor_timeouts())
```

### 3. 记录详细的执行信息

```python
async def log_execution(schedule_id: str):
    """记录任务的详细执行信息"""
    task = await scheduler.get_schedule(schedule_id)
    if not task:
        return
    
    print(f"\n=== 任务: {task.task_name} ===")
    print(f"状态: {task.status.value}")
    print(f"触发次数: {task.trigger_count}")
    print(f"成功: {task.success_count}")
    print(f"失败: {task.failure_count}")
    
    if task.execution_history:
        latest = task.execution_history[-1]
        print(f"\n最近执行:")
        print(f"  ID: {latest.execution_id}")
        print(f"  状态: {latest.status.value}")
        print(f"  开始: {latest.started_at.isoformat()}")
        if latest.ended_at:
            print(f"  结束: {latest.ended_at.isoformat()}")
        print(f"  耗时: {latest.duration:.3f}s")
        if latest.error:
            print(f"  错误: {latest.error}")
```

---

## 性能优化

### 1. 减少检查频率

如果任务不需要频繁检查，可以增加 `check_interval`：

```python
# 默认每 1 秒检查一次
# 如果任务触发的最小间隔是 10 秒，可以改为每 5 秒检查一次
config = SchedulerConfig(
    check_interval=5.0,  # 减少 CPU 占用
)
```

### 2. 清理策略

调整已完成任务的清理策略：

```python
config = SchedulerConfig(
    cleanup_interval=300.0,  # 每 5 分钟清理一次
    keep_completed_tasks=50   # 只保留 50 个已完成任务的记录
)
```

### 3. 禁用指标收集（如果不需要）

虽然调度器本身会记录执行统计，但如果不需要详细的执行历史，可以减少记录：

```python
# 这需要自定义配置逻辑，当前版本未提供
# 但你可以通过清理执行历史来节省内存：

async def cleanup_old_history():
    """定期清理旧的执行历史"""
    tasks = await scheduler.list_all_schedules()
    for task in tasks:
        if len(task.execution_history) > 5:
            task.execution_history = task.execution_history[-5:]
```

### 4. 使用同步函数时的性能考虑

```python
# ✓ 好：异步函数，不阻塞事件循环
async def async_task():
    await asyncio.sleep(1)
    return "done"

# △ 可以接受：同步函数，但会在线程池中运行
def sync_task():
    time.sleep(1)
    return "done"

# ✗ 差：同步函数中进行阻塞 I/O
def bad_sync_task():
    requests.get("https://example.com")  # 阻塞线程
```

---

## 监控和日志

### 1. 定期统计

```python
async def periodic_stats():
    """每分钟打印一次统计信息"""
    while True:
        stats = await scheduler.get_statistics()
        print(f"""
调度器统计:
  运行时长: {stats['uptime_seconds']:.1f}s
  总执行: {stats['total_executions']}
  失败: {stats['total_failures']}
  超时: {stats['total_timeouts']}
  活跃任务: {stats['active_tasks']}
        """)
        await asyncio.sleep(60)

# 在后台运行
asyncio.create_task(periodic_stats())
```

### 2. 任务健康检查

```python
async def health_check():
    """检查是否有任务持续失败"""
    tasks = await scheduler.list_all_schedules()
    
    for task in tasks:
        if task.failure_count > 5:
            print(f"⚠️ 警告: 任务 '{task.task_name}' 已失败 {task.failure_count} 次")
        
        if task.status == TaskStatus.TIMEOUT:
            print(f"⏱️ 超时: 任务 '{task.task_name}' 执行超时")
```

### 3. 性能监控

```python
async def performance_monitor():
    """监控任务的性能"""
    tasks = await scheduler.list_all_schedules()
    
    for task in tasks:
        if task.trigger_count > 0:
            avg_time = task.total_execution_time / task.trigger_count
            success_rate = task.success_count / task.trigger_count
            
            print(f"{task.task_name}:")
            print(f"  平均耗时: {avg_time:.3f}s")
            print(f"  成功率: {success_rate:.1%}")
```

---

## 实战案例

### 案例 1：定时数据备份

```python
async def backup_database():
    """备份数据库"""
    print("正在备份数据库...")
    try:
        # 假设这是备份逻辑
        await asyncio.sleep(2)
        print("备份完成")
    except Exception as e:
        print(f"备份失败: {e}")
        raise

async def setup_backup():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    # 每天凌晨 2 点执行备份
    tomorrow_2am = (datetime.now() + timedelta(days=1)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    
    await scheduler.create_schedule(
        callback=backup_database,
        trigger_type=TriggerType.TIME,
        trigger_config={
            "trigger_at": tomorrow_2am,
            "interval_seconds": 86400  # 每 24 小时重复
        },
        is_recurring=True,
        task_name="daily_backup",
        timeout=3600.0,  # 最多 1 小时
        max_retries=2
    )
    
    print("备份任务已创建")
```

### 案例 2：健康检查与自动恢复

```python
class ServiceMonitor:
    def __init__(self):
        self.service_healthy = True
    
    async def health_check(self):
        """检查服务健康状态"""
        if not self.service_healthy:
            print("检测到服务异常，尝试恢复...")
            self.service_healthy = await self.recover()
    
    async def recover(self):
        """恢复服务"""
        try:
            # 恢复逻辑
            await asyncio.sleep(1)
            print("服务已恢复")
            return True
        except Exception:
            print("恢复失败")
            return False

async def setup_monitor():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    monitor = ServiceMonitor()
    
    # 每 10 秒检查一次服务健康
    await scheduler.create_schedule(
        callback=monitor.health_check,
        trigger_type=TriggerType.TIME,
        trigger_config={"interval_seconds": 10},
        is_recurring=True,
        task_name="health_check"
    )
```

### 案例 3：批量任务处理

```python
class BatchProcessor:
    def __init__(self, batch_size: int = 100):
        self.batch_size = batch_size
        self.queue = []
    
    async def process_batch(self):
        """处理一批任务"""
        if not self.queue:
            print("队列为空，跳过处理")
            return
        
        batch = self.queue[:self.batch_size]
        self.queue = self.queue[self.batch_size:]
        
        print(f"处理 {len(batch)} 个任务")
        # 处理逻辑
        for item in batch:
            print(f"  处理: {item}")

async def setup_batch_processor():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    processor = BatchProcessor(batch_size=50)
    
    # 模拟添加任务到队列
    async def add_tasks():
        for i in range(200):
            processor.queue.append(f"task_{i}")
            await asyncio.sleep(0.1)
    
    # 启动添加任务
    asyncio.create_task(add_tasks())
    
    # 每 5 秒处理一批
    await scheduler.create_schedule(
        callback=processor.process_batch,
        trigger_type=TriggerType.TIME,
        trigger_config={"interval_seconds": 5},
        is_recurring=True,
        task_name="batch_processor",
        timeout=60.0
    )
```

### 案例 4：条件驱动的初始化

```python
class AppInitializer:
    def __init__(self):
        self.database_ready = False
        self.cache_ready = False
    
    async def check_init_ready(self) -> bool:
        """检查是否可以初始化"""
        return self.database_ready and self.cache_ready
    
    async def do_initialization(self):
        """执行初始化"""
        print("所有依赖已就绪，执行初始化...")
        # 初始化逻辑
        await asyncio.sleep(1)
        print("初始化完成")

async def setup_conditional_init():
    scheduler = get_unified_scheduler()
    await scheduler.start()
    
    initializer = AppInitializer()
    
    # 创建条件触发的初始化任务
    await scheduler.create_schedule(
        callback=initializer.do_initialization,
        trigger_type=TriggerType.CUSTOM,
        trigger_config={
            "condition_func": initializer.check_init_ready
        },
        is_recurring=False,  # 只执行一次
        task_name="app_initialization"
    )
    
    # 模拟服务准备
    async def prepare_services():
        await asyncio.sleep(2)
        initializer.database_ready = True
        print("数据库已准备")
        
        await asyncio.sleep(1)
        initializer.cache_ready = True
        print("缓存已准备")
    
    asyncio.create_task(prepare_services())
```

---

## 总结

- **并发控制**：使用信号量限制并发数，保护有限资源
- **错误处理**：使用重试和超时保护，确保系统稳定
- **性能优化**：调整检查间隔和清理策略，平衡精度和性能
- **监控**：定期检查任务健康状态和性能指标
- **最佳实践**：为关键任务设置足够的超时和重试

## 相关资源

- [主文档](./README.md) - 基础用法
- [Core 模块](./core.md) - API 参考
- [Types 定义](./types.md) - 类型定义
