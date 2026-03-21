# sql_alchemy 模块

对应源码：src/core/models/sql_alchemy.py

## 概述

本模块定义 core 层持久化使用的 SQLAlchemy ORM 模型，采用 SQLAlchemy 2.0 的 Mapped 注解风格。

## 基础约定

- 统一基类：Base = declarative_base()
- 字符串字段辅助：get_string_field，当前统一返回 Text
- 时间字段混用 float 时间戳与 datetime，按表语义区分

## ORM 表清单

- ChatStreams: 聊天流记录
- LLMUsage: 模型调用与成本统计
- Messages: 消息持久化
- ActionRecords: 动作执行记录
- Images: 图片资源记录
- ImageDescriptions: 图片描述去重记录
- OnlineTime: 在线时长记录
- PersonInfo: 跨平台用户画像中心
- BanUser: 封禁用户记录
- PermissionNodes: 权限节点定义
- UserPermissions: 旧版用户权限（兼容保留）
- PermissionGroups: 用户权限组
- CommandPermissions: 命令级权限覆盖

## 索引与约束

当前模型已配置大量索引以支持高频查询场景，重点包括：

- stream_id、person_id、timestamp 等高频过滤字段
- 平台 + 用户、用户 + 命令等组合索引
- ImageDescriptions 的唯一约束：image_description_hash + type

## 兼容性与迁移

- UserPermissions 标注为兼容保留，推荐优先使用 PermissionGroups + CommandPermissions。
- 结构调整后需同步验证 schema 对齐逻辑与运行时查询语句。
- 涉及主键、唯一约束或字段类型变更时，需先评估历史数据迁移策略。
