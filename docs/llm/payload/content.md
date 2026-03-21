# Content 模块

## 概述

`content.py` 定义了各种消息内容类型。这些类用于表示 LLM 消息中包含的不同类型的信息：文本、图像、音频等。

## 基类：Content

```python
@dataclass(frozen=True, slots=True)
class Content:
    """Payload content 基类。"""
```

所有内容类型的基类。

---

## Text（文本内容）

```python
@dataclass(frozen=True, slots=True)
class Text(Content):
    text: str
```

表示文本内容。

**特点：**
- 最常用的内容类型
- 支持纯 ASCII、Unicode 等所有文本格式
- 不可变（frozen=True）

**使用示例：**
```python
from src.kernel.llm import LLMPayload, Text, ROLE

# 简单文本
payload1 = LLMPayload(ROLE.USER, Text("Hello!"))

# 长文本
payload2 = LLMPayload(ROLE.SYSTEM, Text("""
You are a helpful assistant with extensive knowledge about:
- Python programming
- Machine learning
- Web development

Please provide detailed, well-structured answers.
"""))

# 在请求中使用
request.add_payload(LLMPayload(ROLE.USER, Text("What is Python?")))
```

---

## Image（图像内容）

```python
@dataclass(frozen=True, slots=True)
class Image(Content):
    """图片内容。
    
    value 可以是：
    - 文件路径（如 "pic.jpg"）
    - data URL（如 "data:image/png;base64,..."）
    - "base64|..." 形式（兼容设计稿示例）
    """
    value: str
```

表示图像内容。支持多种格式。

**支持的格式：**

1. **文件路径**
   ```python
   Image("path/to/image.jpg")
   Image("../images/photo.png")
   Image("/absolute/path/to/image.jpg")
   ```
   文件必须存在且可读。

2. **Data URL**
   ```python
   Image("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
   ```
   完整的 base64 编码数据 URL。

3. **Base64 快捷格式**
   ```python
   Image("base64|iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
   ```
   兼容设计稿的简化格式。

**使用示例：**
```python
# 本地文件
payload1 = LLMPayload(ROLE.USER, Image("photo.jpg"))

# 多模态：图片 + 文本
payload2 = LLMPayload(
    ROLE.USER,
    [
        Text("What objects are in this image?"),
        Image("screenshot.png")
    ]
)

# Data URL
payload3 = LLMPayload(ROLE.USER, Image("data:image/png;base64,...")

# 在请求中使用
request.add_payload(LLMPayload(ROLE.USER, [
    Text("Describe this chart:"),
    Image("charts/sales.png")
]))
```

**内部处理：**
- 文件路径会被转换为 data URL（自动读取文件并 base64 编码）
- Data URL 直接传递给模型
- Base64 快捷格式会被转换为标准 data URL

---

## Audio（音频内容）

```python
@dataclass(frozen=True, slots=True)
class Audio(Content):
    value: str
```

表示音频内容。

**注意：** 当前为占位符实现。具体支持的音频格式取决于实现的提供商。

**使用示例：**
```python
# 音频文件路径（假设未来支持）
payload = LLMPayload(ROLE.USER, Audio("recording.mp3"))

# 多模态：音频 + 文本
payload = LLMPayload(
    ROLE.USER,
    [
        Text("Transcribe this audio:"),
        Audio("speech.wav")
    ]
)
```

---

## Action（动作内容）

```python
@dataclass(frozen=True, slots=True)
class Action(Content):
    """占位：Action 与 Tool 类似，但语义上是"动作组件"。
    
    kernel/llm 不关心 Action 的实现细节，只要求遵循 LLMUsable。
    """
    action: type
```

表示可执行的动作。

**特点：**
- 语义上表示"动作组件"，与工具不同
- 需要实现 `LLMUsable` 协议
- 框架不关心具体实现

**使用示例：**
```python
class MyAction:
    @classmethod
    def to_schema(cls) -> dict:
        return {...}

payload = LLMPayload(ROLE.USER, Action(MyAction))
```

---

## 多模态内容

### 创建多模态 payload

```python
# 方式 1：直接传递列表
payload = LLMPayload(
    ROLE.USER,
    [
        Text("Analyze this:"),
        Image("graph.png"),
        Text("What trends do you see?")
    ]
)

# 方式 2：逐个添加（框架会规范化）
content_list = [
    Text("Analyze this:"),
    Image("graph.png"),
]
payload = LLMPayload(ROLE.USER, content_list)
```

### 多模态使用场景

