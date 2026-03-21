# plugin_manager 模块

对应源码：src/core/managers/plugin_manager.py

## 概述

PluginManager 负责单个插件的加载与卸载执行，不负责插件发现和依赖排序。

## 核心职责

- 按 manifest 加载插件模块（目录、zip、mfp）。
- 获取 register_plugin 注册的插件类并实例化。
- 加载插件配置并注入插件实例。
- 注册组件到全局注册表并更新组件状态。
- 卸载时调用生命周期钩子并清理注册信息。

## 边界说明

- 宏观层的发现、manifest 校验、依赖解析由 components/loader 完成。
- 本模块聚焦单插件执行路径与运行态状态维护。

## 关键入口

- load_plugin_from_manifest: 主加载入口。
- unload_plugin: 卸载入口。
- get_plugin_manager: 全局单例获取。

## 协作模块

- src/core/components/loader
- src/core/managers/config_manager
- src/core/managers/event_manager
- src/core/components/state_manager
