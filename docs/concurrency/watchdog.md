# WatchDog 监控系统

## 概述

WatchDog 是一个独立的后台监控系统，在专用线程中运行，提供以下功能：

1. **聊天流心跳监控** - 检测聊天流驱动器的健康状态
2. **任务超时管理** - 自动取消超时的任务
3. **任务清理** - 定期清理已完成的任务
4. **系统健康检查** - 监控 WatchDog 自身的运行状态

**设计特点**：
- ✅ 独立线程运行，不阻塞主事件循环
- ✅ 自动心跳检测和重启机制
- ✅ 可配置的超时和警告阈值
- ✅ 与 TaskManager 紧密集成
- ✅ 日志输出便于调试

---

## 快速开始

### 启动 WatchDog

```python
from src.kernel.concurrency import get_watchdog

# 获取全局 WatchDog 实例
watchdog = get_watchdog()

# 启动监控
watchdog.start()

# 应用关闭时停止
watchdog.stop()
```

### 注册聊天流

```python
def on_stream_restart():
    print("聊天流已重启")

# 注册聊天流
heartbeat = watchdog.register_stream(
    stream_id="stream_123",
    tick_interval=1.0,
    warning_threshold=2.0,
    restart_threshold=5.0,
    restart_callback=on_stream_restart
)

# 定期发送心跳
watchdog.feed_dog("stream_123")

# 稍后注销
watchdog.unregister_stream("stream_123")
```

### 查看监控统计

```python
stats = watchdog.get_stats()
print(f"WatchDog 状态: {stats}")
# {'running': True, 'tick_interval': 1.0, 'registered_streams': 1, 'thread_alive': True}
```

---

## WatchDog 类

### 初始化

```python
watchdog = WatchDog(tick_interval: float = 1.0)
```

**参数**：
- `tick_interval`：WatchDog 自身的检查间隔（秒），默认 1.0

**说明**：
- 更小的 `tick_interval` 能更及时地检测问题，但消耗更多 CPU
- 通常保持默认值 1.0 秒即可

**示例**：

```python
# 更频繁的检查（每 0.5 秒）
watchdog = WatchDog(tick_interval=0.5)

# 较低频的检查（每 5 秒）
watchdog = WatchDog(tick_interval=5.0)
```

### 生命周期管理

#### start()

启动 WatchDog 监控线程。

```python
watchdog.start() -> None
```

**说明**：
- 创建并启动后台线程
- 如果已运行会抛出 `WatchDogError`
- 通常在应用启动时调用

**使用示例**：

```python
watchdog = get_watchdog()

try:
    watchdog.start()
    print("WatchDog 已启动")
except WatchDogError:
    print("WatchDog 已在运行")
```

#### stop()

停止 WatchDog 监控线程。

```python
watchdog.stop() -> None
```

**说明**：
- 停止后台线程
- 等待线程正常退出（最多 5 秒）
- 通常在应用关闭时调用

**使用示例**：

```python
import atexit

watchdog = get_watchdog()
watchdog.start()

# 注册应用关闭时的清理
atexit.register(watchdog.stop)
```

---

## 聊天流监控

### register_stream()

注册一个聊天流进行心跳监控。

```python
heartbeat = watchdog.register_stream(
    stream_id: str,
    tick_interval: float = 1.0,
    warning_threshold: float = 150.0,
    restart_threshold: float = 300.0,
    restart_callback: Callable | None = None
) -> StreamHeartbeat
```

**参数说明**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `stream_id` | `str` | - | 聊天流唯一标识符 |
| `tick_interval` | `float` | 1.0 | 正常 tick 间隔（秒） |
| `warning_threshold` | `float` | 150.0 | 警告阈值（秒） |
| `restart_threshold` | `float` | 300.0 | 重启阈值（秒） |
| `restart_callback` | `Callable \| None` | None | 触发重启时的回调函数 |

**阈值说明**：

- `warning_threshold` = 150.0
  - 距离上次心跳超过 150.0s 时输出警告
- `restart_threshold` = 300.0
  - 距离上次心跳超过 300.0s 时尝试重启

**返回值**：`StreamHeartbeat` 对象

**使用示例**：

```python
def on_restart():
    """聊天流重启回调"""
    print("检测到聊天流卡死，尝试重启...")
    # 执行重启逻辑
    restart_chat_stream()

# 注册聊天流
heartbeat = watchdog.register_stream(
    stream_id="chat_stream_001",
    tick_interval=2.0,      # 期望每 2 秒一次心跳
    warning_threshold=150.0,  # 超过 150 秒输出警告
    restart_threshold=300.0,  # 超过 300 秒尝试重启
    restart_callback=on_restart
)

print(f"聊天流已注册: {heartbeat}")
```

### feed_dog()

发送心跳，更新流的最后活动时间。

```python
watchdog.feed_dog(stream_id: str) -> None
```

**参数**：
- `stream_id`：聊天流 ID

