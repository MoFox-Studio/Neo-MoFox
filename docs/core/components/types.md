# types 模块

对应源码：src/core/components/types.py

## 概述

types 模块定义组件系统核心枚举与签名处理函数，是跨模块通用类型基石。

## 核心枚举

- ChatType: private、group、discuss、all
- ComponentType: action、tool、adapter、chatter、command、config、event_handler、service、router、plugin、agent
- EventType: on_start、on_stop 等系统事件
- ComponentState: unloaded、loaded、active、inactive、error
- PermissionLevel: guest、user、operator、owner

## 关键函数

- parse_signature
作用：解析 plugin_name:component_type:component_name

- build_signature
作用：从结构化字段构造签名字符串

## 注意事项

- PermissionLevel 支持比较运算。
- parse_signature 是 registry 和 manager 层的统一入口。
- 新增组件类型时，需同步更新 ComponentType 与消费方逻辑。
