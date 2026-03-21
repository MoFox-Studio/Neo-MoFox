# Request 模块

## 概述

`request.py` 定义了 `LLMRequest` 类，是与 LLM 交互的核心类。它负责：
- 构建消息 payload 列表
- 管理模型客户端和选择
- 应用负载均衡和重试策略
- 收集请求指标
- 执行实际的 LLM API 调用

## 类定义

```python
from dataclasses import dataclass, field

@dataclass(slots=True)
class LLMRequest:
    """LLMRequest：构建 payload 并执行请求。"""
    
    model_set: ModelSet                                  # 模型配置列表
    request_name: str = ""                              # 请求名称（用于日志和策略）
    
    payloads: list[LLMPayload] = field(default_factory=list)  # 消息 payload 列表
    policy: Policy | None = None                        # 负载均衡/重试策略
    clients: ModelClientRegistry | None = None          # 模型客户端注册表
    context_manager: LLMContextManager | None = None    # 上下文管理器（可重载）
    enable_metrics: bool = True                         # 是否启用指标收集
```

## 核心属性

### model_set

**类型：** `list[dict]`

**描述：** 模型配置列表，每个元素是一个完整的模型配置字典。

**必需配置项：**
```python
{
    "client_type": "openai",           # 提供商类型
    "model_identifier": "gpt-4",       # 模型标识
    "api_key": "sk-...",              # API 密钥
}
```

**常见可选配置项：**
```python
{
    "base_url": "https://api.openai.com/v1",  # API 基础 URL
    "max_retry": 3,                    # 最大重试次数
    "retry_interval": 1.0,             # 重试间隔（秒）
    "timeout": 30.0,                   # 请求超时（秒）
    "temperature": 0.7,                # 采样温度
    "max_tokens": 2000,                # 最大输出 token 数
}
```

**使用示例：**
```python
# 单模型
model_set = [
    {
        "client_type": "openai",
        "model_identifier": "gpt-4",
        "api_key": "sk-...",
        "base_url": "https://api.openai.com/v1",
        "max_retry": 3,
        "retry_interval": 1.0,
    }
]

# 多模型（用于故障转移和负载均衡）
model_set = [
    {
        "client_type": "openai",
        "model_identifier": "gpt-4",
        "api_key": "key1",
        "max_retry": 2,
    },
    {
        "client_type": "openai",
        "model_identifier": "gpt-3.5-turbo",
        "api_key": "key2",
        "max_retry": 3,
    }
]
```

### payloads

**类型：** `list[LLMPayload]`

**描述：** 包含所有消息的列表。每个元素是一个 `LLMPayload` 对象。

### request_name

**类型：** `str`

**描述：** 请求的名称，用于日志和策略跟踪。同一名称的请求将使用相同的负载均衡状态。

### policy

**类型：** `Policy | None`

**描述：** 负载均衡和重试策略。默认为 `RoundRobinPolicy()`。

### clients

**类型：** `ModelClientRegistry | None`

**描述：** 模型客户端注册表。默认创建内置的 `OpenAIChatClient`。

### context_manager

**类型：** `LLMContextManager | None`

**描述：** 负责 payloads 的上下文管理（QA 组裁剪、压缩等）。可传入自定义子类实例覆盖默认逻辑。

### enable_metrics

**类型：** `bool`

**描述：** 是否启用指标收集。禁用可提高性能。

## 核心方法

### __init__

初始化 LLMRequest。

```python
# 基础初始化
request = LLMRequest(model_set=models)

# 完整初始化
request = LLMRequest(
    model_set=models,
    request_name="my_request",
    policy=RoundRobinPolicy(),
    enable_metrics=True,
    context_manager=LLMContextManager(max_payloads=40)
)
```

### add_payload

添加消息 payload。

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

**使用示例：**
```python
request = LLMRequest(model_set=models)

# 链式调用
request \
    .add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful."))) \
    .add_payload(LLMPayload(ROLE.USER, Text("Hello")))

# 指定位置
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("...")), position=0)

# 追加多个
request.add_payload(LLMPayload(ROLE.USER, Text("Question 1")))
request.add_payload(LLMPayload(ROLE.USER, Text("Question 2")))
```

### 自定义上下文管理

```python
from src.kernel.llm import LLMContextManager

class MyContextManager(LLMContextManager):
    def maybe_trim(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
        # 自定义策略
        return super().maybe_trim(payloads)

request = LLMRequest(
    model_set=models,
    context_manager=MyContextManager(max_payloads=40)
)
```

### send

发送请求到 LLM。

```python
async def send(
    self, 
    auto_append_response: bool = True, 
    *, 
    stream: bool = True
) -> LLMResponse:
    """
    Args:
        auto_append_response: 是否自动将响应添加到 payloads
        stream: 是否使用流式传输
    
    Returns:
        LLMResponse 对象
    
    Raises:
        LLMError 及其子类
    """
```

**使用示例：**
```python
# 非流式，自动追加响应
response = await request.send(stream=False)
message = await response
print(message)

# 流式，自动追加响应
response = await request.send(stream=True, auto_append_response=True)
async for chunk in response:
    print(chunk, end="", flush=True)

# 流式，不追加响应
response = await request.send(stream=True, auto_append_response=False)
async for chunk in response:
    pass
```

---

## 内部实现细节

### 请求执行流程

1. **验证 model_set**
   ```python
   model_set = _validate_model_set(self.model_set)
   ```
   确保 model_set 是有效的列表。

2. **规范化 payload**
   ```python
   payloads = [_normalize_tool_result_payload(p) for p in self.payloads]
   ```
   确保 TOOL_RESULT payload 的格式正确。

3. **创建策略会话**
   ```python
   session = self.policy.new_session(model_set=model_set, request_name=self.request_name)
   ```
   根据策略获取初始模型选择。

4. **轮询尝试**
   ```python
   step = session.first()
   while step.model is not None:
       try:
           # 调用模型
       except Exception as e:
           # 分类异常
           # 获取下一步
           step = session.next_after_error(e)
   ```

5. **收集指标**
   ```python
   collector.record_request(RequestMetrics(...))
   ```

### 负载规范化

#### _normalize_tool_result_payload

```python
def _normalize_tool_result_payload(payload: LLMPayload) -> LLMPayload:
    """规范化 TOOL_RESULT payload。
    
    确保 ToolResult 的 call_id 被保留，其他内容转换为 Text。
    """
```

作用：确保各种提供商都能正确理解 TOOL_RESULT 消息。

#### _extract_tools

```python
def _extract_tools(payloads: list[LLMPayload]) -> list[Tool]:
    """从 payload 列表中提取所有 Tool 对象。"""
```

作用：收集所有工具声明，以便传递给模型客户端。

---

## 使用模式

### 模式 1：简单的单轮对话

```python
models = [{
    "client_type": "openai",
    "model_identifier": "gpt-4",
    "api_key": "sk-...",
}]

request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("What is AI?")))

response = await request.send(stream=False)
print(await response)
```

### 模式 2：多轮对话

```python
request = LLMRequest(model_set=models, request_name="chat")

# 第一轮
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("What is AI?")))
r1 = await request.send(auto_append_response=True, stream=False)
print(await r1)

# 第二轮（自动追加了第一轮的响应）
request.add_payload(LLMPayload(ROLE.USER, Text("Tell me more.")))
r2 = await request.send(auto_append_response=True, stream=False)
print(await r2)
```

### 模式 3：工具调用

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You have access to tools.")))
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))
request.add_payload(LLMPayload(ROLE.USER, Text("What is 2 + 2?")))

# 获取工具调用
response = await request.send()
for call in response.call_list:
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

