# Response 模块

## 概述

`response.py` 定义了 `LLMResponse` 类，是处理 LLM 响应的核心类。它提供了统一的接口支持两种消费方式：
- **await 模式**：收集完整响应后返回
- **async for 模式**：流式处理响应数据

此外，它还支持：
- 推理内容（reasoning）的结构化解析（如 DeepSeek-R1、OpenAI o1 的思考链）
- 自动工具调用解析与 `tool_call_compat` 兼容模式
- 响应自动追加到对话历史（通过 `LLMContextManager`）

## 类定义

```python
@dataclass(slots=True)
class LLMResponse:
    """对缓冲式 provider 结果或实时流结果的统一包装。"""

    _stream: AsyncIterator[StreamEvent] | None          # 流式迭代器
    _upper: "LLMRequest | LLMResponse"                  # 上级请求/响应
    _auto_append_response: bool                         # 是否自动追加到对话

    payloads: list[LLMPayload]                          # 当前对话的所有 payload
    model_set: "ModelSet"                               # 模型配置
    context_manager: LLMContextManager | None = None    # 上下文管理器

    message: str | None = None                          # 完整的文本响应
    reasoning_content: str | None = None                # 推理过程纯文本
    reasoning_parts: list[ReasoningText] | None = None  # 推理过程结构化片段
    call_list: list[ToolCall] | None = None             # 工具调用列表（可空）
    tool_call_compat: bool = False                      # 工具调用兼容模式

    _consumed: bool = False                             # 是否已被消费
    _appended_to_context: bool = False                  # 是否已追加到上下文
```

## 核心属性

### message

**类型：** `str | None`

模型的文本响应。流式/非流式完成后填充。

### reasoning_content / reasoning_parts

**类型：** `str | None` / `list[ReasoningText] | None`

模型的推理/思考过程：
- `reasoning_content`：纯文本形式的推理链
- `reasoning_parts`：带签名的结构化推理片段（如 Anthropic 的 thinking blocks）

仅在支持 reasoning 的模型（如 DeepSeek-R1、OpenAI o1/o3、Anthropic Claude）上有效。

### call_list

**类型：** `list[ToolCall] | None`

模型请求的工具调用列表。可能为 `None`（消费前）或空列表（无工具调用）。

### tool_call_compat

**类型：** `bool`

当模型不支持原生 function calling 时，启用兼容模式从纯文本中解析工具调用（XML/JSON 格式）。

### context_manager

**类型：** `LLMContextManager | None`

从上游 `LLMRequest` 自动继承的上下文管理器。用于在自动追加响应时保持裁剪/校验行为一致。

## 使用方式

### 方式 1：非流式（await）

```python
response = await request.send(stream=False)
message = await response
print(message)

# 检查推理内容
if response.reasoning_content:
    print(f"思考过程: {response.reasoning_content}")

# 检查工具调用
if response.call_list:
    for call in response.call_list:
        print(f"工具: {call.name}({call.args})")
```

### 方式 2：流式（async for）

```python
response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)

# 消费完成后可访问完整信息
print(f"推理: {response.reasoning_content}")
print(f"工具调用: {response.call_list}")
```

### 方式 3：链式继续对话

```python
# 利用 to_payload() 将响应转为 payload 继续对话
response = await request.send()
payload = response.to_payload()
# payload 包含 assistant 的文本、reasoning、tool_calls
```

response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)
```

**特点：**
- 实时接收数据块
- 可以在消费过程中处理数据
- 一旦开始迭代，响应被消费

### 方式 3：混合模式

```python
# 同一个 LLMResponse 对象不能既 await 又 async for
# 但可以根据需要选择合适的方式

# ✓ 正确：选择其中一种
response1 = await request.send(stream=False)
msg = await response1

# ✓ 正确：选择其中一种
response2 = await request.send(stream=True)
async for chunk in response2:
    print(chunk)

