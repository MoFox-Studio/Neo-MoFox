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
- LLM 预检：按配置安排 core 的通用非生成探测，只消费安全结果与覆盖计数；协议、SDK、认证和资源由 kernel 能力处理，见 [技术边界](../llm/model_client/runtime.md)。
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
2. 发布 ON_STOP，停止 HTTP 入口；先停止看门狗防复活，再停止聊天流（使用 Bot 持有的 `_stream_loop_manager` 实例）。
3. 观察聊天任务：捕获 TaskManager 中仍登记的聊天 daemon 句柄（保留强引用），
   仅取消尚未 cancelling 的任务，等待所有任务完成。分别检查已完成任务的异常和未完成的超时任务。
   若该步骤失败，标记有 pending 任务。
4. 按逆序逐个卸载插件，每个插件独立处理超时及异常。
5. 移除日志、媒体清理任务，再停止调度器。
6. 关闭 MCP、云端遥测发送循环，取消并观察任务管理器持有的任务（仅取消尚未 cancelling 的任务，排除协调者和关闭步骤本身）。
   若任务管理器步骤失败，标记有 pending 任务。
7. 在所有 LLM 任务退出后回收内核默认模型资源。若有 pending 任务，跳过此步骤以保留可能被使用的模型池。
8. 关闭主数据库、向量库和 LLM 统计存储。若有 pending 任务，跳过这些步骤以保留依赖资源。
9. 记录关闭结果，解绑遥测订阅，关闭本地遥测存储。若有 pending 任务，跳过遥测存储关闭。
10. 在 finally 中恢复 DNS 和默认线程池，关闭日志、命令解析器，最后恢复信号处理器（仅同步操作）。

`initialize()`、内核/Core/插件编排、`run()`、运行服务启动、`shutdown()` 和
`start()` 均有编号步骤注释；各资源函数的文档字符串说明依赖和释放职责。

`shutdown(timeout=30.0)` 的 timeout 表示**每个异步清理步骤**的等待上限，
包括每个插件，不代表整个关闭流程的总时限。内部步骤观察返回成功或失败，公开 `shutdown()` 仍返回 `None`。
每步由 TaskManager 创建受管任务，通过 `asyncio.wait(..., timeout=timeout)` 有界观察；
到期只请求一次取消，不无限等待该步骤的 finally。吞掉取消的步骤仍由强引用跟踪，
不会据此认定资源已经释放；事件循环中的同步阻塞代码仍无法被抢占。
一个步骤失败或超时后仍尝试独立清理，最后汇总固定阶段和错误类型为 `BotShutdownError`，
不拼接供应商或业务错误原文；出错时不会输出“关闭完成”。

全局资源在初始化调用前登记，以便释放初始化中途失败产生的资源；未尝试初始化的
数据库不会被关闭，关闭时不通过懒加载 getter 新建聊天流管理器。任务清理仅针对
TaskManager 持有的任务（排除当前关闭任务），不会遍历并取消宿主事件循环的所有任务。
线程池也只释放 Bot 创建的实例，并恢复先前的默认线程池。

聊天任务停止策略：Bot 从 TaskManager 捕获仍登记的聊天句柄（包含尚未清理的已退出任务），
在来源封闭后仅取消尚未 cancelling 的任务（避免二次取消打断 lease 清理）。
owner 等待使用不传播取消的观察，步骤预算由外层限制；没有 pending 时仍检查已退出任务的普通错误。
任务退出只证明不再运行，清理失败不宣称资源已成功回收。迟到任务的异常由完成回调取回，
随后释放 Bot 保存的强引用；不提供新的 StopSnapshot 公共接口。

资源保留策略：若聊天来源、聊天任务或任务管理器步骤失败，或仍有 owner／关闭步骤未退出，
则保守跳过默认模型池、主数据库、向量库、LLM 统计和本地遥测的关闭。
即使其他独立步骤继续，不能把这些依赖资源的跳过报告为回收成功。

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
