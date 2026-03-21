# adapter_manager 模块

对应源码：src/core/managers/adapter_manager.py

## 概述

AdapterManager 负责适配器实例的启动、停止和运行态管理，衔接平台输入与 core 处理链。

## 核心职责

- 基于签名启动 Adapter 并管理 active 实例。
- 与 SinkManager 协作注入 CoreSink。
- 更新组件状态并处理适配器命令响应。
- 提供批量健康检查与重启能力。

## 关键入口

- start_adapter / stop_adapter
- get_adapter_manager
- initialize_adapter_manager

## 约束

- run_in_subprocess 已移除，声明该模式会被拒绝启动。
