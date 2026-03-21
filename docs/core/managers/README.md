# Managers 模块文档

本目录对应 src/core/managers，负责 core 层运行时编排与组件治理，是 plugin、transport 与 app/runtime 之间的关键协作层。

## 模块职责

- 按组件类型管理注册、筛选、实例化与调用。
- 管理插件生命周期中的挂载、事件订阅、路由暴露与状态更新。
- 管理会话流、权限、媒体、工具调用等横切能力。

## 文档导航

- [plugin_manager](./plugin_manager.md): 单插件导入、组件注册、卸载与生命周期钩子。
- [action_manager](./action_manager.md): Action 组件筛选、schema 和执行入口。
- [adapter_manager](./adapter_manager.md): Adapter 启停、健康检查、命令下发。
- [chatter_manager](./chatter_manager.md): Chatter 组件选择与活跃实例管理。
- [command_manager](./command_manager.md): 命令前缀识别、匹配与执行。
- [config_manager](./config_manager.md): 插件配置加载、重载与缓存。
- [event_manager](./event_manager.md): EventHandler 注册、订阅映射与事件发布。
- [media_manager](./media_manager.md): 媒体识别、缓存和清理任务。
- [permission_manager](./permission_manager.md): 权限组与命令覆盖权限检查。
- [router_manager](./router_manager.md): Router 挂载到 HTTPServer 与卸载管理。
- [service_manager](./service_manager.md): Service 查询、实例化和方法调用。
- [stream_manager](./stream_manager.md): ChatStream 单例管理、消息持久化与 TTL 清理。
- [tool_manager](./tool_manager.md): ToolUse、MCPManager 与工具历史缓存。

## 导出入口

src/core/managers/__init__.py 统一导出：

- get_xxx_manager 单例入口。
- initialize_xxx_manager 初始化入口。
- manager 类本体。
- 与 transport/distribution 的初始化桥接函数。

## 与其他层关系

- 输入来源：components、models、config。
- 协作对象：transport、kernel 的 db/event/logger/scheduler。
- 上层调用方：app/runtime/bot 在初始化和关闭阶段集中调用。

## 维护建议

- 新增 manager 时，保持 get_xxx_manager 单例模式一致。
- 涉及生命周期事件的改动，优先验证加载与关闭阶段顺序。
- 管理器间调用应避免循环依赖，必要时使用延迟导入。