# ✗ 错误：不能混用
response3 = await request.send()
content = await response3
async for chunk in response3:  # 错误！
    pass
```

---

## 核心方法

### __await__

使 `LLMResponse` 可以作为 awaitable 对象。

```python
# 这些都是等价的
message = await response
message = await response._collect_full_response()
```

**使用示例：**
```python
response = await request.send(stream=False)
message = await response
print(message)
```

### __aiter__

使 `LLMResponse` 可以作为异步迭代器。

```python
response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)
```

**内部流程：**
1. 检查是否已消费
2. 标记为已消费
3. 若无流数据，返回现有消息
4. 否则逐块迭代流数据
5. 累积工具调用信息
6. 完成后自动追加响应到对话历史

---

## 工具调用处理

### 自动解析工具调用

```python
# 使用工具
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))
request.add_payload(LLMPayload(ROLE.USER, Text("What is 2 + 2?")))

# 获取响应（自动解析工具调用）
response = await request.send()
result = await response

# 查看工具调用
for call in response.call_list:
    print(f"工具: {call.name}")
    print(f"参数: {call.args}")
    print(f"ID: {call.id}")
```

### 处理工具调用结果

```python
response = await request.send()
result = await response

# 处理工具调用
for tool_call in response.call_list:
    # 执行工具
    tool_result = await execute_tool(tool_call.name, tool_call.args)
    
    # 回传结果
    request.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(
                value=tool_result,
                call_id=tool_call.id,
                name=tool_call.name
            )
        )
    )

# 获取最终答案
final_response = await request.send()
final_message = await final_response
```

### 内部工具调用累积

```python
class _ToolCallAccumulator:
    """在流式模式中累积工具调用信息。"""
    
    def apply(self, event: StreamEvent) -> None:
        """应用单个流事件。"""
    
    def finalize(self) -> list[ToolCall]:
        """返回最终的工具调用列表。"""
```

---

## 自动追加响应

### 启用自动追加

```python
# 自动追加到对话历史（默认）
response = await request.send(auto_append_response=True)
msg = await response

# 之后可以继续对话
request.add_payload(LLMPayload(ROLE.USER, Text("Tell me more")))
response2 = await request.send()
msg2 = await response2
```

**流程：**
1. 模型返回响应
2. 响应自动创建 `LLMPayload(ROLE.ASSISTANT, Text(message))`
3. 自动添加到 `request.payloads`
4. 下一次请求包含完整对话历史

### 禁用自动追加

```python
# 不自动追加
response = await request.send(auto_append_response=False)
msg = await response

# request.payloads 中没有本次响应
# 可以手动添加或处理

request.add_payload(LLMPayload(ROLE.ASSISTANT, Text(msg)))
```

---

## 使用模式

### 模式 1：简单的单次请求

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

response = await request.send(stream=False)
message = await response
print(message)
```

### 模式 2：多轮对话

```python
request = LLMRequest(model_set=models, request_name="chat")

# 第一轮
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("What is AI?")))
resp1 = await request.send(auto_append_response=True, stream=False)
msg1 = await resp1
print(msg1)

# 第二轮（自动包含第一轮响应）
request.add_payload(LLMPayload(ROLE.USER, Text("Tell me more.")))
resp2 = await request.send(auto_append_response=True, stream=False)
msg2 = await resp2
print(msg2)

# 第三轮
request.add_payload(LLMPayload(ROLE.USER, Text("Give examples.")))
resp3 = await request.send(auto_append_response=True, stream=False)
msg3 = await resp3
print(msg3)
```

### 模式 3：流式处理

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.USER, Text("Write a long story about...")))

response = await request.send(stream=True, auto_append_response=True)
full_message = []
async for chunk in response:
    print(chunk, end="", flush=True)
    full_message.append(chunk)

