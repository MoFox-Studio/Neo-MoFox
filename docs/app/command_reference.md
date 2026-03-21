# Runtime 命令手册

本文档对应 src/app/runtime/command_parser.py 中注册的交互命令。

## 命令列表

- /help
- /status
- /reload [plugin_name]
- /stop
- /plugins
- /tasks
- /ui level [minimal|standard|verbose]

## 命令说明

### /help

显示命令与说明表。

### /status

展示当前 Bot 状态，包括：

- 是否已初始化
- 是否运行中
- 已加载插件数
- 加载失败插件数
- 当前活动任务数

### /reload [plugin_name]

- 不带参数：重载全部插件。
- 带插件名：只重载指定插件。

说明：单插件重载会先卸载再加载；全量重载按统一流程执行。

### /stop

请求停止 Bot 主循环，并进入统一关闭流程。

### /plugins

显示插件加载结果与状态信息。

### /tasks

显示任务统计，便于排查异步任务堆积或泄露。

### /ui level [minimal|standard|verbose]

动态切换 UI 显示级别：

- minimal: 最小输出，适合日志采集。
- standard: 默认级别，平衡信息量与可读性。
- verbose: 显示实时仪表盘和更详细状态。

## 输入行为说明

- 非斜杠开头输入会被判定为未知命令。
- 命令输入由后台线程读取，并通过队列与异步主循环对接。
- EOF 或中断信号会触发循环退出。

## 故障排查

- 命令无响应：确认主循环仍在运行，且未进入 shutdown。
- /reload 失败：优先检查插件 manifest、依赖安装结果和加载日志。
- /ui level 切换异常：确认参数值为 minimal/standard/verbose。
