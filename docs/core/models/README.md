# Models 模块文档

本目录对应 src/core/models，负责 core 层统一的数据结构定义，覆盖运行时消息模型、会话流模型和数据库 ORM 模型。

## 模块职责

- 定义运行时消息模型 Message 与 MessageType。
- 定义聊天流模型 ChatStream 与流上下文 StreamContext。
- 定义 SQLAlchemy ORM 表结构，支撑持久化查询。
- 通过 __init__.py 统一导出供 managers、transport、plugin_system 使用。

## 文档导航

- [message](./message.md): Message 与 MessageType 的字段与序列化行为。
- [stream](./stream.md): StreamContext 与 ChatStream 的上下文管理和 stream_id 生成逻辑。
- [sql_alchemy](./sql_alchemy.md): ORM 表清单、关键索引与兼容性说明。
- [protocols](./protocols.md): 协议模块现状与扩展约定。

## 模块导出

当前导出集中在 src/core/models/__init__.py，分为三类：

- 运行时模型：Message、MessageType、ChatStream、StreamContext。
- ORM 基类：Base。
- ORM 实体：ActionRecords、BanUser、ChatStreams、CommandPermissions、ImageDescriptions、Images、LLMUsage、Messages、OnlineTime、PermissionGroups、PermissionNodes、PersonInfo、UserPermissions。

## 依赖关系

- 上游依赖：src/kernel/db 与 SQLAlchemy。
- 主要使用方：src/core/managers、src/core/transport、src/app/plugin_system/types。

## 维护建议

- 运行时模型字段变更时，同步评估序列化兼容性。
- ORM 结构变更时，同步检查 schema_sync 逻辑与迁移脚本。
- 对外导出变更时，同步更新 app 层 plugin_system 类型映射。
