# state_manager 模块

对应源码：src/core/components/state_manager.py

## 概述

StateManager 负责组件生命周期状态与运行时数据管理，并提供异步安全操作。

## 核心职责

- 维护组件状态映射。
- 维护组件运行时键值数据。
- 提供同步与异步两套状态接口。
- 支持按依赖关系执行级联禁用与依赖检查启用。

## 关键状态

- unloaded
- loaded
- active
- inactive
- error

## 关键能力

- set_state / get_state
- set_state_async / get_state_async
- set_runtime_data / get_runtime_data
- disable_component_cascade
- enable_component_with_dependencies

## 协作模块

- src/core/components/registry
- src/core/components/types
