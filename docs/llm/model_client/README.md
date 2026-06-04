# Model Client 模块

## 概述

`model_client/` 子模块实现了与各个 LLM 提供商的交互。它定义了统一的客户端接口（Chat、Embedding、Rerank），并提供了 OpenAI 和 Anthropic 的具体实现。框架设计允许轻松扩展支持其他提供商。

## 模块结构

```
model_client/
├── base.py              # 客户端接口协议（ChatModelClient / EmbeddingModelClient / RerankModelClient）
├── openai_client.py     # OpenAI 实现
├── anthropic_client.py  # Anthropic 实现
├── registry.py          # 客户端注册表（ModelClientRegistry）
├── shared.py            # 共享工具
└── __init__.py          # 公开 API
```

## StreamEvent（流事件）

```python
@dataclass(frozen=True, slots=True)
class StreamEvent:
    """provider-agnostic 的流事件。"""

    text_delta: str | None = None               # 文本增量
    tool_call_id: str | None = None             # 工具调用 ID
    tool_name: str | None = None                # 工具名称
    tool_args_delta: str | None = None          # 工具参数增量（JSON 片段）
    reasoning_block_type: str | None = None     # reasoning block 类型
    reasoning_delta: str | None = None          # reasoning 文本增量
    reasoning_signature_delta: str | None = None # reasoning 签名增量
    usage: dict[str, Any] | None = None         # token 用量信息
```

表示流式响应中的单个事件。这是提供商无关的统一格式，支持文本、工具调用、推理内容和用量信息。

---

## 客户端协议

### ChatModelClient

```python
class ChatModelClient(Protocol):
    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        """发起一次聊天请求。"""
```

### EmbeddingModelClient

```python
class EmbeddingModelClient(Protocol):
    async def create_embedding(
        self,
        *,
        model_name: str,
        inputs: list[str],
        request_name: str,
        model_set: Any,
    ) -> list[list[float]]:
        """发起一次 embedding 请求，返回向量列表。"""
```

### RerankModelClient

```python
class RerankModelClient(Protocol):
    async def create_rerank(
        self,
        *,
        model_name: str,
        query: str,
        documents: list[Any],
        top_n: int | None,
        request_name: str,
        model_set: Any,
    ) -> list[dict[str, Any]]:
        """发起一次 rerank 请求，返回排序结果。"""
```

---

## 已内置的客户端

| 客户端 | 协议 | 说明 |
|--------|------|------|
| `OpenAIChatClient` | Chat / Embedding / Rerank | 完整实现三种协议 |
| `AnthropicChatClient` | Chat | 支持 reasoning blocks 和扩展思考 |

**返回值：**
- `message`: 非流式时的完整响应文本；流式时为 None
- `tool_calls`: 非流式时解析的工具调用列表（dict 格式）；流式时为 None
- `stream_iter`: 流式迭代器；非流式时为 None

---

## OpenAIChatClient

```python
class OpenAIChatClient:
    """OpenAI provider。
    
    依赖 openai>=2.x。
    """
```

OpenAI 的具体实现。

### 内部机制

#### Payload 转换

```python
def _payloads_to_openai_messages(payloads: list[LLMPayload]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """将 LLMPayload 转换为 OpenAI 消息格式。
    
    返回 (messages, tools)
    """
```

将标准的 `LLMPayload` 转换为 OpenAI 的消息格式：

- `ROLE.SYSTEM` → `{"role": "system", "content": "..."}`
- `ROLE.USER` → `{"role": "user", "content": "..."}`
- `ROLE.ASSISTANT` → `{"role": "assistant", "content": "..."}`
- `ROLE.TOOL` → tools 列表
- `ROLE.TOOL_RESULT` → `{"role": "tool", "content": "...", "tool_call_id": "..."}`

**多模态支持：**
```python
# 图文混合会转换为 OpenAI 的 content parts 格式
{
    "role": "user",
    "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]
}
```

#### 图像处理

```python
def _image_to_data_url(value: str) -> str:
    """转换图像为 data URL。"""
```

支持多种图像格式：
- 文件路径：自动读取并 base64 编码
- Data URL：直接使用
- Base64 快捷格式：转换为标准 data URL

#### 客户端缓存

```python
def _get_client(self, *, api_key: str, base_url: str | None, timeout: float | None):
    """获取或创建 AsyncOpenAI 客户端（带缓存）。"""
```

缓存机制：
- 按 `(api_key, base_url, event_loop)` 缓存
- 避免重复创建客户端
- 线程安全

**重要：** 禁用了 OpenAI SDK 的自动重试（`max_retries=0`），重试由 policy 层完全控制。

### 使用示例

```python
from src.kernel.llm import LLMRequest, LLMPayload, Text, ROLE

models = [{
    "client_type": "openai",
    "model_identifier": "gpt-4",
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
}]

request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.USER, Text("Hello!")))

response = await request.send()
message = await response
```

