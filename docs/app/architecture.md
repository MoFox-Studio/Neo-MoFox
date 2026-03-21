# App 层架构总览

本文档说明 src/app 在三层架构中的角色、职责边界与关键调用链。

## 分层定位

- kernel: 提供通用技术能力（配置、日志、并发、数据库、存储等）。
- core: 提供业务域能力（组件、管理器、传输、模型、提示词）。
- app: 负责装配与运行时，组织启动、运行、关闭生命周期。

app 层原则：只做编排，不重复实现 core/kernel 已有能力。

## 目录职责

- src/app/runtime: 运行时生命周期管理。
- src/app/plugin_system: 面向插件作者的稳定入口（base/api/types）。

## 启动调用链

1. main.py 读取 config/core.toml 的 bot.ui_level。
2. main.py 创建 Bot 实例并调用 Bot.start()。
3. Bot.initialize() 完成 kernel/core/plugin 初始化。
4. Bot.run() 进入交互主循环。
5. 触发退出条件后由 Bot.shutdown() 统一回收资源。

## 运行时关键状态

Bot 维护以下关键状态：

- _initialized: 是否完成初始化。
- _running: 是否处于运行循环。
- _shutdown_requested: 是否已进入关闭流程。
- _stats: 插件、任务、数据库、调度器等统计信息。

## 插件系统在 app 层的作用

- 提供插件作者统一导入路径。
- 通过 API 聚合减少插件代码对底层路径的依赖。
- 为未来升级保留兼容层，不将底层实现细节暴露为公共契约。

## 设计约束

- 运行时异步任务优先通过 task_manager 管理。
- 插件组件签名遵循 plugin_name:component_type:component_name。
- 文档更新应跟随入口和导出变化同步推进。
