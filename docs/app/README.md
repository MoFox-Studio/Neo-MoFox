# App 层文档

本目录记录 src/app 的运行时装配、插件系统对外接口以及运维操作指引。

## 文档导航

- [architecture](./architecture.md): app 层架构边界、目录职责和启动路径。
- [runtime](./runtime.md): Bot 生命周期、初始化阶段、关闭流程与故障排查。
- [plugin_system](./plugin_system.md): 插件作者可用入口、API 分类与扩展约束。
- [command_reference](./command_reference.md): 运行时交互命令说明与使用场景。

## 代码映射

- 启动入口：main.py
- 运行时主类：src/app/runtime/bot.py
- Runtime 导出：src/app/runtime/__init__.py
- 插件系统总入口：src/app/plugin_system/__init__.py
- 插件系统 API 聚合：src/app/plugin_system/api/__init__.py
- 插件基类入口：src/app/plugin_system/base/__init__.py
- 插件类型入口：src/app/plugin_system/types.py

## 维护原则

- 文档描述必须与当前实现一致，禁止保留“未来计划”式描述。
- 新增 app 模块对外能力时，同步更新 runtime 或 plugin_system 文档。
- 插件 API 出现兼容性变化时，必须补充迁移说明与影响面。