**说明**：
- 应在聊天流的主循环中定期调用
- 用于告诉 WatchDog"我还活着"
- 如果流未注册，此调用被忽略

**使用示例**：

```python
async def chat_stream_driver():
    """聊天流驱动程序"""
    watchdog = get_watchdog()
    stream_id = "stream_123"
    
    # 在主循环中定期喂狗
    while True:
        # 执行聊天逻辑
        message = await get_next_message()
        process_message(message)
        
        # 发送心跳
        watchdog.feed_dog(stream_id)
        
        await asyncio.sleep(1)  # tick_interval = 1.0
```

### unregister_stream()

注销聊天流监控。

```python
watchdog.unregister_stream(stream_id: str) -> None
```

**参数**：
- `stream_id`：聊天流 ID

**说明**：
- 移除流的心跳监控
- 流停止时调用
- 如果流不存在，操作被忽略

**使用示例**：

```python
# 聊天流结束时
watchdog.unregister_stream("stream_123")
```

### get_stream_heartbeat()

获取聊天流的心跳信息。

```python
heartbeat = watchdog.get_stream_heartbeat(stream_id: str) -> StreamHeartbeat | None
```

**返回**：`StreamHeartbeat` 对象或 `None`（如果未注册）

**使用示例**：

```python
heartbeat = watchdog.get_stream_heartbeat("stream_123")

if heartbeat:
    print(f"最后心跳: {heartbeat.last_tick}")
    print(f"心跳间隔: {heartbeat.tick_interval}s")
else:
    print("流未注册")
```

---

## 任务监控

WatchDog 自动执行以下任务监控操作（不需要手动调用）：

### 任务超时检查

- 定期检查所有非守护任务
- 如果任务运行时间超过 `timeout` 设置，自动取消该任务
- 通过日志记录超时事件

**配置示例**：

```python
tm = get_task_manager()

# 创建 30 秒超时的任务
task = tm.create_task(
    long_operation(),
    name="long_task",
    timeout=30
)

# WatchDog 会在 30 秒后自动取消此任务
```

### 任务清理

- 定期检查已完成的任务
- 从管理器中删除已完成任务，释放内存
- 通过日志记录清理事件

---

## 监控统计

### get_stats()

获取 WatchDog 的统计信息。

```python
stats = watchdog.get_stats() -> dict[str, Any]
```

**返回字典包含**：

| 键 | 类型 | 说明 |
|---|------|------|
| `running` | `bool` | WatchDog 是否运行中 |
| `tick_interval` | `float` | Tick 检查间隔（秒） |
| `registered_streams` | `int` | 已注册的聊天流数量 |
| `thread_alive` | `bool` | 监控线程是否活跃 |

**使用示例**：

```python
stats = watchdog.get_stats()

print(f"运行状态: {'运行中' if stats['running'] else '已停止'}")
print(f"注册流数: {stats['registered_streams']}")
print(f"线程活跃: {'是' if stats['thread_alive'] else '否'}")
```

---

## 日志输出

WatchDog 会输出以下日志信息：

### 启动日志
```
[INFO] WatchDog 监控已启动 (tick间隔=1.0s)
```

### 心跳异常警告
```
[WARNING] 聊天流 'stream_001' 响应缓慢: 距离上次心跳 2.5s (正常间隔 1.0s)
```

### 心跳卡死警告
```
[WARNING] 聊天流 'stream_001' 可能已卡死: 距离上次心跳 5.5s，尝试重启...
```

### 任务超时警告
```
[WARNING] 任务 'long_task' (id=a1b2c3d4) 超时 (15.5s > 10.0s)，尝试取消...
```

### Tick 异常警告
```
[WARNING] WatchDog tick 间隔异常: 2.1s (预期 1.0s)
```

---

## 使用模式

### 模式 1: 基本设置

```python
from src.kernel.concurrency import get_watchdog

async def main():
    watchdog = get_watchdog()
    watchdog.start()
    
    try:
        # 应用逻辑
        await run_application()
    finally:
        watchdog.stop()

asyncio.run(main())
```

### 模式 2: 聊天流监控

```python
watchdog = get_watchdog()
watchdog.start()

# 注册聊天流
watchdog.register_stream(
    stream_id="user_chat_123",
    tick_interval=1.0,
    warning_threshold=3.0,
    restart_threshold=5.0,
    restart_callback=lambda: restart_stream("user_chat_123")
)

# 在聊天流驱动中定期喂狗
async def stream_driver():
    while stream_is_running:
        watchdog.feed_dog("user_chat_123")
        # 聊天处理逻辑
        await asyncio.sleep(1)
```

### 模式 3: 多个聊天流监控

