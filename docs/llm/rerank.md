# Rerank 模块

## 概述

rerank_request.py 和 rerank_response.py 提供了文档重排序（Rerank）能力。给定一个查询（query）和一组待排序文档，返回按相关性排序的结果。用于搜索、推荐等场景的精细排序。

## RerankRequest

`python
@dataclass(slots=True)
class RerankRequest:
    model_set: ModelSet
    request_name: str = ""
    query: str = ""
    documents: list[Any] = field(default_factory=list)
    top_n: int | None = None
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.RERANK
`

### 核心属性

| 属性 | 类型 | 说明 |
|---|---|---|
| model_set | ModelSet | 模型配置列表 |
| request_name | str | 请求名称（用于日志和策略） |
| query | str | 查询文本 |
| documents | list[Any] | 待排序的文档列表 |
| top_n | int \| None | 返回前 N 个结果，None 表示返回全部 |
| policy | Policy \| None | 负载均衡/重试策略，默认使用 create_default_policy() |
| clients | ModelClientRegistry \| None | 模型客户端注册表 |

### 核心方法

#### set_query

`python
def set_query(self, value: str) -> Self:
`

设置 rerank 查询文本。支持链式调用。

#### add_document

`python
def add_document(self, value: Any) -> Self:
`

追加待排序文档。支持链式调用。

#### send

`python
async def send(self) -> RerankResponse:
`

发送 rerank 请求，返回 RerankResponse。

**执行流程：**
1. 校验 query 和 documents 非空
2. 通过策略获取模型步骤
3. 调用客户端的 create_rerank 方法
4. 支持超时控制
5. 记录指标
6. 失败时通过策略进行重试
7. 将客户端返回的结果转换为 RerankItem 列表

---

## RerankItem

`python
@dataclass(slots=True)
class RerankItem:
    index: int
    score: float
    document: Any
`

### 属性

| 属性 | 类型 | 说明 |
|---|---|---|
| index | int | 文档在原始列表中的索引 |
| score | loat | 相关性分数（越高越相关） |
| document | Any | 原始文档内容 |

---

## RerankResponse

`python
@dataclass(slots=True)
class RerankResponse:
    results: list[RerankItem]
    model_name: str
    request_name: str = ""
`

### 属性

| 属性 | 类型 | 说明 |
|---|---|---|
| results | list[RerankItem] | 按相关性降序排列的结果列表 |
| model_name | str | 实际使用的模型名称 |
| request_name | str | 请求名称 |

---

## 使用示例

### 基础用法

`python
from src.kernel.llm import RerankRequest

model_set = [
    {
        "client_type": "openai",
        "model_identifier": "rerank-model",
        "api_key": "sk-...",
        "max_retry": 2,
    }
]

request = RerankRequest(model_set=model_set)
request.set_query("Python 编程入门")
request.add_document({"title": "Python 教程", "content": "..."})
request.add_document({"title": "Java 指南", "content": "..."})
request.add_document({"title": "Python 高级", "content": "..."})

response = await request.send()
for item in response.results:
    print(f"Index {item.index}: score={item.score:.4f}, doc={item.document['title']}")
`

### 限制返回数量

`python
request = RerankRequest(model_set=model_set, top_n=3)

request.set_query("机器学习")
for doc in documents:
    request.add_document(doc)

response = await request.send()
print(f"返回前 {len(response.results)} 个结果")
`

### 链式调用

`python
response = await (
    RerankRequest(model_set=model_set, request_name="rerank_demo")
    .set_query("深度学习框架对比")
    .add_document({"title": "TensorFlow", "content": "..."})
    .add_document({"title": "PyTorch", "content": "..."})
    .add_document({"title": "JAX", "content": "..."})
    .send()
)

for item in response.results:
    print(f"{item.document['title']}: {item.score:.4f}")
`

### 多模型故障转移

`python
model_set = [
    {"client_type": "openai", "model_identifier": "rerank-v3", "api_key": "key1", "max_retry": 2},
    {"client_type": "openai", "model_identifier": "rerank-v2", "api_key": "key2", "max_retry": 3},
]

request = RerankRequest(model_set=model_set)
request.set_query("查询内容")
request.add_document(doc1)
request.add_document(doc2)

try:
    response = await request.send()
except Exception as e:
    print(f"所有模型失败: {e}")
`

---

## 错误处理

`python
from src.kernel.llm import LLMConfigurationError

try:
    response = await request.send()
except LLMConfigurationError as e:
    print(f"配置错误: {e}")
except Exception as e:
    print(f"请求失败: {e}")
`

---

## 模型客户端扩展

Rerank 请求通过 ModelClientRegistry.get_rerank_client_for_model() 获取客户端。客户端需实现 create_rerank 方法：

`python
class MyRerankClient:
    async def create_rerank(
        self,
        *,
        model_name: str,
        query: str,
        documents: list[Any],
        top_n: int | None,
        request_name: str,
        model_set: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # 返回格式：[{"index": 0, "score": 0.95, "document": ...}, ...]
        pass
`

---

## 相关文档

- [LLM 主文档](../README.md)
- [Embedding 模块](./embedding.md)
- [Request 模块](./request.md)
- [Types 模块](./types.md)
- [Policy 模块](./policy/README.md)
