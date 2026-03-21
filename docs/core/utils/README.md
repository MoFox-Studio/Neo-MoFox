# Utils 模块文档

本目录对应 src/core/utils，负责 core 层公共辅助逻辑与安全/同步工具。

## 模块职责

- 提供跨模块可复用工具函数
- 维护数据库结构对齐与安全辅助能力
- 承载与业务领域无强耦合的通用逻辑

## 文档导航

- [schema_sync](./schema_sync.md): 数据库结构一致性对齐机制。
- [user_query_helper](./user_query_helper.md): 用户中心查询与更新辅助。
- [security](./security.md): API Key 认证依赖与受保护路由用法。
- [troubleshooting](./troubleshooting.md): utils 相关常见故障排查。

## 当前代码结构

- schema_sync.py: 数据库结构对齐
- user_query_helper.py: 用户查询辅助
- security/: 安全相关工具
- __init__.py: 对外导出

## 模块边界

- utils 不承载核心业务编排，仅提供复用能力。
- schema_sync 具有结构变更副作用，应仅在可控生命周期触发。
- security 仅提供 FastAPI 依赖，不直接管理 token 生命周期。

## 依赖关系

- 调用方：src/core/managers、src/app/runtime
- 相关底层：src/kernel/db、src/kernel/logger

## 导出说明

当前 src/core/utils/__init__.py 仅包含模块级说明，不聚合导出子能力。
调用方通常直接从具体文件导入，如：

- src.core.utils.schema_sync
- src.core.utils.user_query_helper
- src.core.utils.security