print()
print(f"\n完整消息: {''.join(full_message)}")
```

### 模式 4：工具调用处理

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.TOOL, Tool(SearchTool)))
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))
request.add_payload(LLMPayload(ROLE.USER, Text("What's 2+2 and search for Python?")))

# 第一次请求可能返回工具调用
response = await request.send(auto_append_response=True, stream=False)
message = await response

print(f"响应消息: {message}")
print(f"工具调用数: {len(response.call_list)}")

# 处理工具调用
for call in response.call_list:
    print(f"调用: {call.name}({call.args})")
    
    # 执行工具
    result = await execute_tool(call.name, call.args)
    
    # 回传结果
    request.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(value=result, call_id=call.id)
        )
    )

# 第二次请求获取最终答案
final_response = await request.send(auto_append_response=True, stream=False)
final_message = await final_response
print(f"最终答案: {final_message}")
```

### 模式 5：错误处理和重试

```python
from src.kernel.llm import LLMError, LLMRateLimitError, LLMTimeoutError

request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.USER, Text("Query")))

try:
    response = await request.send(stream=False)
    message = await response
    print(message)
except LLMRateLimitError as e:
    if e.retry_after:
        print(f"限流，等待 {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
except LLMTimeoutError as e:
    print(f"超时: {e.timeout}s")
except LLMError as e:
    print(f"错误: {e}")
```

---

## 高级特性

### 检查响应是否已消费

```python
response = await request.send()

if response._consumed:
    print("响应已被消费")
else:
    print("响应未被消费")

# 消费响应
msg = await response

# 现在 _consumed 为 True
```

### 获取响应元数据

```python
response = await request.send(stream=False)
message = await response

# 获取元数据
print(f"消息: {response.message}")
print(f"工具调用: {response.call_list}")
print(f"所有 payload: {len(response.payloads)} 条消息")
print(f"模型配置: {response.model_set}")
```

---

## 常见错误

### ❌ 错误：重复消费响应

```python
response = await request.send()

# 第一次消费（成功）
msg1 = await response

# 第二次消费（失败）
try:
    msg2 = await response  # LLMResponseConsumedError!
except LLMResponseConsumedError:
    print("不能重复消费响应")
```

**解决：** 保存响应内容
```python
response = await request.send()
message = await response
print(message)  # 使用保存的内容
```

### ❌ 错误：混用 await 和 async for

```python
response = await request.send()

# 不能先 await 再 async for
content = await response
async for chunk in response:  # 错误！
    print(chunk)
```

**解决：** 选择一种方式
```python
# 方式 1：await
response = await request.send(stream=False)
message = await response

# 方式 2：async for
response = await request.send(stream=True)
async for chunk in response:
    print(chunk)
```

### ❌ 错误：忘记处理工具调用

```python
response = await request.send()
message = await response

# 如果有工具调用但没有处理，对话流被中断
if response.call_list:
    print("有未处理的工具调用!")
```

**解决：** 完成工具调用循环
```python
for call in response.call_list:
    result = await execute_tool(call.name, call.args)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(result, call_id=call.id)))
```

---

## 性能考虑

### 1. 选择合适的流式模式

```python
# 短响应：使用非流式（更快）
response = await request.send(stream=False)
msg = await response

# 长响应：使用流式（更节省内存，实时展示）
response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)
```

### 2. 禁用自动追加（如果不需要）

```python
# 如果响应不需要追加到历史
response = await request.send(auto_append_response=False)
msg = await response
```

### 3. 及时消费流数据

```python
# 不要保存流迭代器，及时消费
response = await request.send(stream=True)
async for chunk in response:
    process_immediately(chunk)  # 立即处理，不缓存
```

---

## 相关文档

- [Request 模块](./request.md) - 发送请求
- [Roles 模块](./roles.md) - 消息角色
- [Payload 模块](./payload/README.md) - 消息负载
- [Exceptions 模块](./exceptions.md) - 异常处理
- [Monitor 模块](./monitor.md) - 指标收集

