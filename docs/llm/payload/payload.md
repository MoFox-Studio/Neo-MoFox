# Payload 模块

## 概述

`payload.py` 定义了 `LLMPayload` 类，是 LLM 消息系统的核心数据结构。每条消息都是一个 `LLMPayload` 实例，包含一个角色（role）和一个或多个内容（content）。

## 类定义

```python
@dataclass(slots=True)
class LLMPayload:
    role: ROLE
    content: list[Content]
    
    def __init__(self, role: ROLE, content: Content | list[Content]):
        self.role = role
        self.content = _normalize_content(content)
```

## 构造方式

### 单个内容

```python
from src.kernel.llm import LLMPayload, Text, ROLE

# 文本内容
payload1 = LLMPayload(ROLE.USER, Text("Hello"))

# 图像内容
payload2 = LLMPayload(ROLE.USER, Image("photo.jpg"))
```

### 多个内容

```python
# 列表形式
payload = LLMPayload(ROLE.USER, [
    Text("What's in this image?"),
    Image("photo.jpg")
])

# 框架会自动规范化为 list[Content]
assert payload.content == [Text("..."), Image("...")]
```

## 核心特性

### 1. 内容规范化

```python
# 单个内容自动转换为列表
payload1 = LLMPayload(ROLE.USER, Text("Hello"))
assert payload1.content == [Text("Hello")]

# 列表保持不变
payload2 = LLMPayload(ROLE.USER, [Text("A"), Text("B")])
assert payload2.content == [Text("A"), Text("B")]
```

### 2. 不可变性

`content` 本身是列表（可变），但 `LLMPayload` 被用作数据容器。建议不要直接修改 `content`：

```python
payload = LLMPayload(ROLE.USER, Text("Original"))

# ✓ 可以（创建新对象）
new_payload = LLMPayload(ROLE.USER, Text("Modified"))

# ✗ 不建议（直接修改）
payload.content.append(Text("Extra"))
```

### 3. 角色验证

```python
from src.kernel.llm import ROLE

# 必须使用有效的 ROLE 枚举值
payload1 = LLMPayload(ROLE.USER, Text("Hello"))       # ✓
payload2 = LLMPayload(ROLE.SYSTEM, Text("System"))    # ✓
payload3 = LLMPayload(ROLE.ASSISTANT, Text("Reply"))  # ✓
payload4 = LLMPayload(ROLE.TOOL, Tool(MyTool))        # ✓
payload5 = LLMPayload(ROLE.TOOL_RESULT, ToolResult()) # ✓

# 使用字符串会错误
payload_bad = LLMPayload("user", Text("Bad"))  # ✗ 类型错误
```

---

## 使用场景

### 场景 1：构建多轮对话

```python
from src.kernel.llm import LLMPayload, LLMRequest, Text, ROLE

request = LLMRequest(model_set=models)

# 系统提示
request.add_payload(LLMPayload(ROLE.SYSTEM, Text(
    "You are a Python expert. Provide clear, concise code examples."
)))

# 第一轮：用户提问
request.add_payload(LLMPayload(ROLE.USER, Text(
    "How do I read a file in Python?"
)))

# 获取响应（自动追加为 ASSISTANT payload）
response1 = await request.send(auto_append_response=True)
msg1 = await response1

# 第二轮：后续提问（请求已包含前面的所有 payload）
request.add_payload(LLMPayload(ROLE.USER, Text(
    "How do I handle errors?"
)))

response2 = await request.send(auto_append_response=True)
msg2 = await response2
```

### 场景 2：多模态内容

```python
# 图文分析
payload = LLMPayload(
    ROLE.USER,
    [
        Text("Analyze this chart and provide insights:"),
        Image("sales_chart.jpg"),
        Text("Focus on trends over the last quarter.")
    ]
)

request.add_payload(payload)
response = await request.send()
```

### 场景 3：工具使用流程

```python
# 声明工具
request.add_payload(LLMPayload(ROLE.TOOL, Tool(CalculatorTool)))

# 用户请求
request.add_payload(LLMPayload(ROLE.USER, Text("What's 2+2?")))

# 获取工具调用
response = await request.send(auto_append_response=True)
message = await response

# 处理工具调用并回传结果
for call in response.call_list:
    result = await execute_tool(call)
    request.add_payload(LLMPayload(
        ROLE.TOOL_RESULT,
        ToolResult(value=result, call_id=call.id)
    ))

# 最终回复
final_response = await request.send()
```

---

## 内容访问

### 访问单个内容

```python
payload = LLMPayload(ROLE.USER, [Text("A"), Text("B")])

# 遍历所有内容
for content in payload.content:
    if isinstance(content, Text):
        print(f"Text: {content.text}")
    elif isinstance(content, Image):
        print(f"Image: {content.value}")
```

### 获取特定类型的内容

```python
def get_text_content(payload: LLMPayload) -> list[str]:
    """提取 payload 中的所有文本。"""
    texts = []
    for content in payload.content:
        if isinstance(content, Text):
            texts.append(content.text)
    return texts

texts = get_text_content(payload)
full_text = " ".join(texts)
```

---

## 高级用法

### 动态构建 payload

