# OpenAIResponsesClient 与内置 web_search

`OpenAIResponsesClient` 是基于 openai 官方 **Responses API** 的客户端，使用
`client.responses.create` 替代传统的 Chat Completions。它原生支持**服务端执行的内置工具**
（如 `web_search`），这些工具无需本地实现，由模型服务端直接联网并返回结果。

本文档说明如何：

1. 让模型走 `OpenAIResponsesClient`；
2. 在配置中启用内置 `web_search` 工具；
3. 验证配置是否生效。

> 相关代码：
> - `src/kernel/llm/model_client/responses_client.py`（客户端实现）
> - `src/kernel/llm/model_client/registry.py`（客户端路由）

---

## 1. 前提：模型必须走 Responses 客户端

内置 `web_search` 是 **Responses API 独有**的工具类型，普通 Chat Completions 请求没有它。
因此使用的模型必须满足：

- provider 支持 Responses 端点（如 DeepSeek 官方 API 的 `base_url = "https://api.deepseek.com/v1"`）；
- 该模型的 `client_type` 被设为 `openai_response`。

`ModelClientRegistry.get_client_for_model` 按 `client_type` 路由：
`openai_response` / `responses` / `openai.responses` 会命中 `OpenAIResponsesClient`
（`registry.py:48`）。其他值（`openai`、`anthropic` 等）走各自旧客户端。

### 配置方式

在 `config/model.toml` 中找到目标 **API 提供商**，把 `client_type` 改为 `openai_response`：

```toml
[[api_providers]]
name = "DeepSeek"
base_url = "https://api.deepseek.com/v1"

# 关键：使用 Responses 客户端
client_type = "openai_response"
```

配置加载后，`model_set` 中的 `client_type` 来自 provider（`src/core/config/model_config.py:519`），
因此**所有挂在 `DeepSeek` 下的模型都会走 Responses 客户端**。若只想让个别模型走，请单独建一个
provider。

> 注意：若 provider 服务端不支持 Responses 端点，请求会直接失败。先用简单的非流式请求验证连通性。

---

## 2. 声明内置 web_search 工具

在模型配置的 `extra_params.body.tools` 中声明服务端内置工具。客户端会提取该声明
（`_extract_custom_tools`，`responses_client.py:363`），与函数工具合并去重后一起发送
（`_merge_tools`，`responses_client.py:601` ~ `_create_responses_stream`）。

### 配置方式

在 `config/model.toml` 的目标模型上修改 `extra_params`：

```toml
[[models]]
model_identifier = "deepseek-v4-flash"
name = "deepseek-ai/DeepSeek-V4-Flash"
api_provider = "DeepSeek"
# ... 其他价格/上下文配置省略 ...

extra_params = { body = { tools = [{ type = "web_search" }] } }
```

每个元素是一个工具声明对象，字段如下：

| 字段 | 必填 | 适用类型 | 约束 |
|------|:----:|----------|------|
| `type` | 是 | 全部 | 可选值 `function` / `web_search` / `web_search_2025_08_26` |
| `name` | 否 | `function` | 非空、≤128 字符、匹配 `^[a-zA-Z0-9_-]+$`，且所有工具名唯一 |
| `description` | 否 | `function` | 函数用途说明，供模型决定何时调用 |
| `parameters` | 否 | `function` | 函数入参的 JSON Schema（object） |

内置 `web_search` 只需要 `type` 一个字段，无需 `name` / `description` / `parameters`
（服务端自行联网）。除 `function` / `web_search` / `web_search_2025_08_26` 之外的其他
内置工具类型会被 API 静默忽略。

### 支持的声明形式

`extra_params.body.tools` 支持混合内置工具与普通函数工具：

```toml
extra_params = { body = { tools = [
    { type = "web_search" },
    { type = "function",
      name = "get_weather",
      description = "查询某地天气",
      parameters = { type = "object", properties = { location = { type = "string" } } } },
] } }
```

> 内置工具（`web_search` 等）本身不会出现在你本地的工具注册表里，模型也不会“调用”它们
> 让你手动执行——它们由服务端执行，结果作为 `web_search_call` output item 直接流入响应。

---

## 3. 请求必须有 input 或 instructions

`create` 会校验请求体：缺少 `input` 且缺少 `instructions` 时抛出
`LLMConfigurationError`（`responses_client.py:651`）。正常聊天流程自带 payloads，
一般不会触发；若你的调用方式只传了工具没有消息，就会报错。

---

## 4. 验证是否生效

倾向先做最小连通性验证（非流式）：

```python
import asyncio
from src.kernel.llm import LLMRequest, LLMPayload, Text, ROLE
from src.core.config.model_config import get_model_config

async def main():
    model_set = get_model_config().get_model_set_by_name("deepseek-v4-flash")
    # model_set 里每个 model 的 client_type 应为 "openai_response"

    request = LLMRequest(model_set=model_set, request_name="web_search_check")
    request.add_payload(LLMPayload(ROLE.USER, Text("搜索一下今天深圳的天气")))
    response = await request.send(stream=False)
    print(await response)

asyncio.run(main())
```

### 排查清单

| 现象 | 可能原因 |
|------|----------|
| 请求报错、完全没触发搜索 | provider 不支持 Responses 端点，或 `client_type` 仍是 `openai` |
| 发起了请求但模型不联网 | `extra_params.body.tools` 未生效 —— 检查配置里 `body` 的拼写与嵌套层级 |
| 模型调用了一个本地不存在的工具 | 把 `web_search` 写成了 `function` 工具声明，应改用 `{ type = "web_search" }` |

流式路径下，服务端内置工具事件（`web_search_call` 等）会被客户端静默跳过
（`responses_client.py:838`），只消费最终文本与用量，不影响注册表调用。

---

## 相关文档

- [Model Client 模块概览](./README.md)
- [Request 模块](../request.md)
- [LLM 模块总览](../README.md)