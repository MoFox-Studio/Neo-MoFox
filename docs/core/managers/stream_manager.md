# stream_manager 模块

对应源码：src/core/managers/stream_manager.py

## 概述

StreamManager 是聊天流统一管理器，负责流实例单例、消息落库和上下文清理。

## 核心职责

- 获取或创建 ChatStream（同一 stream_id 全局唯一）。
- 从数据库重建流上下文并回填消息历史。
- 管理消息写入、序号与流活跃时间更新。
- 执行 TTL 清理与定期维护任务。

## 关键入口

- get_or_create_stream
- build_stream_from_database
- add_message_to_stream
- get_stream_manager

## 并发控制

- 使用每流锁 + 全局锁，避免并发创建同一 stream。
