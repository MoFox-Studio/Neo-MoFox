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

1. main.py 确认启动协议与遥测选择。
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
- 本地遥测、LLM 统计及云端遥测运行时准备。

`_initialize_kernel()` 只编排各资源的初始化方法。同步构造使用普通函数，
数据库连接等异步操作使用异步函数；每个方法说明其依赖和副作用。
调度器此时只用于注册任务，看门狗按配置启动以监控后续初始化。

### 阶段 2：Core 组件初始化

- `_initialize_media_manager()`：数据库就绪后显式准备媒体管理器。
- `_initialize_transport()`：初始化 MessageReceiver 与 SinkManager。
- 初始化 adapter/router/event/distribution 管理器。
- `_initialize_mcp_manager()`：准备 MCP 工具连接。
- `_initialize_http_server()`：在配置启用时启动 HTTPServer，供插件挂载路由。

### 阶段 3：插件发现与加载

- `_initialize_plugins()` 编排市场同步、发现、依赖安装和加载。
- 通过 PluginLoader.plan_plugins() 生成 load_order 与 manifests。
- 可选安装插件 Python 依赖（受 plugin_deps 配置控制）。
- 逐个插件加载，失败插件按容错策略继续后续加载。
- 全部加载后发布 ON_ALL_PLUGIN_LOADED 事件。
- `_validate_startup_contract()` 检查必需资源，记录已注册的定时任务。

重复调用已成功完成的 `initialize()` 会直接返回；关闭后的 Bot 不可复用。
此阶段仍可能建立外部连接或由插件事件启动适配器，不能视为完全无后台活动。

## 运行阶段

Bot.run() 的核心行为：

1. 检查初始化、运行和关闭状态，并验证必需资源。
2. `_start_runtime_services()` 依次启动调度器、媒体清理、日志清理、云端遥测发送循环。
3. `_publish_start_event()` 发布 ON_START。
4. `_install_signal_handler()` 注册信号处理器，`_create_command_parser()` 创建命令解析器。
5. `_run_command_loop()` 处理用户命令，遇到退出命令、关闭信号或取消时结束。

媒体启动方法要求调度器已运行。服务启动向外抛出异常时不发布 ON_START，
完整入口 `start()` 的 finally 执行关闭。日志清理和云端遥测启动失败沿用警告后继续的策略。
`run()` 退出时复位运行标记；信号处理器和命令解析器保留到 `shutdown()` 末尾，
保证关闭期间第二次 Ctrl+C 仍可强制退出。

## 关闭阶段

Bot.shutdown() 默认流程如下：

1. 设置停止标记，记录关闭开始遥测。
2. 发布 ON_STOP，停止 HTTP 入口与聊天流。
3. 按逆序逐个卸载插件，每个插件独立处理超时及异常。
4. 移除日志、媒体清理任务，再停止调度器。
5. 关闭 MCP、云端遥测发送循环和看门狗，取消并等待任务管理器持有的任务。
6. 关闭主数据库、向量库和 LLM 统计存储。
7. 记录关闭结果，解除遥测订阅，关闭本地遥测存储。
8. 在 finally 中恢复 DNS 和默认线程池，关闭日志、命令解析器，最后恢复信号处理器。

`initialize()`、内核/Core/插件编排、`run()`、运行服务启动、`shutdown()` 和
`start()` 均有编号步骤注释；各资源函数的文档字符串说明依赖和释放职责。

`shutdown(timeout=30.0)` 的 timeout 表示**每个异步清理步骤**的等待上限，
包括每个插件，不代表整个关闭流程的总时限。超时依赖协程配合取消，无法抢占
同步阻塞代码或吞掉取消的协程。一个步骤失败或超时后仍尝试后续步骤，最后
汇总为 `BotShutdownError`；出错时不会输出“关闭完成”。

全局资源在初始化调用前登记，以便释放初始化中途失败产生的资源；未尝试初始化的
数据库不会被关闭，关闭时不通过懒加载 getter 新建聊天流管理器。任务清理仅针对
TaskManager 持有的任务（排除当前关闭任务），不会遍历并取消宿主事件循环的所有任务。
线程池也只释放 Bot 创建的实例，并恢复先前的默认线程池。

外部取消关闭协程时仍会执行 finally 中的同步恢复，但尚未执行的异步清理步骤
不会继续。关闭请求具有幂等保护，重复调用直接返回，不表示重新尝试失败步骤。
`start()` 在已有启动异常时把关闭错误作为异常附注保留，避免覆盖最初的故障原因。

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
