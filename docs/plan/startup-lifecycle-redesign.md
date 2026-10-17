# 启动循环重写：任务注册前置与显式初始化

> 状态：设计阶段（未实施）

## 1. 背景

当前生命周期边界不清晰，导致定时任务注册依赖运行时序竞争：

1. `Bot.initialize()` 加载插件并发布 `ON_ALL_PLUGIN_LOADED`（`bot.py:_load_plugins`）。
2. 插件在 `on_plugin_loaded()` 中尝试注册定时任务（如 `emoji_sender/plugin.py`）。
3. 但 `UnifiedScheduler.start()` 要到 `Bot.run()` 才执行（`bot.py:run` L823）。
4. `create_schedule()` 在调度器未运行时直接抛错：

   ```python
   # src/kernel/scheduler/core.py L762
   if not self._running:
       raise RuntimeError("调度器未运行，请先调用 start()")
   ```

5. 因此插件只能创建后台协程反复轮询等待 scheduler 就绪：

   ```python
   # plugins/emoji_sender/plugin.py _register_schedule_when_ready
   for attempt in range(600):
       try:
           await scheduler.create_schedule(...)
       except RuntimeError:
           await asyncio.sleep(0.5)
   ```

6. `MediaManager` 存在同类问题：构造函数内直接启动清理调度协程，且任何代码调用
   `get_media_manager()` 都会触发懒加载实例化，绕过 Bot 生命周期：

   ```python
   # src/core/managers/media_manager/manager.py L87
   start_cleanup_scheduler_async(self._cleanup)

   # cleanup.py L225 —— 构造即后台启动，异常只能事后出现
   asyncio.create_task(cleanup.start_scheduler())
   ```

## 2. 问题清单

| # | 问题 | 位置 | 后果 |
|---|------|------|------|
| P1 | `create_schedule()` 禁止未运行时注册 | `scheduler/core.py` L762 | 插件被迫后台轮询等待 |
| P2 | 插件注册定时任务靠 `task_manager` 后台协程 + 600 次重试 | `emoji_sender/plugin.py` L182–229 | 注册结果与插件加载状态脱节；失败静默；卸载需额外取消协程 |
| P3 | `MediaManager.__init__()` 内隐式启动后台任务 | `media_manager/manager.py` L87 | 可能早于数据库/scheduler 初始化；重载产生重复任务；异常只在后台出现 |
| P4 | `get_media_manager()` 懒加载绕过生命周期 | `media_manager/manager.py` L387 | 无显式初始化入口被 Bot 掌控；`initialize_media_manager()` 存在但从未被调用 |
| P5 | `run()` 中散落启动逻辑（scheduler、日志清理、云端遥测） | `bot.py` L822–843 | 启动顺序靠代码位置约定，无契约校验 |
| P6 | `shutdown()` 未显式关闭媒体管理器 | `bot.py` L1089+ | 媒体清理任务只随 scheduler.stop() 清空而消失，无独立释放 |

## 3. 目标生命周期

```text
创建 Bot
  ↓
initialize()                 —— 构建与注册（可验证，无运行态循环）
  ├─ 配置 / 日志 / EventBus / TaskManager
  ├─ 数据库 → schema 对齐 → VectorDB / Storage
  ├─ 显式初始化 MediaManager（构造 + initialize()）
  ├─ 创建 Scheduler（不 start）
  ├─ Core 组件初始化（Receiver / Sink / Managers / MCP / HTTP Server）
  ├─ 插件发现 → 依赖安装 → 加载
  ├─ 插件直接注册所有定时任务（无需等待）
  └─ 启动前契约校验

run()                        —— 运行态（只启动循环）
  ├─ scheduler.start()       —— 已注册任务在此后正常生效
  ├─ 启动日志清理 / 云端遥测发送循环
  ├─ 发布 ON_START
  ├─ 信号处理器 / 命令解析器
  └─ 主循环

shutdown()                   —— 逆序显式释放
  ├─ 发布 ON_STOP → 卸载插件
  ├─ media_manager.shutdown()
  ├─ 停止日志清理 → scheduler.stop()
  ├─ HTTP / MCP / WatchDog / TaskManager
  └─ 数据库 / VectorDB / LLM 统计 / 云遥测
```

核心原则：

