# Tooling 模块

## 概述

`tooling.py` 实现了 LLM 的工具调用系统，包括工具定义、调用、结果处理和执行管理。这是实现 AI 与外部系统集成的核心机制。

## 核心接口

### LLMUsable 协议

```python
class LLMUsable(Protocol):
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """将组件描述为可被 LLM 调用的 schema。"""
        ...
```

所有可被 LLM 调用的工具必须实现此协议。

**示例实现：**
```python
class SearchTool:
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web for information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {"type": "integer", "description": "Max results"}
                    },
                    "required": ["query"]
                }
            }
        }
```

---

## ToolCall（工具调用）

```python
@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str | None                    # 工具调用的唯一 ID
    name: str                         # 工具名称
    args: dict[str, Any] | str        # 工具参数（字典或 JSON 字符串）
```

表示 LLM 请求的一个工具调用。

**属性详解：**
- `id`: 工具调用的唯一标识，用于匹配工具结果
- `name`: 工具的函数名称
- `args`: 工具的参数。通常是 dict，但某些实现可能是 JSON 字符串

**使用示例：**
```python
from src.kernel.llm import ToolCall

# 来自 LLM 的工具调用
call = ToolCall(
    id="call_12345",
    name="search",
    args={"query": "Python tutorial", "limit": 5}
)

print(f"工具: {call.name}")
print(f"ID: {call.id}")
print(f"参数: {call.args}")
```

---

## Tool（工具声明）

```python
@dataclass(frozen=True, slots=True)
class Tool:
    """工具声明（用于告诉模型有哪些可调用工具）。"""
    tool: type[LLMUsable]
    
    def to_openai_tool(self) -> dict[str, Any]:
        """转换为 OpenAI tools 格式。"""
```

封装工具类，用于在请求中声明。

**使用示例：**
```python
from src.kernel.llm import Tool, LLMPayload, ROLE

# 创建工具声明
search_tool = Tool(SearchTool)
calculator_tool = Tool(CalculatorTool)

# 添加到请求
request.add_payload(LLMPayload(ROLE.TOOL, search_tool))
request.add_payload(LLMPayload(ROLE.TOOL, calculator_tool))

# 或使用列表
request.add_payload(LLMPayload(ROLE.TOOL, [search_tool, calculator_tool]))
```

---

## ToolResult（工具结果）

```python
@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行结果。
    
    value：建议为 dict/str；若为 dict，会默认 JSON 序列化。
    call_id：用于 OpenAI tool message 的 tool_call_id。
    name：可选，便于调试；OpenAI tool message 不需要。
    """
    value: Any
    call_id: str | None = None
    name: str | None = None
    
    def to_text(self) -> str:
        """将结果转换为文本形式。"""
```

表示工具执行的结果。

**属性详解：**
- `value`: 工具执行的结果。可以是任意类型（dict、list、str、int 等）
- `call_id`: 匹配的工具调用 ID，用于 OpenAI 等 API 关联结果
- `name`: 工具名称，可选，用于调试

**使用示例：**
```python
from src.kernel.llm import ToolResult

# 简单结果
result1 = ToolResult(
    value="Search completed successfully",
    call_id="call_12345"
)

# 结构化结果
result2 = ToolResult(
    value={
        "results": [
            {"title": "Python.org", "url": "https://python.org"},
            {"title": "Python Docs", "url": "https://docs.python.org"}
        ],
        "total": 2
    },
    call_id="call_12345",
    name="search"
)

# 数字结果
result3 = ToolResult(
    value=15,  # 2 + 2 = 4... 实际上是其他操作的结果
    call_id="call_12346"
)
```

**to_text() 方法：**
```python
# 字符串结果
result = ToolResult("Success")
result.to_text()  # "Success"

# 字典结果（自动 JSON 序列化）
result = ToolResult({"key": "value"})
result.to_text()  # '{"key": "value"}'

# 其他类型（转换为字符串）
result = ToolResult(123)
result.to_text()  # "123"
```

---

## ToolRegistry（工具注册表）

```python
class ToolRegistry:
    """工具注册表，支持动态注册和发现工具。"""
    
    def __init__(self) -> None:
        """初始化工具注册表。"""
```

管理工具的注册、查询和发现。

### 核心方法

#### register

```python
def register(self, tool: type[LLMUsable], name: str | None = None) -> None:
    """注册工具。
    
    Args:
        tool: 工具类（需实现 LLMUsable 协议）。
        name: 工具名称，若不提供则从 schema 中提取。
    """
```

