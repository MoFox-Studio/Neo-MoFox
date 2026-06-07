# Types 模块

## 概述

	ypes.py 定义了 LLM 模块使用的核心类型别名和 TypedDict 定义，为整个模块提供统一的类型约束。

## RequestType（请求类型枚举）

`python
class RequestType(str, Enum):
    COMPLETIONS = "completions"
    EMBEDDINGS = "embeddings"
    RERANK = "rerank"
`

标识 LLM 请求的类型。用于区分聊天补全、向量嵌入和文档重排序三种不同的能力。

| 值 | 含义 |
|---|---|
| COMPLETIONS | 聊天补全请求（Chat Completions） |
| EMBEDDINGS | 向量嵌入请求（Embeddings） |
| RERANK | 文档重排序请求（Rerank） |

---

## ModelEntry（模型配置条目）

`python
class ModelEntry(TypedDict, total=True):
    api_provider: str
    base_url: str
    model_identifier: str
    api_key: str
    client_type: str
    max_retry: int
    timeout: float
    retry_interval: float
    price_in: float
    price_out: float
    temperature: float
    max_tokens: int
    max_context: int
    tool_call_compat: bool
    extra_params: dict[str, Any]
`

定义单个 LLM 模型的完整配置信息。所有字段在类型层面标记为必须（	otal=True），但实际使用中上层可按需传递。

**常用字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| client_type | str | 提供商类型，如 "openai"、"anthropic" |
| model_identifier | str | 模型标识，如 "gpt-4" |
| api_key | str | API 密钥 |
| base_url | str | API 基础 URL |
| max_retry | int | 最大重试次数 |
| 	imeout | loat | 请求超时（秒） |
| retry_interval | loat | 重试间隔（秒） |
| 	emperature | loat | 采样温度 |
| max_tokens | int | 最大输出 token 数 |
| max_context | int | 模型上下文窗口大小 |
| price_in / price_out | loat | 输入/输出价格（用于成本计算） |
| tool_call_compat | tool | 是否启用工具调用兼容模式 |
| extra_params | dict | 额外参数透传 |

---

## ModelSet（模型集合类型）

`python
ModelSet: TypeAlias = list[ModelEntry]
`

一组可用的模型配置，用于负载均衡和故障转移。LLMRequest、EmbeddingRequest、RerankRequest 均使用此类型。

**使用示例：**

`python
from src.kernel.llm import ModelSet

model_set: ModelSet = [
    {
        "client_type": "openai",
        "model_identifier": "gpt-4",
        "api_key": "sk-...",
        "base_url": "https://api.openai.com/v1",
        "max_retry": 3,
    },
    {
        "client_type": "openai",
        "model_identifier": "gpt-3.5-turbo",
        "api_key": "sk-...",
        "max_retry": 2,
    }
]
`

---

## 相关文档

- [LLM 主文档](../README.md)
- [Request 模块](./request.md)
- [Embedding 模块](./embedding.md)
- [Rerank 模块](./rerank.md)
- [Policy 模块](./policy/README.md)