> **构造对象不启动后台任务；初始化阶段完成依赖准备与任务注册；运行阶段只启动循环；关闭阶段按逆序显式释放。**

## 4. 改动设计

### 4.1 Scheduler 支持"先注册，后启动"（P1）

`UnifiedScheduler` 的 `_running` 只表达"检查循环正在运行"，不适合作为任务注册权限。
改为：调度器实例创建后即可 `create_schedule()`，任务先入表；`start()` 只启动
`_check_loop` / `_cleanup_loop`，首轮检查自然触发存量任务。

```python
# scheduler/core.py
async def create_schedule(self, ...) -> str:
    # 删除运行时拦截；未启动时任务状态为 PENDING，start() 后正常触发
    ...

@property
def is_started(self) -> bool:
    """start() 是否被调用过（任务注册永远允许，此状态供诊断/UI 使用）。"""
```

语义确认：

- 一次性任务 `delay_seconds=0`：在 `start()` 后第一次 `_check_and_trigger_tasks` 触发；
  `created_at` 在 `initialize()` 阶段写入，`elapsed >= 0` 恒成立，符合"启动后尽快执行"直觉。
- 周期任务 `interval_seconds`：`_scheduled_trigger_time = created_at + interval`，
  首次触发在启动后一个完整周期，与现状一致。

**风险**：`stop()` 会 `_tasks.clear()`，若存在"stop 后仍注册"的调用方会丢任务。
缓解：`stop()` 后调用 `create_schedule()` 记录 WARNING（允许但提示），不抛错。

### 4.2 MediaManager 显式初始化（P3 / P4）

构造函数剥离一切副作用，新增显式生命周期：

```python
class MediaManager:
    def __init__(self) -> None:
        # 只构造子组件，不启动任何后台任务、不注册 schedule
        ...

    async def initialize(self) -> None:
        """注册媒体清理定时任务（数据库此时已就绪）。"""
        self._cleanup = MediaCleanup(self._config)
        await self._cleanup.start_scheduler()

    async def shutdown(self) -> None:
        """移除媒体清理任务。"""
        scheduler = get_unified_scheduler()
        for schedule_id in (
            self._cleanup.cache_cleanup_task_id,
            self._cleanup.file_cleanup_task_id,
        ):
            if schedule_id:
                try:
                    await scheduler.remove_schedule(schedule_id)
                except Exception:
                    pass
```

- 删除 `start_cleanup_scheduler_async()`（`cleanup.py` L214–227），或降级为仅兼容层且不再被内部调用。
- `get_media_manager()` 保留懒加载兜底（供测试与极端调用方），但 Bot 走显式路径：

```python
# bot.py _initialize_kernel，放在数据库 schema 对齐之后
from src.core.managers.media_manager import initialize_media_manager

self.media_manager = initialize_media_manager()
await self.media_manager.initialize()
self.ui.update_phase_status("媒体管理器", "已初始化")
```

- `Bot.__init__` 增加属性声明：`self.media_manager: MediaManager | None = None`。

初始化顺序依据：`MediaRepository` 走数据库，`MediaFileStore` 需要落盘目录，
因此必须在 **数据库之后、插件加载之前**。

### 4.3 emoji_sender 去除轮询等待（P2）

scheduler 支持运行前注册后，插件加载即注册：

```python
async def on_plugin_loaded(self) -> None:
    ...
    await self._register_schedule()   # 直接 await，不再后台协程

async def _register_schedule(self) -> None:
    scheduler = get_unified_scheduler()
    interval = int(self.config.scheduler.interval_seconds)
    once_id = await scheduler.create_schedule(
        callback=self._ingest_job,
        trigger_type=TriggerType.TIME,
        trigger_config={"delay_seconds": 0},
        is_recurring=False,
        task_name="emoji_sender_ingest_once",
        force_overwrite=True,
    )
    recurring_id = await scheduler.create_schedule(
        callback=self._ingest_job,
        trigger_type=TriggerType.TIME,
        trigger_config={"interval_seconds": interval},
        is_recurring=True,
        task_name="emoji_sender_ingest_recurring",
        force_overwrite=True,
    )
    self._schedule_ids = [once_id, recurring_id]
```

删除：`_register_schedule_when_ready`、`self._register_task_id`、600 次重试循环、
`on_plugin_unloaded` 中的注册协程取消逻辑。