**使用示例：**
```python
from src.kernel.llm import ToolRegistry

registry = ToolRegistry()

# 自动提取名称
registry.register(SearchTool)

# 指定名称
registry.register(CalculatorTool, name="calc")

# 注册多个工具
registry.register(FileSystemTool)
registry.register(NetworkTool)
```

#### get

```python
def get(self, name: str) -> type[LLMUsable] | None:
    """根据名称获取工具类。"""
```

**使用示例：**
```python
tool_class = registry.get("search")
if tool_class:
    print(f"Found tool: {tool_class}")
else:
    print("Tool not found")
```

#### list_all

```python
def list_all(self) -> list[dict[str, Any]]:
    """获取所有已注册工具的 schema 列表。"""
```

返回 OpenAI 格式的工具 schema 列表。

**使用示例：**
```python
registry = ToolRegistry()
registry.register(SearchTool)
registry.register(CalculatorTool)

schemas = registry.list_all()
# [
#     {
#         "type": "function",
#         "function": {...}
#     },
#     ...
# ]
```

#### get_all_names

```python
def get_all_names(self) -> list[str]:
    """获取所有已注册工具的名称。"""
```

**使用示例：**
```python
names = registry.get_all_names()
print(f"Available tools: {', '.join(names)}")
```

---

## LLMUsableExecution（执行包装器）

`LLMUsableExecution` 是框架内部的执行包装对象，用于让 Tool / Action / Agent
共享同一套并行调度逻辑。

```python
LLMUsableExecutionStatus = Literal["_WORKING", "_READY", "_DONE"]
```

状态含义：

- `_WORKING`：执行仍在运行，调度器会先跳过
- `_READY`：异步生成器已经准备完毕并暂停，等待统一调度器继续推进
- `_DONE`：执行已经完成，结果可读取

`execute()` 支持两种写法：

```python
# 普通 coroutine：适合查询、搜索、记忆读取等顺序不敏感能力
async def execute(self, query: str) -> tuple[bool, str]:
    result = await search(query)
    return True, result


# 异步生成器：适合 send_text 这类顺序敏感动作
async def execute(self, content: str):
    prepared = content.strip()
    yield None
    success = await self.send(prepared)
    yield success, f"已发送消息: {prepared}"
```

异步生成器的最后一次非空 `yield` 会作为最终结果；空 `yield` 只表示“已经准备好，
可以等待统一调度”。一次 LLM 响应中的普通 tool calls 会通过
`src.core.utils.llm_tool_call.run_tool_call()` 批量执行，结果按原始 call 顺序写回
`TOOL_RESULT`。

---

## 完整工具调用流程

### 步骤 1：定义工具

```python
from src.kernel.llm import LLMPayload, Tool, ROLE, LLMRequest, LLMResponse, ToolCall, ToolResult
from src.kernel.llm import Text

class CalculatorTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Perform mathematical calculations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["add", "subtract", "multiply", "divide"],
                            "description": "Operation to perform"
                        },
                        "a": {"type": "number", "description": "First number"},
                        "b": {"type": "number", "description": "Second number"}
                    },
                    "required": ["operation", "a", "b"]
                }
            }
        }

class SearchTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search for information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"}
                    },
                    "required": ["query"]
                }
            }
        }
```

### 步骤 2：声明工具

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You have access to tools.")))
request.add_payload(LLMPayload(ROLE.TOOL, [
    Tool(CalculatorTool),
    Tool(SearchTool)
]))
request.add_payload(LLMPayload(ROLE.USER, Text("What's 2+2? And search for Python.")))
```

### 步骤 3：执行工具调用

```python
async def execute_tool(name: str, args: dict):
    """执行工具"""
    if name == "calculator":
        op = args["operation"]
        a, b = args["a"], args["b"]
        if op == "add":
            return a + b
        elif op == "subtract":
            return a - b
        elif op == "multiply":
            return a * b
        elif op == "divide":
            return a / b if b != 0 else "Error: Division by zero"
    elif name == "search":
        query = args["query"]
        # 执行搜索（演示）
        return f"Search results for '{query}': ..."
    return "Unknown tool"

# 获取 LLM 的工具调用
response = await request.send()
message = await response

print(f"LLM Response: {message}")
print(f"Tool calls: {response.call_list}")

# 处理每个工具调用
for tool_call in response.call_list:
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
```

### 步骤 4：获取最终答案

```python
# LLM 基于工具结果生成最终答案
final_response = await request.send()
final_message = await final_response

print(f"Final Answer: {final_message}")
```

---

## 使用模式

### 模式 1：简单工具

```python
class TimeTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": "get_current_time",
            "description": "Get the current time",
            "parameters": {"type": "object", "properties": {}}
        }

