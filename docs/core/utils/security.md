# security 子模块

对应源码：src/core/utils/security/__init__.py

## 概述

security 子模块提供 FastAPI API Key 验证依赖，用于保护 HTTP 路由端点。

## 核心对象

- get_api_key
作用：从请求头 X-API-Key 读取并校验密钥。

- VerifiedDep
作用：Depends(get_api_key) 的可复用依赖对象。

## 校验逻辑

1. 读取 core_config.http_router.api_keys。
2. 配置未初始化：返回 HTTP 401。
3. api_keys 为空：返回 HTTP 401 并告警。
4. 请求密钥不在白名单：返回 HTTP 403。
5. 命中白名单：返回密钥字符串。

## 典型用法

- 路由级依赖
@router.get(..., dependencies=[VerifiedDep])

- 参数注入
async def endpoint(_: str = VerifiedDep)

## 关联配置

- config/core.toml -> [http_router].api_keys

## 安全建议

- 生产环境必须配置非空 api_keys。
- 日志中不要完整打印密钥，仅打印前缀。
