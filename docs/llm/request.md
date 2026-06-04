# Request 模块

## 概述

`request.py` 定义了 `LLMRequest` 类，是与 LLM 交互的核心门面类。它负责：
- 构建消息 payload 列表
- 通过 `LLMContextManager` 管理上下文结构
- 管理模型客户端和选择
- 应用负载均衡和重试策略
- 收集请求指标
- 执行实际的 LLM API 调用

另外，`embedding_request.py` 和 `rerank_request.py` 分别提供了 `EmbeddingRequest` 和 `RerankRequest` 两种专用请求类型。

## LLMRequest 类定义

```python
from dataclasses import dataclass, field
from typing import Any, Self

@dataclass(slots=True)
class LLMRequest:
    """一次逻辑 LLM 交互的可变请求构建器与发送器。"""

    model_set: ModelSet                                  # 模型配置列表
    request_name: str = ""                              # 请求名称（用于日志和策略）
    meta_data: dict[str, Any] = field(default_factory=dict)  # 附加元数据

    payloads: list[LLMPayload] = field(default_factory=list)  # 消息 payload 列表
    policy: Policy | None = None                        # 负载均衡/重试策略
    clients: ModelClientRegistry | None = None          # 模型客户端注册表
    context_manager: LLMContextManager | None = None    # 上下文管理器
    enable_metrics: bool = True                         # 是否启用指标收集
    request_type: RequestType = RequestType.COMPLETIONS # 请求类型
```

### ModelEntry 完整字段

模型配置使用 `ModelEntry` TypedDict，所有字段均为**必需**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `api_provider` | `str` | API 提供商名称（如 `"openai"`） |
| `base_url` | `str` | API 基础 URL |
| `model_identifier` | `str` | 模型标识符（如 `"gpt-4"`） |
| `api_key` | `str` | API 密钥 |
| `client_type` | `str` | 客户端类型（如 `"openai"`） |
| `max_retry` | `int` | 最大重试次数 |
| `timeout` | `float` | 请求超时（秒） |
| `retry_interval` | `float` | 重试间隔（秒） |
| `price_in` | `float` | 输入价格（$/1K tokens） |
| `cache_hit_price_in` | `float` | 缓存命中输入价格 |
| `price_out` | `float` | 输出价格（$/1K tokens） |
| `temperature` | `float` | 采样温度 |
| `max_tokens` | `int` | 最大输出 token 数 |
| `max_context` | `int` | 最大上下文窗口（0 表示不限制） |
| `tool_call_compat` | `bool` | 是否启用工具调用兼容模式 |
| `extra_params` | `dict[str, Any]` | 额外参数（可含 `context_reserve_ratio`、`context_reserve_tokens`） |

**使用示例：**

```python
model_set = [
    {
        "api_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": "gpt-4",
        "api_key": "sk-...",
        "client_type": "openai",
        "max_retry": 3,
        "timeout": 30.0,
        "retry_interval": 1.0,
        "price_in": 0.03,
        "cache_hit_price_in": 0.015,
        "price_out": 0.06,
        "temperature": 0.7,
        "max_tokens": 4096,
        "max_context": 8192,
        "tool_call_compat": False,
        "extra_params": {},
    }
]
```

### request_type

**类型：** `RequestType`

**可选值：**
- `RequestType.COMPLETIONS` — 对话补全（默认）
- `RequestType.EMBEDDINGS` — 向量嵌入
- `RequestType.RERANK` — 重排序

### meta_data

**类型：** `dict[str, Any]`

**描述：** 附加到请求的任意元数据，会随观测记录一起持久化。

## 核心方法

### add_payload

添加消息 payload。现在通过 `LLMContextManager` 处理追加逻辑：

```python
def add_payload(self, payload: LLMPayload, position=None) -> Self:
    """
    Args:
        payload: 要添加的 payload
        position: 插入位置（默认追加到末尾）

    Returns:
        self（便于链式调用）
    """
```

**关键行为：**
- 如果设置了 `context_manager`，委托给 `context_manager.add_payload()` 处理
- 否则，同 role 的连续 payload 会自动合并内容（相邻 USER 消息合并等）
- 支持 `position` 参数精确控制插入位置

**使用示例：**
```python
request = LLMRequest(model_set=models)

# 链式调用
request \
    .add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful."))) \
    .add_payload(LLMPayload(ROLE.USER, Text("Hello")))

# 指定位置
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("...")), position=0)
```

### send

发送请求到 LLM。

```python
async def send(
    self,
    auto_append_response: bool = True,
    *,
    stream: bool = True,
) -> LLMResponse:
    """
    Args:
        auto_append_response: 是否自动将响应添加到 payloads
        stream: 是否使用流式传输（默认 True）

    Returns:
        LLMResponse 对象

    Raises:
        LLMError 及其子类
    """
```

**内部执行流程：**
1. 校验 `model_set`（`_validate_model_set` → `_validate_model_entry`）
2. 委托给 `execute_request()` 执行完整生命周期
3. 策略驱动的模型选择与重试
4. 上下文预处理（压缩/裁剪）
5. Provider 调用（通过 `model_client`）
6. 响应归一化（含 `tool_call_compat` 解析）
7. 观测记录分发