```python
watchdog = get_watchdog()
watchdog.start()

# 注册多个聊天流
streams = ["stream_1", "stream_2", "stream_3"]

for stream_id in streams:
    watchdog.register_stream(
        stream_id=stream_id,
        tick_interval=1.0,
        restart_callback=lambda sid=stream_id: restart_stream(sid)
    )

# 定期为所有流喂狗
for stream_id in streams:
    watchdog.feed_dog(stream_id)
```

### 模式 4: 应用生命周期

```python
import atexit
from src.kernel.concurrency import get_watchdog

def shutdown():
    """应用关闭时的清理"""
    watchdog = get_watchdog()
    watchdog.stop()

# 在应用启动时
watchdog = get_watchdog()
watchdog.start()

# 注册关闭处理
atexit.register(shutdown)

# 应用运行
asyncio.run(main())
```

---

## StreamHeartbeat 数据类

心跳信息对象，存储聊天流的监控参数和状态。

```python
@dataclass
class StreamHeartbeat:
    stream_id: str                          # 流 ID
    last_tick: datetime                     # 最后心跳时间
    tick_interval: float                    # 正常 tick 间隔
    warning_threshold: float                # 警告阈值（秒）
    restart_threshold: float                # 重启阈值（秒）
    restart_callback: Callable | None       # 重启回调
```

---

## 最佳实践

### 1. 总是在应用启动时启动 WatchDog

```python
async def main():
    # 启动监控系统
    watchdog = get_watchdog()
    watchdog.start()
    
    try:
        # 应用逻辑
        await run_app()
    finally:
        watchdog.stop()
```

### 2. 为任务设置合理的超时时间

```python
tm = get_task_manager()

# 快速任务：5 秒超时
tm.create_task(quick_query(), timeout=5)

# 中等任务：30 秒超时
tm.create_task(api_call(), timeout=30)

# 长任务：120 秒超时
tm.create_task(batch_process(), timeout=120)
```

### 3. 定期检查 WatchDog 状态

```python
watchdog = get_watchdog()

# 定期检查
async def monitor_watchdog():
    while True:
        stats = watchdog.get_stats()
        if not stats['thread_alive']:
            print("ERROR: WatchDog 线程已死亡！")
            # 告警/重启逻辑
        
        await asyncio.sleep(30)
```

### 4. 为聊天流提供重启回调

```python
def on_stream_restart():
    """聊天流重启处理"""
    # 1. 停止当前流
    stop_current_stream()
    
    # 2. 清理资源
    cleanup_resources()
    
    # 3. 重启流
    start_new_stream()

watchdog.register_stream(
    stream_id="stream_123",
    restart_callback=on_stream_restart
)
```

### 5. 在应用关闭时清理

```python
import signal
import sys
from src.kernel.concurrency import get_watchdog

def signal_handler(sig, frame):
    """处理关闭信号"""
    watchdog = get_watchdog()
    watchdog.stop()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
```

---

## 常见问题

### Q: WatchDog 监控会影响性能吗？

A: 影响很小。WatchDog 在独立线程中运行，不会阻塞主事件循环。使用默认的 1.0s tick 间隔时，CPU 占用通常低于 1%。

### Q: 如何调整监控敏感度？

A: 通过调整 `tick_interval` 参数：

```python
# 更敏感的监控（每 0.1 秒检查一次）
watchdog = WatchDog(tick_interval=0.1)

# 更宽松的监控（每 5 秒检查一次）
watchdog = WatchDog(tick_interval=5.0)
```

### Q: 聊天流心跳如何配置？

A: 根据应用特点调整阈值：

```python
# 快速响应的流
watchdog.register_stream(
    stream_id="fast_stream",
    tick_interval=0.5,
    warning_threshold=1.0,  # 1 秒无响应警告
    restart_threshold=1.5   # 1.5 秒重启
)

# 稳定的流
watchdog.register_stream(
    stream_id="stable_stream",
    tick_interval=5.0,
    warning_threshold=15.0,  # 15 秒无响应警告
    restart_threshold=25.0   # 25 秒重启
)
```

### Q: 如何确认 WatchDog 正常工作？

A: 检查日志输出和统计信息：

```python
# 1. 查看日志（通常为 INFO 或 WARNING 级别）
# [INFO] WatchDog 监控已启动

# 2. 检查统计
stats = watchdog.get_stats()
assert stats['running'] == True
assert stats['thread_alive'] == True

# 3. 检查注册的流
streams = watchdog.get_stream_heartbeat("stream_id")
assert streams is not None
```

### Q: WatchDog 停止后如何重启？

A: 创建新实例或重新启动：

```python
watchdog = get_watchdog()
watchdog.stop()

# 方式 1: 重新启动同一实例
watchdog.start()

# 方式 2: 获取新实例
watchdog = WatchDog()
watchdog.start()
```

---

## 相关资源

- [Concurrency 主文档](./README.md) - 概览
- [TaskManager 详解](./task_manager.md) - 任务管理器
- [TaskGroup 详解](./task_group.md) - 任务组管理
- [类型定义](./types.md) - StreamHeartbeat 等类型
