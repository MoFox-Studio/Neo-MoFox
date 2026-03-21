# message 模块

对应源码：src/core/models/message.py

## 概述

本模块定义运行时消息对象 Message 和消息类型枚举 MessageType。

## MessageType

当前内置类型：

- text
- image
- voice
- video
- file
- location
- emoji
- notice
- unknown

## Message 结构

Message 由四类字段组成：

- 基础字段：message_id、time、reply_to
- 内容字段：content、processed_plain_text、message_type
- 用户字段：sender_id、sender_name、sender_cardname、sender_role
- 上下文字段：platform、chat_type、stream_id、raw_data、extra

## 关键行为

- 初始化时若 time 为 datetime，会自动转换为 Unix 时间戳。
- to_dict 会输出 message_type.value，便于跨模块传递。
- from_dict 会对 message_type 做容错解析，不合法时回退到 text。
- from_dict 会把未识别字段收集到 extra。

## 兼容性注意

- Message 是运行时模型，不等同于 ORM 表 Messages。
- raw_data 不进入 to_dict 输出，避免传递平台原始大对象。
- 修改字段名称时需同步检查 transport 与 plugin API 的读取逻辑。