---

## EmbeddingRequest

```python
@dataclass(slots=True)
class EmbeddingRequest:
    """EmbeddingRequest：构建输入并执行向量请求。"""

    model_set: ModelSet
    request_name: str = ""
    inputs: list[str] = field(default_factory=list)
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.EMBEDDINGS
```

**使用示例：**
```python
from src.kernel.llm import EmbeddingRequest

req = EmbeddingRequest(model_set=embedding_models, request_name="doc_embed")
req.add_input("Hello world").add_input("Another text")
response = await req.send()
# response.embeddings: list[list[float]]
# response.model: str  — 实际使用的模型名
# response.usage: dict | None  — token 用量
```

---

## RerankRequest

```python
@dataclass(slots=True)
class RerankRequest:
    """RerankRequest：构建排序输入并执行请求。"""

    model_set: ModelSet
    request_name: str = ""
    query: str = ""
    documents: list[Any] = field(default_factory=list)
    top_n: int | None = None
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.RERANK
```

**使用示例：**
```python
from src.kernel.llm import RerankRequest

req = RerankRequest(model_set=rerank_models, request_name="doc_rerank")
req.set_query("What is AI?")
req.add_document("AI is artificial intelligence...")
req.add_document("Machine learning is a subset of AI...")
response = await req.send()
# response.results: list[RerankItem]
for item in response.results:
    print(f"score={item.relevance_score:.4f}, doc={item.document}")
```
    result = await execute_tool(call.name, call.args)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(result, call_id=call.id)))

# 获取最终答案
final = await request.send()
print(await final)
```

### 模式 4：故障转移和重试

```python
models = [
    {"client_type": "openai", "model_identifier": "gpt-4", "api_key": "key1", "max_retry": 2},
    {"client_type": "openai", "model_identifier": "gpt-3.5-turbo", "api_key": "key2", "max_retry": 3},
]

request = LLMRequest(model_set=models, request_name="important")
request.add_payload(LLMPayload(ROLE.USER, Text("Important query")))

try:
    response = await request.send()
    print(await response)
except LLMError as e:
    print(f"所有模型都失败了: {e}")
```

### 模式 5：流式处理

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.USER, Text("Write a long story")))

response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)
print()
```

---

## 错误处理

### 异常类型

```python
from src.kernel.llm import (
    LLMError,
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMAuthenticationError,
)

try:
    response = await request.send()
except LLMAuthenticationError as e:
    print(f"认证失败，请检查 API key")
except LLMRateLimitError as e:
    if e.retry_after:
        await asyncio.sleep(e.retry_after)
except LLMTimeoutError as e:
    print(f"请求超时: {e.timeout}s")
except LLMError as e:
    print(f"其他 LLM 错误: {e}")
```

### 重试策略

重试由 `policy` 自动处理。每个模型配置的 `max_retry` 控制该模型的重试次数。

---

## 指标收集

### 启用/禁用指标

```python
# 启用（默认）
request = LLMRequest(model_set=models, enable_metrics=True)

# 禁用（提高性能）
request = LLMRequest(model_set=models, enable_metrics=False)
```

### 访问指标

```python
from src.kernel.llm import get_global_collector

collector = get_global_collector()

# 获取特定模型的统计
stats = collector.get_stats("gpt-4")
print(f"总请求: {stats.total_requests}")
print(f"成功率: {stats.success_rate:.2%}")
print(f"平均延迟: {stats.avg_latency:.2f}s")
```

---

## 常见问题

### Q: 如何同时调用多个请求？

A: 使用 `asyncio.gather()`：
```python
r1 = await request1.send()
r2 = await request2.send()
results = await asyncio.gather(r1, r2)
```

### Q: 如何自定义重试逻辑？

A: 创建自定义 `Policy`：
```python
from src.kernel.llm.policy import Policy

class MyPolicy(Policy):
    def new_session(self, *, model_set, request_name):
        # 自定义逻辑
```

### Q: 流式和非流式有什么区别？

A: 
- 非流式：等待完整响应，适合简短输出
- 流式：实时接收块数据，适合长输出和实时展示

### Q: payload 数量有限制吗？

A: 没有硬性限制，但总 token 数受模型限制（通常 4K-128K）。

---

## 性能优化

### 1. 禁用指标收集

```python
request = LLMRequest(model_set=models, enable_metrics=False)
```

### 2. 使用流式处理处理大输出

```python
response = await request.send(stream=True)
async for chunk in response:
    # 实时处理，而不是等待完整响应
    process(chunk)
```

### 3. 批量请求时复用 request 对象

```python
request = LLMRequest(model_set=models)
for query in queries:
    request.payloads = []  # 清空 payload
    request.add_payload(LLMPayload(ROLE.USER, Text(query)))
    response = await request.send(auto_append_response=False)
```

---

## 相关文档

- [Response 模块](./response.md) - 处理响应
- [Roles 模块](./roles.md) - 消息角色
- [Payload 模块](./payload/README.md) - 消息负载
- [Policy 模块](./policy/README.md) - 负载均衡策略
- [Monitor 模块](./monitor.md) - 指标收集