收益：注册失败直接体现在插件加载结果中；不存在"加载成功但任务几分钟后仍未注册"的窗口。

### 4.4 bot.py 阶段化重写（P5 / P6）

`initialize()` 拆为明确的内部阶段方法：

```python
async def _initialize_kernel(self) -> None: ...          # 配置/日志/事件/任务/DB/向量/存储/媒体管理器
async def _initialize_media_manager(self) -> None: ...   # 显式媒体管理器初始化
async def _initialize_core(self) -> None: ...            # Core managers / MCP / HTTP
async def _load_and_register_plugins(self) -> None: ...  # 发现→依赖→加载（定时任务在此注册）
async def _validate_startup_contract(self) -> None: ...  # 启动前断言
```

`run()` 只保留运行态启动：

```python
async def run(self) -> None:
    self._require_initialized()

    self._running = True

    await self.scheduler.start()
    await self._start_runtime_services()   # 日志清理 + 云端遥测发送循环

    await self.event_bus.publish(EventType.ON_START)

    self._install_signal_handler()
    self._create_command_parser()
    await self._run_command_loop()
```

`shutdown()` 关闭顺序（媒体管理器在 scheduler 停止前显式移除任务）：

```python
await publish(ON_STOP)
await unload_plugins()
await media_manager.shutdown()
await stop_logger_cleanup()
await scheduler.stop()
await stop_http_server() / stop_mcp() / stop_watchdog() / stop_task_manager()
await close_database() / close_vector_db() / close_llm_stats() / close_cloud_telemetry()
```

### 4.5 启动契约校验（新增）

插件加载结束后断言核心依赖齐备，并输出已注册任务清单供人工核对：

```python
def _validate_startup_contract(self) -> None:
    assert self.scheduler is not None
    assert self.media_manager is not None
    assert self.event_bus is not None
    assert self.task_manager is not None

    # 诊断日志：启动前已注册的定时任务
    for name in self.scheduler.list_schedule_names():
        self.logger.info(f"启动前已注册定时任务: {name}")
```

需在 `UnifiedScheduler` 上补充 `list_schedule_names()`（遍历 `_tasks_by_name`）。

## 5. 实施顺序

| 步骤 | 内容 | 涉及文件 |
|---|------|---------|
| 1 | scheduler 允许未运行注册 + `is_started` + `list_schedule_names()` + stop 后注册告警 | `src/kernel/scheduler/core.py` |
| 2 | `MediaManager` 构造去副作用，新增 `initialize()` / `shutdown()` | `src/core/managers/media_manager/manager.py` |
| 3 | 删除/降级 `start_cleanup_scheduler_async` | `src/core/managers/media_manager/cleanup.py` |
| 4 | Bot 显式初始化媒体管理器 + 阶段化重写 + 契约校验 | `src/app/runtime/bot.py` |
| 5 | emoji_sender 改直接注册 | `plugins/emoji_sender/plugin.py` |
| 6 | 测试补齐 | `test/kernel/test_scheduler.py`、`test/core/managers/test_media_manager.py` 等 |

## 6. 测试要点

- scheduler 未启动时注册一次性 / 周期任务，`start()` 后正常触发；
- `delay_seconds=0` 的一次性任务在 `start()` 后首轮检查即执行；
- `MediaManager()` 构造期间不创建任何 asyncio task（无 `start_cleanup_scheduler_async` 调用）；
- `await media_manager.initialize()` 注册两个清理任务；`await shutdown()` 后任务移除；
- 重复 `initialize()` 不产生重复任务（`force_overwrite` 或幂等守卫）；
- emoji_sender `on_plugin_loaded()` 返回后，`scheduler` 中已存在 `emoji_sender_ingest_*` 两个任务；
- `Bot.run()` 进入主循环前无任何后台轮询协程在等 scheduler；
- 插件卸载后其定时任务全部移除。

## 7. 迁移与兼容

- `get_media_manager()` 懒加载保留，但核心路径（converter / sender / stream_manager / media_api）
  均在 Bot 显式初始化之后才会被触发，行为不变。
- `create_schedule()` 删除 RuntimeError 拦截属行为变更：第三方插件若依赖
  "未启动时抛错"的防御逻辑，需依赖 `force_overwrite` / 任务名判重来保证幂等（现状已如此）。
- `ON_START` 仍发布于所有运行态服务启动之后，订阅方语义不变。
