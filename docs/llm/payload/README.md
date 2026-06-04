# Payload 模块

## 概述

`payload/` 子模块定义了 LLM 消息系统中的核心数据结构。它包括：
- **Content 类型**：各种消息内容的表示（Text、ReasoningText、Image、Audio、Video、File）
- **LLMPayload**：消息的标准格式（角色 + 内容）
- **工具调用系统**：基于 `LLMUsable` 协议的工具声明、调用和结果处理（含 `ToolRegistry`）

## 模块结构

```
payload/
├── content.py      # 内容类型定义（Text/ReasoningText/Image/Audio/Video/File）
├── payload.py      # LLMPayload 结构
├── tooling.py      # 工具调用系统（LLMUsable/ToolCall/ToolResult/ToolRegistry）
└── __init__.py     # 公开 API
```

## 快速开始

```python
from src.kernel.llm import LLMPayload, Text, Image, ReasoningText
from src.kernel.llm import ROLE, ToolCall, ToolResult, LLMUsable

# 创建文本 payload
text_payload = LLMPayload(ROLE.USER, Text("Hello!"))

# 创建多模态 payload
image_payload = LLMPayload(
    ROLE.USER,
    [Text("What's in this image?"), Image("path/to/image.jpg")]
)

# 推理内容（reasoning）
reasoning_payload = LLMPayload(
    ROLE.ASSISTANT,
    [ReasoningText("Let me think..."), Text("The answer is...")]
)

# 使用 LLMUsable 协议（类直接传入，无需包装）
request.add_payload(LLMPayload(ROLE.TOOL, MyTool))

# 工具结果
result_payload = LLMPayload(
    ROLE.TOOL_RESULT,
    ToolResult(value="result", call_id="call_123", name="my_tool")
)
```

## 内容类型一览

| 类型 | 说明 | Python 类 |
|------|------|-----------|
| 文本 | 普通文本消息 | `Text` |
| 推理文本 | 模型思考链/推理过程 | `ReasoningText` |
| 图像 | 支持 URL / data URL / 本地路径 | `Image` |
| 音频 | 音频内容 | `Audio` |
| 视频 | 视频内容 | `Video` |
| 文件 | 文件内容 | `File` |

## 工具系统类型

| 类型 | 说明 |
|------|------|
| `LLMUsable` | 工具抽象基类，需实现 `to_schema()` |
| `ToolCall` | 模型发起的工具调用（含 id/name/args） |
| `ToolResult` | 工具执行结果（含 value/call_id/name） |
| `ToolRegistry` | 工具注册表，集中管理工具集合 |
| `LLMUsableExecution` | 工具执行记录 |
| `LLMUsableExecutionStatus` | 执行状态枚举 |

## 详细模块文档

- [Content 模块](./content.md) - 内容类型详解
- [Payload 模块](./payload.md) - 消息结构
- [Tooling 模块](./tooling.md) - 工具系统

## 相关文档

- [LLM 主文档](../README.md)
- [Roles 模块](../roles.md)
- [Request 模块](../request.md)
- [Response 模块](../response.md)

