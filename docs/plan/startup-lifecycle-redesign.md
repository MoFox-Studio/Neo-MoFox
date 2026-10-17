# 启动循环重写：任务注册前置与显式初始化

> 状态：调度器生命周期、Bot 启动和关闭阶段拆分已实施；插件迁移及组件内部生命周期完善待实施

本文是 `src/app/runtime/bot.py` 启动生命周期重构的设计基线。它描述目标顺序、组件边界和实施批次；代码改动应按“实施顺序”逐步落地，并在每个批次完成后更新本文状态和测试清单。

当前已完成：调度器支持运行前注册任务；媒体管理器不再在构造函数中创建后台任务，并由 Bot 显式初始化、启动和关闭；日志清理模块改为延迟导入调度器，避免初始化循环。

第二批已完成：Kernel 和 Core 按资源拆分初始化函数，媒体管理器归入 Core 阶段；
插件准备集中在 `_initialize_plugins()`；`run()` 拆为运行服务启动、事件发布、
信号/终端准备与命令循环，并增加必需资源校验。所有新增阶段方法具备文档字符串。
当前实际流程见 [Runtime 模块说明](../app/runtime.md)，下文尚未实施的设计草图不作为当前 API 保证。

第三批已完成：为各编排入口补齐编号步骤注释；关闭流程按资源拆分并隔离各步骤异常，
每个异步步骤及单个插件独立限时。聊天流先于插件和数据库停止，云端发送先于统计存储停止。
初始化前登记资源以覆盖部分失败，关闭时恢复原默认线程池，仅取消受管理任务。
终端与信号恢复独立兜底，关闭错误不会覆盖原始启动异常。

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

### 3.1 当前实现与目标实现的边界

当前已支持启动前注册调度任务；媒体管理器由 Bot 显式初始化，在调度器启动后调用
`start()` 注册清理任务，在调度器停止前调用 `shutdown()` 移除任务。
媒体构造函数不再创建后台协程。`Bot.run()` 已完成阶段化拆分，退出或启动异常时复位运行标记。

调度器当前执行约束：

- 首次 `start()` 前注册仅入表，时间任务和自定义条件任务不会自动执行。
- `trigger_schedule()` 和 `trigger_event()` 在未启动或关闭期间抛出 `RuntimeError`；事件不会缓存重放。
- 相对时间从注册时计算。启动时已到期的任务在首轮检查执行；周期任务跳过错过的周期，不逐次补跑。
- `stop()` 即使在首次启动前调用也会清空预注册任务及事件索引。
- 关闭期间和停止后拒绝注册；再次 `start()` 后可注册新任务，已清空任务不会恢复。
- 事件参数在入表及覆盖同名任务前校验，失败不会留下无效任务或移除原任务。

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
    """调度器检查循环是否正在运行。"""
```

语义确认：

- 一次性任务 `delay_seconds=0`：在 `start()` 后第一次 `_check_and_trigger_tasks` 触发；
  `created_at` 在 `initialize()` 阶段写入，`elapsed >= 0` 恒成立，符合"启动后尽快执行"直觉。
- 周期任务 `interval_seconds`：`_scheduled_trigger_time = created_at + interval`，
  首次触发按注册时间计算；初始化超过一个周期时，启动后的首轮检查即可触发。

`stop()` 会清空任务；关闭期间及停止后调用 `create_schedule()` 明确抛错，
避免调用方误以为任务将继续执行。需要重新运行时先 `start()`，再重新注册任务。

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

本节初始化、运行及关闭阶段拆分已实施；以下草图展示最初计划，实际顺序以 Runtime 文档为准。
实际方法名为 `_initialize_plugins()`、`_validate_startup_contract()`、
`_start_runtime_services()`、`_publish_start_event()`、`_install_signal_handler()`、
`_create_command_parser()` 和 `_run_command_loop()`。
`_start_runtime_services()` 包含调度器、媒体清理、日志清理和云端遥测启动。
`ON_ALL_PLUGIN_LOADED` 保留在 `_load_plugins()` 中，以兼容适配器及路由的事件订阅。

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
await stop_http_server()
await stop_stream_loops()
await unload_plugins()
await media_manager.shutdown()
await stop_logger_cleanup()
await scheduler.stop()
await stop_mcp() / close_cloud_telemetry() / stop_watchdog() / stop_task_manager()
await close_database() / close_vector_db() / close_llm_stats()
```

各步骤独立限时、汇总错误；最终同步资源恢复放在 finally 中，详情见 Runtime 文档。

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

第三批回归：`uv run pytest test/app/runtime test/kernel/test_scheduler_lifecycle.py -q --no-cov`，
结果为 73 passed、1 skipped。新增 `test/app/runtime/test_bot_shutdown.py` 覆盖关闭依赖顺序、
插件挂起超时后继续清理、部分初始化失败释放、未初始化资源跳过、终端异常和外部取消时
恢复信号、原始启动异常保留，以及无关任务和默认线程池的保护。

已执行第二批回归：`uv run pytest test/app/runtime test/kernel/test_scheduler_lifecycle.py -q --no-cov`，
结果为 65 passed、1 skipped。新增 `test/app/runtime/test_bot_lifecycle.py` 验证媒体先于插件初始化、
服务先于 ON_START 启动、服务失败不发布启动事件、必需依赖缺失阻止运行、
ON_START 可请求停止，以及完整入口在初始化失败后仍执行关闭。

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
