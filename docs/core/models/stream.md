# stream 模块

对应源码：src/core/models/stream.py

## 概述

本模块定义聊天流上下文 StreamContext 与聊天流对象 ChatStream。

## StreamContext

主要职责：

- 管理 unread_messages 与 history_messages。
- 维护当前处理中消息状态（current_message、processing_message_id）。
- 维护消息缓存队列与缓冲计数。
- 持有流循环任务引用 stream_loop_task。

关键方法：

- add_unread_message: 追加未读消息。
- add_history_message: 追加历史消息并按 max_context_size 截断。
- check_types: 基于 current_message.extra.format_info.accept_format 判断类型兼容性。
- flush_unreads_to_history: 批量转移未读消息到历史消息。

## ChatStream

主要职责：

- 封装 stream 元信息（platform、chat_type、bot_id 等）。
- 维护创建时间与最后活跃时间。
- 内置一个 StreamContext 作为运行态上下文容器。

关键方法：

- update_active_time: 更新最后活跃时间。
- set_context: 绑定当前消息并刷新活跃时间。
- generate_stream_id: 根据 platform 与 user_id/group_id 生成稳定 stream_id。

## stream_id 生成策略

- 私聊键：platform_userid_private
- 群聊键：platform_groupid
- 哈希算法：SHA-256
- 内部使用 LRU 缓存提升重复计算性能

## 维护注意

- 调整 max_context_size 需同步评估上下文截断影响。
- check_types 依赖 extra.format_info 约定，改动需同步平台适配器。
- 若引入持久化字段，需明确与 ORM 模型的职责边界。
