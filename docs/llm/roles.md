# Roles 模块

## 概述

`roles.py` 定义了 LLM 消息系统中的角色（Role）枚举。每条消息都必须关联一个角色，用于标识消息的来源和用途，使 LLM 能够正确理解消息的上下文和意图。

## 角色定义

```python
class ROLE(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    TOOL_RESULT = "tool_result"
```

## 角色详解

### SYSTEM（系统角色）

**用途：** 设置 AI 的行为准则、个性、能力边界等。

**特点：**
- 通常放在对话的最开始
- 定义 AI 的角色、能力和约束
- 可选，但推荐总是包含

**使用示例：**
```python
from src.kernel.llm import ROLE, LLMPayload, Text

system_payload = LLMPayload(
    role=ROLE.SYSTEM,
    content=Text(
        "You are a helpful programming assistant. "
        "Provide clear, concise code examples with explanations. "
        "Always prioritize code clarity over brevity."
    )
)
```

**常见用法：**
```python
# 角色定义
LLMPayload(ROLE.SYSTEM, Text("You are a professional translator. Translate English to Chinese."))

# 任务定义
LLMPayload(ROLE.SYSTEM, Text("Answer questions about machine learning in a beginner-friendly way."))

# 输出格式定义
LLMPayload(ROLE.SYSTEM, Text("Always respond in JSON format: {\"answer\": \"...\", \"confidence\": 0-1}"))

# 约束定义
LLMPayload(ROLE.SYSTEM, Text("Do not provide information about illegal activities."))
```

---

### USER（用户角色）

**用途：** 表示用户输入的内容。

**特点：**
- 代表来自最终用户的请求或问题
- 可以包含文本、图像、音频等多模态内容
- 可以多次出现在对话中

**使用示例：**
```python
from src.kernel.llm import ROLE, LLMPayload, Text, Image

# 纯文本
user_text = LLMPayload(ROLE.USER, Text("What is machine learning?"))

# 多模态内容
user_multimodal = LLMPayload(
    ROLE.USER,
    [
        Text("What's in this image?"),
        Image("path/to/image.jpg")
    ]
)
```

**常见模式：**
```python
# 单轮对话
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("Hello!")))

# 多轮对话
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("What is AI?")))
request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("AI is...")))
request.add_payload(LLMPayload(ROLE.USER, Text("Tell me more.")))
```

---

### ASSISTANT（助手角色）

**用途：** 表示 AI 的响应。

**特点：**
- 来自模型的输出
- 通常由框架自动生成并追加到对话历史
- 可以多次出现在对话中（多轮对话）

**使用示例：**
```python
# 自动追加（推荐）
response = await request.send(auto_append_response=True)
# 框架自动添加 LLMPayload(ROLE.ASSISTANT, Text(response.message))

# 手动追加
request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("Previous AI response...")))
```

**对话流程示例：**
```python
# 初始化请求
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")))
request.add_payload(LLMPayload(ROLE.USER, Text("What is Python?")))

# 第一轮响应
response1 = await request.send(auto_append_response=True)
print(response1.message)

# 继续对话
request.add_payload(LLMPayload(ROLE.USER, Text("Tell me more about its libraries.")))

# 第二轮响应
response2 = await request.send(auto_append_response=True)
print(response2.message)
```

---

### TOOL（工具角色）

**用途：** 声明 AI 可以调用的工具/函数列表。

**特点：**
- 不是对话的一部分，而是能力声明
- 告诉 AI "你可以调用这些工具"
- 包含工具的 schema（名称、参数、描述等）

**使用示例：**
```python
from src.kernel.llm import ROLE, LLMPayload, Tool, ToolRegistry

# 定义工具
class CalculatorTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": "calculator",
            "description": "Performs mathematical calculations",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"}
                },
                "required": ["operation", "a", "b"]
            }
        }

# 使用工具
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))
```

**多工具场景：**
```python
# 方案 1：多个 TOOL payload
request.add_payload(LLMPayload(ROLE.TOOL, Tool(SearchTool)))
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))

# 方案 2：一个 payload 多个工具
tools = [Tool(SearchTool), Tool(CalculatorTool)]
request.add_payload(LLMPayload(ROLE.TOOL, tools))
```

---

### TOOL_RESULT（工具结果角色）

**用途：** 回传工具执行的结果给 AI。

**特点：**
- 告诉 AI "你请求的工具已执行，这是结果"
- 通常在 TOOL_CALL 之后使用
- 包含工具调用的 ID（便于 OpenAI 等 API 追踪）

**使用示例：**
```python
from src.kernel.llm import ROLE, LLMPayload, ToolResult

# AI 请求调用工具
response = await request.send()
tool_calls = response.call_list  # [ToolCall(...), ...]

# 执行工具
for tool_call in tool_calls:
    result = await execute_tool(tool_call.name, tool_call.args)
    
    # 回传结果
    request.add_payload(
        LLMPayload(
            ROLE.TOOL_RESULT,
            ToolResult(
                value=result,
                call_id=tool_call.id,
                name=tool_call.name
            )
        )
    )

# 继续对话（AI 可以根据工具结果做出响应）
response = await request.send()
```

