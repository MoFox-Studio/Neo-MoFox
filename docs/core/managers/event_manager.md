# event_manager 模块

对应源码：src/core/managers/event_manager.py

## 概述

EventManager 是 kernel/event 的上层封装，负责 EventHandler 注册、订阅构建和事件发布。

## 核心职责

- 注册和注销插件事件处理器。
- 构建订阅映射并按权重执行处理器。
- 支持 EventType 系统事件与字符串自定义事件。
- 支持消息拦截与事件决策协作。

## 关键入口

- register_plugin_handlers / unregister_plugin_handlers
- build_subscription_map
- publish_event
- get_event_manager / initialize_event_manager

## 并发模型

- 使用异步锁保护处理器映射，确保订阅变更一致性。
