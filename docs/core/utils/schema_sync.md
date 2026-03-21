# schema_sync 模块

对应源码：src/core/utils/schema_sync.py

## 概述

schema_sync 在启动阶段对数据库结构执行强一致对齐，使数据库表结构与 ORM 定义保持一致。

## 入口函数

- enforce_database_schema_consistency(metadata=None)
返回 SchemaSyncStats，记录表检查和列变更统计。

## 执行步骤

1. 使用 metadata.create_all 补齐缺失表。
2. 遍历每张模型表，读取数据库列信息。
3. 删除模型中不存在的数据库列。
4. 添加数据库中缺失的模型列。
5. 校验并修正列类型。
6. 校验并修正列可空性。

## 统计结构

SchemaSyncStats 包含：

- tables_checked
- columns_added
- columns_removed
- columns_type_altered
- columns_nullability_altered

## 数据库差异处理

- PostgreSQL：支持 ALTER TYPE、ALTER NULLABILITY。
- SQLite：对 ALTER TYPE/NULLABILITY 给出告警并跳过，避免阻断启动。

## 安全约束

- 缺失主键列时直接抛 DatabaseInitializationError。
- 缺失非空且无默认值列，且表已有数据时抛错，避免不安全自动修复。

## 建议使用时机

- 仅在应用启动初始化阶段调用。
- 避免在高并发业务请求中临时触发。
