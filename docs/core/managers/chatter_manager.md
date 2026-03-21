# chatter_manager 模块

对应源码：src/core/managers/chatter_manager.py

## 概述

ChatterManager 管理 Chatter 组件与流绑定关系，提供按上下文选择 chatter 的能力。

## 核心职责

- 查询全量或插件级 Chatter。
- 管理 stream_id 到活跃 chatter 实例的映射。
- 为流自动选择并创建可用 chatter。

## 关键入口

- get_or_create_chatter_for_stream
- register_active_chatter / unregister_active_chatter
- get_chatter_manager

## 说明

- Chatter 是对话策略核心，通常与 stream_manager 联动。
