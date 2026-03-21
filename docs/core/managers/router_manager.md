# router_manager 模块

对应源码：src/core/managers/router_manager.py

## 概述

RouterManager 负责 Router 组件在 HTTPServer 上的动态挂载和卸载。

## 核心职责

- 查询并实例化 Router 组件。
- 将 Router app 挂载到 HTTPServer 指定路径。
- 调用 Router startup/shutdown 生命周期。
- 管理已挂载 Router 实例映射。

## 关键入口

- mount_router / unmount_router
- mount_plugin_routers / unmount_plugin_routers
- get_router_manager / initialize_router_manager

## 依赖

- src/core/transport/router/get_http_server