```python
def build_user_payload(text: str, images: list[str] | None = None) -> LLMPayload:
    """动态构建用户 payload。"""
    content_list = [Text(text)]
    
    if images:
        for img_path in images:
            content_list.append(Image(img_path))
    
    return LLMPayload(ROLE.USER, content_list)

# 使用
request.add_payload(build_user_payload("What's in these images?", ["a.jpg", "b.jpg"]))
```

### payload 转换

```python
def payload_to_dict(payload: LLMPayload) -> dict:
    """将 payload 转换为字典形式。"""
    return {
        "role": payload.role.value,
        "content": [
            {
                "type": type(c).__name__,
                "value": getattr(c, "text", getattr(c, "value", str(c)))
            }
            for c in payload.content
        ]
    }

payload = LLMPayload(ROLE.USER, [Text("Hello"), Image("photo.jpg")])
data = payload_to_dict(payload)
print(data)
# {
#     "role": "user",
#     "content": [
#         {"type": "Text", "value": "Hello"},
#         {"type": "Image", "value": "photo.jpg"}
#     ]
# }
```

### payload 序列化

```python
import json

def serialize_payload(payload: LLMPayload) -> str:
    """序列化 payload 为 JSON。"""
    content_data = []
    for c in payload.content:
        if isinstance(c, Text):
            content_data.append({"type": "text", "text": c.text})
        elif isinstance(c, Image):
            content_data.append({"type": "image", "value": c.value})
    
    data = {
        "role": payload.role.value,
        "content": content_data
    }
    return json.dumps(data, ensure_ascii=False)

payload = LLMPayload(ROLE.USER, Text("Hello"))
json_str = serialize_payload(payload)
```

---

## 常见模式

### 模式 1：清空并重建 payload

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("System")))
request.add_payload(LLMPayload(ROLE.USER, Text("Q1")))

# 清空（开始新对话）
request.payloads = [LLMPayload(ROLE.SYSTEM, Text("New system")),
                    LLMPayload(ROLE.USER, Text("New Q"))]

response = await request.send()
```

### 模式 2：条件性添加内容

```python
def add_context_payload(request, context: str | None):
    """如果有上下文，添加系统提示。"""
    if context:
        request.add_payload(LLMPayload(
            ROLE.SYSTEM,
            Text(f"Context: {context}")
        ))

add_context_payload(request, "Important: Focus on data security")
```

### 模式 3：批量构建对话

```python
messages = [
    ("system", "You are helpful."),
    ("user", "What is AI?"),
    ("assistant", "AI is..."),
    ("user", "Tell me more."),
]

request = LLMRequest(model_set=models)
for role_str, text in messages:
    role = ROLE(role_str)
    request.add_payload(LLMPayload(role, Text(text)))
```

---

## 最佳实践

### 1. 清晰的内容组织

```python
# 好的做法：逻辑清晰
payload = LLMPayload(
    ROLE.USER,
    [
        Text("Here is a question:"),
        Text("What is the capital of France?"),
        Text("Please answer concisely.")
    ]
)

# 不好的做法：混乱的组织
payload = LLMPayload(
    ROLE.USER,
    Text("Here is a question: What is the capital of France? Please answer concisely.")
)
```

### 2. 适当使用多模态

```python
# 推荐：必要时才混合多模态
payload = LLMPayload(
    ROLE.USER,
    [
        Text("Describe this image:"),
        Image("image.jpg")
    ]
)

# 过度：不必要的多模态
payload = LLMPayload(
    ROLE.USER,
    [
        Text("Describe this image:"),
        Image("image.jpg"),
        Text("Be detailed"),
        Image("another_image.jpg")
    ]
)
```

### 3. 一致的 payload 格式

```python
# 保持风格一致
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("System prompt")))
request.add_payload(LLMPayload(ROLE.USER, Text("User query")))
request.add_payload(LLMPayload(ROLE.TOOL, Tool(MyTool)))

# 避免混乱的格式
request.add_payload(LLMPayload(ROLE.SYSTEM, "System prompt"))  # 字符串，不是 Text
request.add_payload(LLMPayload(ROLE.USER, ["User query"]))      # 列表，应该是 Text
```

---

## 常见问题

### Q: 能否修改已添加的 payload？

A: 不建议。如需修改，应该重新创建对象：
```python
# 不建议
payload.content[0] = Text("New")

# 推荐
request.payloads.pop(0)  # 移除旧 payload
request.payloads.insert(0, LLMPayload(ROLE.USER, Text("New")))  # 添加新 payload
```

### Q: payload 数量有限制吗？

A: 没有硬性限制，但总 token 数受限。建议检查 token 总数是否超过模型限制。

### Q: 如何清空所有 payload？

A: 直接重置列表：
```python
request.payloads = []
```

### Q: 能否复制 payload？

A: 可以。由于 `Content` 对象是不可变的，可以安全地复制：
```python
import copy

new_payload = copy.deepcopy(old_payload)
```

---

## 相关文档

- [Content 模块](./content.md) - 内容类型详解
- [Tooling 模块](./tooling.md) - 工具系统
- [Request 模块](../request.md) - 请求发送
- [Roles 模块](../roles.md) - 角色定义

