# service_manager 模块

对应源码：src/core/managers/service_manager.py

## 概述

ServiceManager 负责 Service 组件查询、实例创建和方法调用，服务于跨插件能力复用。

## 核心职责

- 获取全量或插件级 Service 类。
- 按签名创建新的 Service 实例（非单例）。
- 解析签名并完成同步/异步方法调用。

## 关键入口

- get_service
- call_service / call_service_async
- get_service_manager

## 说明

- 每次 get_service 都是新实例，调用方不应假设状态复用。
