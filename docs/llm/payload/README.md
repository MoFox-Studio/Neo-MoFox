# Payload 模块

## 概述

`payload/` 子模块定义了 LLM 消息系统中的核心数据结构。它包括：
- **Content 类型**：各种消息内容的表示（文本、图像、音频等）
- **LLMPayload**：消息的标准格式（角色 + 内容）
- **工具调用系统**：工具声明、调用和结果的处理

## 模块结构

```
payload/
├── content.py      # 内容类型定义
├── payload.py      # LLMPayload 结构
├── tooling.py      # 工具调用系统
└── __init__.py     # 公开 API
```

## 快速开始

```python
from kernel.llm import LLMPayload, Text, Image, ROLE, Tool, ToolCall, ToolResult

# 创建文本 payload
text_payload = LLMPayload(ROLE.USER, Text("Hello!"))

# 创建多模态 payload
image_payload = LLMPayload(
    ROLE.USER,
    [
        Text("What's in this image?"),
        Image("path/to/image.jpg")
    ]
)

# 使用工具
tool_payload = LLMPayload(ROLE.TOOL, Tool(MyTool))

# 工具结果
result_payload = LLMPayload(ROLE.TOOL_RESULT, ToolResult(value="result", call_id="123"))
```

## 详细模块文档

- [Content 模块](./content.md) - 内容类型详解
- [Payload 模块](./payload.md) - 消息结构
- [Tooling 模块](./tooling.md) - 工具系统

## 相关文档

- [LLM 主文档](../README.md)
- [Roles 模块](../roles.md)
- [Request 模块](../request.md)
- [Response 模块](../response.md)
