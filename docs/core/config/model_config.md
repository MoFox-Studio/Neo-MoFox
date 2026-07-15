# model_config 模块

对应源码：src/core/config/model_config.py

## 概述

ModelConfig 管理 LLM 相关配置，包括 API 提供商、模型信息与任务到模型的映射关系，并提供可直接用于 kernel.llm 的 ModelSet 构建接口。

## 关键结构

- APIProviderSection
字段：name、base_url、api_key、client_type、max_retry、timeout、retry_interval。
特点：api_key 支持字符串或列表，并通过 get_api_key 轮询。

- ModelInfoSection
字段：model_identifier、name、api_provider、价格、上下文上限、tool_call_compat、extra_params。

- TaskConfigSection
字段：model_list、max_tokens、temperature、concurrency_count、embedding_dimension。

- ModelTasksSection
职责：内置任务集合（utils、actor、vlm、tool_use、embedding 等）和 get_task。

- ModelConfig
职责：组合 provider/models/tasks，并提供查询和转换方法。

## 核心方法

- get_provider(provider_name)
按名称获取提供商配置。

- get_model(model_name)
按模型名称获取模型信息。

- get_task(task_name)
将任务配置转换为 ModelSet 列表，返回给 LLMRequest 直接使用。

- get_model_set_by_name(model_name)
按单模型快速构建 ModelSet，可选覆盖 temperature/max_tokens。

## ModelSet 构建细节

在 get_task/get_model_set_by_name 中，extra_params 会合并：

1. core_config.advanced.force_sync_http/trust_env
2. model_info.extra_params

这样可保证全局请求策略自动下发到每个模型调用。

### extra_params HTTP 层特殊键

`model_info.extra_params` 支持三个 HTTP 层特殊键，会被原样透传到 ModelSet，
最终由 kernel.llm 的 provider 客户端提取并注入到 HTTP 请求的不同位置：

| 键 | 类型 | 用途 |
|---|---|---|
| `headers` | `dict[str, str]` | 注入 HTTP 请求头 |
| `query` | `dict[str, Any]` | 注入 URL 查询参数 |
| `body` | `dict[str, Any]` | 合并到请求体字段 |

配置示例：

```toml
[[models]]
model_identifier = "custom-model-v1"
name = "custom-model"
api_provider = "custom"

# 单行 inline table 形式（兼容 Python tomllib）
extra_params = {headers = {"X-API-Version" = "2024-06"}, query = {version = "2024-01-01"}, body = {metadata = {source = "maibot"}}, enable_thinking = false}
```

> ⚠️ Python 3.11 自带的 `tomllib` 不支持多行 inline table（TOML 1.1 特性）。
> 若需多行书写，请使用子表语法 `[models.extra_params.headers]` 等仅对 `[[models]]`
> 数组最后一项生效，或改用单行写法。详见 [LLM Types 文档](../../../docs/llm/types.md)。

## 全局实例管理

- _global_model_config: ModelConfig | None
- get_model_config: 未初始化时抛 RuntimeError
- init_model_config: 自动创建默认文件并 load(auto_update=True)

## 注意事项

- provider/model 名称区分大小写，未命中会抛 KeyError。
- 任务未配置会抛 ValueError。
- 多 API key 轮询是线程安全实现（ThreadLock）。
