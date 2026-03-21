# Components 模块文档

本目录对应 src/core/components，负责定义插件组件体系、组件元数据与装载基础能力。

## 模块职责

- 定义各类组件基类与公共抽象
- 维护组件签名、类型与状态相关定义
- 提供插件装载与组件注册基础能力

## 文档导航

- [base](./base.md): 组件基类体系与职责边界
- [loader](./loader.md): 插件注册装饰器、manifest 解析与加载计划
- [registry](./registry.md): 组件注册表与依赖关系管理
- [state_manager](./state_manager.md): 组件状态与运行时数据管理
- [types](./types.md): 枚举、签名解析与权限级别定义
- [utils](./utils.md): schema 工具、依赖安装器和调用辅助

## 当前代码结构

- base/: 组件基类定义
- utils/: 组件辅助工具
- loader.py: 插件装载入口能力
- registry.py: 组件注册相关能力
- state_manager.py: 组件状态管理
- types.py: 组件类型与元信息定义
- __init__.py: 对外导出

## 对外导出

src/core/components/__init__.py 当前聚合导出：

- Base 组件类与命令结果类型
- PluginLoader 与插件注册入口
- ComponentRegistry 与 StateManager 全局单例入口
- ChatType、ComponentType、ComponentState、EventType、PermissionLevel

## 依赖关系

- 下游使用方：src/core/managers
- 运行时接入方：src/app/runtime
- 基础能力依赖：src/kernel

## 维护建议

- 新增组件类型时，先扩展 types.py，再同步 registry 和 manager 层。
- 涉及签名规则变更时，需要同步更新 plugin_system 与示例代码。
- loader 与 plugin_manager 的职责边界保持稳定，避免重复实现。
