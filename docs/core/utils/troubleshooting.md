# Utils 排障手册

## schema_sync 导致启动失败

排查：

1. 检查错误是否为缺失主键列。
2. 检查是否为缺失非空且无默认值列 + 历史数据存在。
3. 若为 SQLite 类型修正告警，一般可继续启动。

## user_query_helper 查询不一致

排查：

1. 检查 platform/user_id 是否一致。
2. 检查 raw person_id 与 hash person_id 是否混用。
3. 检查 alru_cache 是否造成短时旧值读取。

## 用户信息未更新

排查：

1. update_person_info 是否传入 nickname/cardname。
2. person_id 对应记录是否存在（不存在会先创建）。
3. 检查数据库事务和 CRUD 更新返回。

## HTTP API 全部 401

排查：

1. core config 是否已初始化。
2. http_router.api_keys 是否为空。
3. 请求头是否包含 X-API-Key。

## HTTP API 403 无权限

排查：

1. 传入的 X-API-Key 是否在白名单。
2. 是否存在多环境配置文件不一致。
3. 检查是否热更新后未重载配置。