```python
# 图文分析
request.add_payload(LLMPayload(
    ROLE.USER,
    [
        Text("Please OCR this document:"),
        Image("document.pdf")
    ]
))

# 多图比较
request.add_payload(LLMPayload(
    ROLE.USER,
    [
        Text("Compare these two charts:"),
        Image("chart_a.png"),
        Image("chart_b.png")
    ]
))

# 混合分析
request.add_payload(LLMPayload(
    ROLE.USER,
    [
        Text("Q: What is shown here?"),
        Image("image.jpg"),
        Text("A: I see..."),  # 前文本内容
        Text("Now, what can we infer?")
    ]
))
```

---

## 内容验证

### 文件路径验证

```python
from pathlib import Path

# Image 会验证文件存在性
image = Image("nonexistent.jpg")
# 在转换为 data URL 时会抛出 FileNotFoundError
```

**最佳实践：**
```python
from pathlib import Path

def safe_add_image(request, file_path: str):
    """安全地添加图像"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"图像文件不存在: {file_path}")
    if not path.is_file():
        raise ValueError(f"路径不是文件: {file_path}")
    
    request.add_payload(LLMPayload(ROLE.USER, Image(file_path)))
```

---

## 常见用法

### 用法 1：纯文本对话

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are helpful.")))
request.add_payload(LLMPayload(ROLE.USER, Text("What is AI?")))
response = await request.send()
```

### 用法 2：图像识别

```python
request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(
    ROLE.USER,
    [
        Text("Identify the objects in this image:"),
        Image("objects.jpg")
    ]
))
response = await request.send()
```

### 用法 3：文档分析

```python
request = LLMRequest(model_set=models)

# 提供上下文
request.add_payload(LLMPayload(
    ROLE.SYSTEM,
    Text("You are an expert document analyzer.")
))

# 上传文档
request.add_payload(LLMPayload(
    ROLE.USER,
    [
        Text("Summarize this document:"),
        Image("report.pdf")
    ]
))

response = await request.send()
message = await response
print(message)
```

---

## 内部实现细节

### 内容规范化

在 `LLMPayload` 中，单个 `Content` 会被转换为 `list[Content]`：

```python
def _normalize_content(content: Content | list[Content]) -> list[Content]:
    if isinstance(content, list):
        return content
    return [content]
```

### 提供商特定处理

在 `model_client/` 中，内容会被转换为提供商特定格式：

```python
# OpenAI 格式转换
for part in payload.content:
    if isinstance(part, Text):
        parts.append({"type": "text", "text": part.text})
    elif isinstance(part, Image):
        url = _image_to_data_url(part.value)
        parts.append({"type": "image_url", "image_url": {"url": url}})
```

---

## 最佳实践

### 1. 始终检查文件存在性

```python
import os

def add_image_safely(request, path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Image not found: {path}")
    request.add_payload(LLMPayload(ROLE.USER, Image(path)))
```

### 2. 使用相对路径

```python
from pathlib import Path

base_dir = Path(__file__).parent
request.add_payload(LLMPayload(
    ROLE.USER,
    Image(str(base_dir / "images" / "photo.jpg"))
))
```

### 3. 提供清晰的上下文

```python
# 好的做法：先描述图像
request.add_payload(LLMPayload(
    ROLE.USER,
    [
        Text("This is a screenshot of a web application. Please identify any UI issues."),
        Image("screenshot.png")
    ]
))

# 不好的做法：仅提供图像
request.add_payload(LLMPayload(ROLE.USER, Image("screenshot.png")))
```

### 4. 合理安排内容顺序

```python
# 推荐：问题在前，资料在后
payload = LLMPayload(
    ROLE.USER,
    [
        Text("What is the main topic of this document?"),
        Image("document.pdf"),
    ]
)

# 也可以：资料在前，问题在后
payload = LLMPayload(
    ROLE.USER,
    [
        Image("document.pdf"),
        Text("What is the main topic?")
    ]
)
```

---

## 常见问题

### Q: 能否同时使用多个 Image？

A: 可以。某些提供商支持多图分析：
```python
payload = LLMPayload(
    ROLE.USER,
    [
        Image("image1.jpg"),
        Image("image2.jpg"),
        Text("Compare these images.")
    ]
)
```

### Q: 图像大小有限制吗？

A: 取决于提供商。OpenAI 对图像大小有限制（通常 20MB）。建议压缩或调整分辨率。

### Q: 支持哪些图像格式？

A: 通常支持 JPEG、PNG、GIF、WebP 等常见格式。具体取决于提供商的支持。

### Q: 能否使用 URL 直接引用网络图像？

A: 当前实现不直接支持网络 URL。建议下载后使用本地路径或 data URL。

---

## 相关文档

- [Payload 模块](./payload.md) - 消息结构
- [Tooling 模块](./tooling.md) - 工具系统
- [Request 模块](../request.md) - 请求发送