**完整工具调用流程：**
```python
# 1. 声明工具
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")))
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))
request.add_payload(LLMPayload(ROLE.USER, Text("What is 2 + 3?")))

# 2. 获取工具调用
response = await request.send()
# response.call_list = [ToolCall(name="calculator", args={"operation": "add", "a": 2, "b": 3}, ...)]

# 3. 执行工具
result = 2 + 3  # 5

# 4. 回传结果
request.add_payload(
    LLMPayload(
        ROLE.TOOL_RESULT,
        ToolResult(
            value=result,
            call_id=response.call_list[0].id
        )
    )
)

# 5. 获取最终答案
final_response = await request.send()
# final_response.message = "The answer is 5."
```

---

## 角色组合规则

### 标准对话流

```
SYSTEM (可选) → USER → ASSISTANT → USER → ASSISTANT → ...
```

### 带工具的对话流

```
SYSTEM → TOOL (工具声明) → USER → 
ASSISTANT (工具调用) → TOOL_RESULT (工具结果) → 
ASSISTANT (最终答案) → USER → ...
```

### 典型完整场景

```python
request = LLMRequest(model_set=models, request_name="complete_example")

# 1. 系统提示 + 工具声明
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant with access to tools.")))
request.add_payload(LLMPayload(ROLE.TOOL, [Tool(SearchTool), Tool(CalculatorTool)]))

# 2. 第一轮：用户问题
request.add_payload(LLMPayload(ROLE.USER, Text("How many people live in China? And what's 100*50?")))

# 3. 获取响应（可能包含工具调用）
response = await request.send(auto_append_response=True)

# 4. 处理工具调用
for tool_call in response.call_list:
    result = await execute_tool(tool_call.name, tool_call.args)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(value=result, call_id=tool_call.id)))

# 5. 获取最终答案
final_response = await request.send(auto_append_response=True)
```

---

## 代码中的使用

### 检查角色

```python
# 检查角色是否为某种类型
payload = LLMPayload(ROLE.USER, Text("Hello"))

if payload.role == ROLE.USER:
    print("这是用户消息")

if payload.role in (ROLE.SYSTEM, ROLE.USER):
    print("这是对话消息")
```

### 角色转换

```python
# 从字符串获取角色
role_str = "user"
role = ROLE(role_str)  # ROLE.USER

# 获取角色的字符串值
role = ROLE.SYSTEM
role_value = role.value  # "system"
```

### 构建角色映射

```python
ROLE_DESCRIPTION = {
    ROLE.SYSTEM: "系统提示，定义 AI 行为",
    ROLE.USER: "用户输入",
    ROLE.ASSISTANT: "AI 响应",
    ROLE.TOOL: "工具声明",
    ROLE.TOOL_RESULT: "工具执行结果",
}

for role, description in ROLE_DESCRIPTION.items():
    print(f"{role.value}: {description}")
```

---

## 常见错误

### ❌ 错误：角色顺序混乱

```python
# 错误示例
request.add_payload(LLMPayload(ROLE.ASSISTANT, Text("...")))  # AI 先说？
request.add_payload(LLMPayload(ROLE.USER, Text("...")))       # 再是用户？
```

**正确做法：** 用户先提问，AI 再回答。

### ❌ 错误：忘记工具声明

```python
# 错误示例
request.add_payload(LLMPayload(ROLE.USER, Text("...")))
# AI 不知道有哪些工具可用！

# 正确做法
request.add_payload(LLMPayload(ROLE.TOOL, Tool(MyTool)))
request.add_payload(LLMPayload(ROLE.USER, Text("...")))
```

### ❌ 错误：工具调用但不回传结果

```python
# 错误示例
response = await request.send()
# AI 调用了工具，但没有回传结果
request.add_payload(LLMPayload(ROLE.USER, Text("接下来呢？")))
# 错误的上下文流

# 正确做法
for tool_call in response.call_list:
    result = await execute_tool(tool_call.name, tool_call.args)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(value=result)))
```

---

## 最佳实践

### 1. 始终包含 SYSTEM 角色

```python
# 好的实践
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")))
request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))

# 不推荐（缺少上下文）
request.add_payload(LLMPayload(ROLE.USER, Text("Hello")))
```

### 2. 清晰地分离对话和工具

```python
# 清晰的结构
request.add_payload(LLMPayload(ROLE.SYSTEM, "System instruction"))
request.add_payload(LLMPayload(ROLE.TOOL, [Tool(ToolA), Tool(ToolB)]))  # 工具声明
request.add_payload(LLMPayload(ROLE.USER, "User question"))              # 对话开始
```

### 3. 完整的工具调用流

```python
# 总是完成工具调用的完整周期
response = await request.send()
for call in response.call_list:
    result = await execute(call)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(result)))
# 获取最终答案
final = await request.send()
```

---

## 相关文档

- [Request 模块](./request.md) - 如何使用角色构建请求
- [Response 模块](./response.md) - 如何处理响应
- [Payload 模块](./payload/README.md) - payload 结构详解