request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.TOOL, Tool(TimeTool)))
request.add_payload(LLMPayload(ROLE.USER, Text("What time is it?")))

response = await request.send()
```

### 模式 2：复杂参数工具

```python
class DataAnalysisTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": "analyze_data",
            "description": "Analyze data with various algorithms",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {"type": "number"}
                    },
                    "algorithm": {
                        "type": "string",
                        "enum": ["mean", "median", "std", "correlation"]
                    },
                    "options": {
                        "type": "object",
                        "properties": {
                            "window_size": {"type": "integer"}
                        }
                    }
                },
                "required": ["data", "algorithm"]
            }
        }
```

### 模式 3：使用 ToolRegistry

```python
registry = ToolRegistry()
registry.register(SearchTool, "search")
registry.register(CalculatorTool, "calc")
registry.register(TimeTool, "time")

# 获取所有工具的 schema
schemas = registry.list_all()

# 在请求中使用
for schema in schemas:
    request.add_payload(LLMPayload(ROLE.TOOL, Tool(...)))
```

### 模式 4：批量执行一次响应中的工具调用

```python
from src.core.utils.llm_tool_call import run_tool_call

await run_tool_call(
    calls=response.call_list,
    response=response,
    usable_map=usable_map,
    trigger_msg=message,
    plugin=plugin,
    stream_id=stream_id,
)
```

---

## 最佳实践

### 1. 清晰的工具定义

```python
# ✓ 好的做法
class EmailTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": "send_email",
            "description": "Send an email to a recipient",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Email address"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"}
                },
                "required": ["to", "subject", "body"]
            }
        }

# ✗ 不好的做法
class EmailTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "name": "email",
            "description": "Send email",
            "parameters": {}  # 缺少参数定义
        }
```

### 2. 错误处理

```python
async def execute_tool(name: str, args: dict):
    """安全的工具执行"""
    try:
        if name == "calculator":
            result = perform_calculation(args)
            return result
    except ValueError as e:
        return f"Invalid input: {e}"
    except Exception as e:
        logger.error(f"Tool execution failed: {e}")
        return f"Error: {type(e).__name__}"
```

### 3. 参数验证

```python
def validate_calculator_args(args: dict) -> tuple[bool, str]:
    """验证计算器参数"""
    if "operation" not in args:
        return False, "Missing 'operation' parameter"
    if "a" not in args or "b" not in args:
        return False, "Missing 'a' or 'b' parameter"
    
    if not isinstance(args["a"], (int, float)):
        return False, "'a' must be a number"
    if not isinstance(args["b"], (int, float)):
        return False, "'b' must be a number"
    
    return True, ""

async def execute_tool(name: str, args: dict):
    if name == "calculator":
        valid, error = validate_calculator_args(args)
        if not valid:
            return f"Validation error: {error}"
        # 执行...
```

### 4. 循环调用处理

```python
async def run_tool_loop(request, max_iterations=10):
    """处理多轮工具调用"""
    for iteration in range(max_iterations):
        response = await request.send()
        message = await response
        
        if not response.call_list:
            # 没有工具调用，对话结束
            return message
        
        # 处理工具调用
        for call in response.call_list:
            result = await execute_tool(call.name, call.args)
            request.add_payload(
                LLMPayload(ROLE.TOOL_RESULT, ToolResult(result, call_id=call.id))
            )
    
    # 超过最大迭代次数
    return "Tool loop exceeded maximum iterations"
```

---

## 常见问题

### Q: 如何处理工具异常？

A: 统一执行器会捕获单个 tool call 的异常，并为对应调用写回失败的
`TOOL_RESULT`，不会因为一个调用失败而打乱整批结果的写回顺序：

```python
await run_tool_call(
    calls=response.call_list,
    response=response,
    usable_map=usable_map,
    trigger_msg=message,
    plugin=plugin,
)
```

### Q: 能否动态注册工具？

A: 可以。使用 `ToolRegistry`：
```python
registry = ToolRegistry()
for tool_class in available_tools:
    registry.register(tool_class)
```

### Q: 工具调用会超时吗？

A: 当前公共 tool call 执行器负责并行调度和结果写回，不在这里直接配置超时。
需要超时保护时，应在具体组件的 `execute()` 内部使用 `asyncio.timeout()`，
或交给更外层的任务/watchdog 机制处理。

### Q: 能否在工具中调用其他工具？

A: 可以，但需要谨慎处理。推荐使用嵌套的 `ToolRegistry`。

---

## 相关文档

- [Payload 模块](./payload.md) - 消息结构
- [Content 模块](./content.md) - 内容类型
- [Request 模块](../request.md) - 请求发送
- [Response 模块](../response.md) - 响应处理

