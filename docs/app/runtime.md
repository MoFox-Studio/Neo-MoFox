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
- LLM 预检：按配置可选执行 provider 健康检查。
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
3. 按逆序卸载插件。
4. 停止调度器与 HTTP 服务器。
5. 停止 WatchDog。
6. 取消并回收任务管理器任务。
7. 关闭数据库与向量数据库。
8. 关闭日志系统。

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
