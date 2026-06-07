# Embedding 模块

## 概述

embedding_request.py 和 embedding_response.py 提供了向 LLM 提供商发送向量嵌入（Embedding）请求的能力。与 LLMRequest 类似，它支持负载均衡、重试策略和指标收集。

## EmbeddingRequest

`python
@dataclass(slots=True)
class EmbeddingRequest:
    model_set: ModelSet
    request_name: str = ""
    inputs: list[str] = field(default_factory=list)
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.EMBEDDINGS
`

### 核心属性

| 属性 | 类型 | 说明 |
|---|---|---|
| model_set | ModelSet | 模型配置列表 |
| request_name | str | 请求名称（用于日志和策略） |
| inputs | list[str] | 待嵌入的输入文本列表 |
| policy | Policy \| None | 负载均衡/重试策略，默认使用 create_default_policy() |
| clients | ModelClientRegistry \| None | 模型客户端注册表 |
| enable_metrics | ool | 是否启用指标收集 |
| request_type | RequestType | 固定为 RequestType.EMBEDDINGS |

### 核心方法

#### add_input

`python
def add_input(self, value: str) -> Self:
`

追加 embedding 输入文本。支持链式调用。

#### send

`python
async def send(self) -> EmbeddingResponse:
`

发送 embedding 请求，返回 EmbeddingResponse。

**执行流程：**
1. 校验 inputs 非空
2. 通过策略获取模型步骤
3. 调用客户端的 create_embedding 方法
4. 支持超时控制（model["timeout"]）
5. 记录指标
6. 失败时通过策略进行重试

---

## EmbeddingResponse

`python
@dataclass(slots=True)
class EmbeddingResponse:
    embeddings: list[list[float]]
    model_name: str
    request_name: str = ""
`

### 属性

| 属性 | 类型 | 说明 |
|---|---|---|
| embeddings | list[list[float]] | 嵌入向量列表，每个元素对应一个输入文本的向量 |
| model_name | str | 实际使用的模型名称 |
| request_name | str | 请求名称 |

---

## 使用示例

### 基础用法

`python
from src.kernel.llm import EmbeddingRequest

model_set = [
    {
        "client_type": "openai",
        "model_identifier": "text-embedding-3-small",
        "api_key": "sk-...",
        "base_url": "https://api.openai.com/v1",
        "max_retry": 2,
    }
]

request = EmbeddingRequest(model_set=model_set)
request.add_input("Hello world")
request.add_input("What is AI?")

response = await request.send()
print(f"模型: {response.model_name}")
print(f"向量维度: {len(response.embeddings[0])}")
for i, emb in enumerate(response.embeddings):
    print(f"Input {i}: {emb[:5]}...")
`

### 批量文本嵌入

`python
request = EmbeddingRequest(model_set=model_set, request_name="batch_embed")

texts = ["文档1", "文档2", "文档3", "文档4", "文档5"]
for text in texts:
    request.add_input(text)

response = await request.send()
print(f"成功嵌入 {len(response.embeddings)} 条文本")
`

### 多模型故障转移

`python
model_set = [
    {
        "client_type": "openai",
        "model_identifier": "text-embedding-3-large",
        "api_key": "key1",
        "max_retry": 2,
    },
    {
        "client_type": "openai",
        "model_identifier": "text-embedding-3-small",
        "api_key": "key2",
        "max_retry": 3,
    },
]

request = EmbeddingRequest(model_set=model_set, request_name="fallback")
request.add_input("Important text to embed")

try:
    response = await request.send()
except Exception as e:
    print(f"所有模型都失败了: {e}")
`

### 禁用指标收集

`python
request = EmbeddingRequest(
    model_set=model_set,
    enable_metrics=False,  # 提升性能
)
request.add_input("text")
response = await request.send()
`

---

## 错误处理

与 LLMRequest 一致的错误分类和重试策略：

`python
from src.kernel.llm import (
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
)

try:
    response = await request.send()
except LLMConfigurationError as e:
    print(f"配置错误: {e}")
except LLMRateLimitError as e:
    print(f"速率限制，等待 {e.retry_after}s")
except LLMTimeoutError as e:
    print(f"超时: {e.timeout}s")
`

---

## 模型客户端扩展

Embedding 请求通过 ModelClientRegistry.get_embedding_client_for_model() 获取客户端。客户端需实现 create_embedding 方法：

`python
class MyEmbeddingClient:
    async def create_embedding(
        self,
        *,
        model_name: str,
        inputs: list[str],
        request_name: str,
        model_set: dict[str, Any],
    ) -> list[list[float]]:
        # 实现自定义 embedding 逻辑
        pass
`

---

## 相关文档

- [LLM 主文档](../README.md)
- [Request 模块](./request.md)
- [Rerank 模块](./rerank.md)
- [Types 模块](./types.md)
- [Policy 模块](./policy/README.md)
