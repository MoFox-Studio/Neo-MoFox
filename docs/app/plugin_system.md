# Plugin System 模块说明

本文档对应 src/app/plugin_system，说明插件开发在 app 层可直接使用的入口与边界。

## 入口层设计

plugin_system 由三个稳定入口组成：

- src/app/plugin_system/base: 插件基类、配置声明与注册工具。
- src/app/plugin_system/api: 面向插件作者的能力 API 聚合。
- src/app/plugin_system/types: 常用类型与模型语义导出。

src/app/plugin_system/__init__.py 聚合以上三个入口，方便插件作者统一导入。

## Base 层能力

src/app/plugin_system/base/__init__.py 暴露核心基类：

- BasePlugin、BaseAction、BaseTool、BaseAgent、BaseAdapter。
- BaseChatter、BaseCommand、BaseEventHandler、BaseService、BaseRouter。
- 配置辅助：Field、SectionBase、config_section。
- 命令路由：CommandNode、cmd_route。
- 插件注册：register_plugin。

## API 层能力分类

src/app/plugin_system/api/__init__.py 聚合 20 个 API 子模块：

- 交互与消息：message_api、send_api、stream_api、chat_api。
- 插件与配置：plugin_api、config_api、permission_api。
- 组件能力：action_api、agent_api、adapter_api、command_api。
- 业务支撑：service_api、router_api、event_api。
- 基础设施：database_api、storage_api、llm_api、log_api、media_api、prompt_api。

建议优先从聚合入口导入，减少插件与底层模块的耦合深度。

## Types 层能力

src/app/plugin_system/types.py 提供：

- 组件与事件：ComponentType、ComponentState、EventType、PermissionLevel。
- 会话与流：Message、MessageType、ChatStream、StreamContext。
- Prompt 相关：PromptTemplate、SystemReminderBucket、RenderPolicy。
- LLM 相关：LLMPayload、ROLE、Text、Image、Audio、ToolCall、ToolResult。
- 任务语义枚举：TaskType（utils、actor、vlm、voice 等）。

## 与 Core/Kernel 的边界

- app/plugin_system 是插件作者入口，不是能力最终实现层。
- 实际执行逻辑位于 core/components、core/managers 和 kernel 子模块。
- 新增 API 时应优先包装并稳定导出，而不是直接暴露底层内部实现细节。

## 示例与验证

示例目录位于 examples/src/app/plugin_system/api，当前覆盖：

- action、adapter、agent、chat、command、config。
- media、permission、plugin、prompt、router、service、stream。

新增 API 后，建议同步补充对应 example，保证插件作者可以快速验证调用路径。
