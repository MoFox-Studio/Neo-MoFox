# Runtime 模块说明

本文档对应 src/app/runtime，描述 Bot 在 app 层的完整生命周期控制。

## 模块职责

- 负责系统初始化顺序编排（kernel -> core -> plugin）。
- 负责运行态事件循环、命令交互和信号处理。
- 负责关闭阶段的资源回收和优雅停机。

## 关键文件

- src/app/runtime/bot.py: 生命周期主协调器 Bot。
- src/app/runtime/command_parser.py: 交互命令读取与执行。
- src/app/runtime/console_ui.py: 控制台可视化输出与进度显示。
- src/app/runtime/signal_handler.py: SIGINT/SIGTERM 处理和强退策略。
- src/app/runtime/exceptions.py: Runtime 专用异常类型。

## 启动路径

1. main.py 从 config/core.toml 读取 ui_level。
2. main.py 创建 Bot 并调用 Bot.start()。
3. Bot.start() 顺序执行 initialize()、run()，最后进入 shutdown()。

## 初始化阶段

Bot.initialize() 采用单条进度链路，按阶段推进。

### 阶段 1：Kernel 初始化

- 配置系统：加载 core/model 配置。
- 日志系统：初始化 logger。
- LLM 预检：按配置安排 core 的通用非生成探测，只消费安全结果与覆盖计数；协议、SDK、认证和资源由 kernel 能力处理，见 [技术边界](../llm/model_client/runtime.md)。
- 事件总线、任务管理器、调度器、WatchDog。
- 数据库与 schema 对齐。
- 向量数据库、JSON 存储初始化。

### 阶段 2：Core 组件初始化

- 初始化 MessageReceiver 与 SinkManager。
- 初始化 adapter/router/event/distribution 管理器。
- 在配置启用时启动 HTTPServer，并尝试挂载 LLM Request Inspector。

### 阶段 3：插件发现与加载

- 通过 PluginLoader.plan_plugins() 生成 load_order 与 manifests。
- 可选安装插件 Python 依赖（受 plugin_deps 配置控制）。
- 逐个插件加载，失败插件按容错策略继续后续加载。
- 全部加载后发布 ON_ALL_PLUGIN_LOADED 事件。

## 运行阶段

Bot.run() 的核心行为：

1. 启动调度器并发布 ON_START 事件。
2. 按 UILevel 决定是否启动实时仪表盘。
3. 注册信号处理器。
4. 启动 CommandParser，循环执行用户命令。
5. 循环期间维护统计并更新 UI（Verbose 模式）。

## 关闭阶段

Bot.shutdown() 默认流程如下：

1. 设置停止标记，拒绝新工作。
2. 发布 ON_STOP 事件。
3. 停止聊天来源和 WatchDog，等待已捕获的活动聊天任务释放资源。
4. 按逆序卸载插件，停止调度器与 HTTP 服务器。
5. 取消并等待任务管理器的已捕获任务，排除当前 shutdown 任务。
6. 在共同截止时间内调用内核默认模型资源回收，不强关活动连接或外部注入客户端。
7. 关闭数据库与向量数据库。
8. 关闭日志系统；无论正常、失败或取消，独立恢复终端解析器与信号。

聊天来源的正常停止将“请求取消”与“观察退出”分离：已在取消清理的 owner 不追加取消，观察者超时或被取消不取消 owner；仍在退出的任务保留引用并可继续观察。管理器 `stop()` 仍返回 `None`、单流停止仍返回 `bool`，这些返回值不保证资源关闭成功；不提供新的状态快照接口。Bot 捕获 TaskManager 中仍登记的聊天句柄（包含尚未清理的已退出任务），等待其中活动者，并把普通退出异常报告为脱敏的关闭失败，而非以 `task.done()` 推断清理成功；已由任务管理器清除的历史失败不在此快照保证范围内。

共同预算只约束聊天停止、已捕获任务等待及默认模型资源回收等已包装步骤，不是整条 `shutdown()` 的硬超时保证。ON_STOP、插件卸载、调度器、HTTP、数据库等历史步骤不因此获得统一 deadline。

SignalHandler 策略：

- 第一次 Ctrl+C：请求优雅关闭。
- 3 秒内第二次 Ctrl+C：强制退出。

## 异常模型

- BotRuntimeError: runtime 基础异常。
- BotInitializationError: 初始化阶段失败。
- BotShutdownError: 关闭阶段失败。
- PluginLoadError: 插件加载失败。
- CommandExecutionError: 命令执行失败。

## 运行排障建议

- 初始化失败：优先查看日志输出中的阶段状态（配置、数据库、插件加载）。
- 插件依赖问题：检查 plugin_deps 配置及插件声明的 python_dependencies。
- 无法退出：检查 task_manager 活动任务是否持续新增。
- HTTP 路由不可用：确认 http_router.enable_http_router 和监听地址配置。