---

## ModelClientRegistry

```python
@dataclass(slots=True)
class ModelClientRegistry:
    """provider -> client 的注册表。
    
    当前默认提供 openai client；gemini/bedrock 后续可注册。
    """
    
    openai: ChatModelClient | None = None
    gemini: ChatModelClient | None = None
    bedrock: ChatModelClient | None = None
```

管理不同提供商的客户端实例。

### get_client_for_model

```python
def get_client_for_model(self, model: dict[str, Any]) -> ChatModelClient:
    """根据单个模型配置决定使用哪个 provider。
    
    当前阶段以 `client_type` 为准：openai/gemini/bedrock。
    """
```

根据模型配置的 `client_type` 返回相应的客户端。

**使用示例：**
```python
registry = ModelClientRegistry()

# OpenAI 模型
model_config = {
    "client_type": "openai",
    "model_identifier": "gpt-4",
    "api_key": "sk-..."
}
client = registry.get_client_for_model(model_config)

# 或使用其他提供商
model_config2 = {
    "client_type": "gemini",
    "model_identifier": "gemini-pro",
    "api_key": "..."
}
# client2 = registry.get_client_for_model(model_config2)  # 需要先注册
```

### 注册自定义客户端

```python
from src.kernel.llm.model_client import ChatModelClient, ModelClientRegistry

class MyCustomClient:
    async def create(self, *, model_name, payloads, tools, request_name, model_set, stream):
        # 实现自定义逻辑
        pass

registry = ModelClientRegistry()
registry.gemini = MyCustomClient()  # 注册自定义客户端

# 现在可以使用
model_config = {"client_type": "gemini", ...}
client = registry.get_client_for_model(model_config)
```

---

## 扩展支持新的提供商

### 步骤 1：实现客户端

```python
from src.kernel.llm.model_client import ChatModelClient, StreamEvent
from src.kernel.llm import LLMPayload, Tool
from typing import AsyncIterator

class GeminiClient:
    """Google Gemini 客户端实现。"""
    
    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[Tool],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None]:
        """发起 Gemini 请求。"""
        
        # 1. 转换 payloads 为 Gemini 格式
        messages = self._convert_to_gemini_format(payloads)
        
        # 2. 初始化 Gemini 客户端
        client = self._get_gemini_client(model_set)
        
        # 3. 发起请求
        if stream:
            stream_iter = self._create_stream(client, messages, tools)
            return None, None, stream_iter
        else:
            message, tool_calls = await self._create_non_stream(client, messages, tools)
            return message, tool_calls, None
    
    def _convert_to_gemini_format(self, payloads: list[LLMPayload]) -> list[dict]:
        """转换为 Gemini API 格式。"""
        # 实现转换逻辑
        pass
```

### 步骤 2：注册客户端

```python
from src.kernel.llm.model_client import ModelClientRegistry

registry = ModelClientRegistry()
registry.gemini = GeminiClient()

# 在 LLMRequest 中使用
request = LLMRequest(model_set=models, clients=registry)
```

### 步骤 3：配置模型

```python
models = [
    {
        "client_type": "gemini",
        "model_identifier": "gemini-pro",
        "api_key": "...",
    }
]

request = LLMRequest(model_set=models, clients=registry)
```

---

## 常见问题

### Q: 如何切换 LLM 提供商？

A: 通过 `client_type` 字段：
```python
# OpenAI
models1 = [{"client_type": "openai", "model_identifier": "gpt-4", ...}]

# Gemini（需要注册）
models2 = [{"client_type": "gemini", "model_identifier": "gemini-pro", ...}]
```

### Q: OpenAI 的重试是如何工作的？

A: OpenAI 客户端禁用了 SDK 级别的重试。重试由 `policy` 层完全控制，允许更细粒度的控制。

### Q: 如何支持新的 LLM 模型？

A: 如果模型与现有提供商兼容，只需更改 `model_identifier`：
```python
models = [{
    "client_type": "openai",
    "model_identifier": "gpt-4-turbo",  # 新模型
    "api_key": "..."
}]
```

### Q: 客户端是否线程安全？

A: `OpenAIChatClient` 使用 `threading.Lock` 保护客户端缓存，是线程安全的。

### Q: 能否同时使用多个提供商？

A: 可以。使用多模型配置和轮询策略：
```python
models = [
    {"client_type": "openai", "model_identifier": "gpt-4", ...},
    {"client_type": "gemini", "model_identifier": "gemini-pro", ...},
]
request = LLMRequest(model_set=models)  # 会轮流尝试
```

---

## 相关文档

- [Request 模块](../request.md)
- [Response 模块](../response.md)
- [Policy 模块](../policy/README.md)

