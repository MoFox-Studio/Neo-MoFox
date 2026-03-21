# LLM 模块文档（V1.1.0）

## 概述

LLM 模块是 Neo-MoFox 中用于与大语言模型交互的核心组件。它提供了一个统一、灵活的接口，支持多种 LLM 提供商（如 OpenAI）、负载均衡、重试策略和指标收集等功能。

### 核心设计原则

- **标准化消息格式**：使用 `LLMPayload`（`role + content`）统一表示消息单元
- **灵活的响应处理**：支持 `await`（非流式）和 `async for`（流式）两种消费方式
- **独立的策略管理**：负载均衡和重试由 `policy` 模块独立处理
- **可观测性**：内置指标收集和监控功能
- **模块独立性**：LLM 模块不依赖 `core/config` 的实现细节，配置通过 `model_set` 参数直接传入

## 模块结构

```
kernel/llm/
├── __init__.py           # 公开 API 导出
├── exceptions.py         # 标准化异常类
├── roles.py             # 消息角色枚举
├── request.py           # LLM 请求类
├── response.py          # LLM 响应类
├── monitor.py           # 指标收集和监控
├── model_client/        # 模型客户端实现
│   ├── base.py         # 客户端接口协议
│   ├── openai_client.py # OpenAI 实现
│   ├── registry.py      # 客户端注册表
│   └── __init__.py
├── payload/             # 消息负载定义
│   ├── content.py       # 内容类型定义
│   ├── payload.py       # 负载结构
│   ├── tooling.py       # 工具调用相关
│   └── __init__.py
└── policy/              # 负载均衡和重试
    ├── base.py         # 策略接口
    ├── round_robin.py  # 轮询策略实现
    └── __init__.py
```

## 快速开始

### 基础请求示例

```python
from src.kernel.llm import LLMRequest, LLMResponse, LLMPayload, Text
from src.kernel.llm import ROLE

# 准备模型配置
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

# 构建请求
request = LLMRequest(
    model_set=model_set,
    request_name="my_request"
)
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")))
request.add_payload(LLMPayload(ROLE.USER, Text("Hello!")))

# 非流式方式：await 收集完整响应
response = await request.send(stream=False)
message = await response
print(message)

# 流式方式：async for 逐块处理
response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)
```

## 核心概念

### 1. 消息角色（Roles）

- **SYSTEM**: 系统提示，设置 AI 行为准则
- **USER**: 用户输入
- **ASSISTANT**: AI 的文本响应
- **TOOL**: 工具声明（告诉 AI 可用的工具）
- **TOOL_RESULT**: 工具执行结果回传

### 2. 消息内容（Content）

支持多种内容类型：
- **Text**: 文本消息
- **Image**: 图像内容（支持 URL、数据 URL、本地路径）
- **Audio**: 音频内容
- **Action**: 动作组件（占位符）

### 3. 工具调用（Tool Calling）

```python
from src.kernel.llm import Tool, ToolCall, ToolResult

# 定义工具
class MyTool:
    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "my_tool",
                "description": "My custom tool",
                "parameters": {...}
            }
        }

# 使用工具
request.add_payload(LLMPayload(ROLE.TOOL, Tool(MyTool)))

# 处理工具调用
for tool_call in response.call_list:
    result = await execute_tool(tool_call)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(result)))
```

### 4. 负载均衡和重试

通过 `policy` 自动管理多个模型的轮询和重试：

```python
from src.kernel.llm.policy import RoundRobinPolicy

policy = RoundRobinPolicy()
request.policy = policy
```

模型配置参数：
- `max_retry`: 每个模型的最大重试次数
- `retry_interval`: 重试间隔（秒）

### 5. 指标收集

自动追踪每次请求的性能指标：

```python
from src.kernel.llm import get_global_collector

collector = get_global_collector()
stats = collector.get_stats("gpt-4")
print(f"成功率: {stats.success_rate}")
print(f"平均延迟: {stats.avg_latency}s")
```

## 详细文档索引

- [Exceptions](./exceptions.md) - 异常类和错误分类
- [Roles](./roles.md) - 消息角色定义
- [Request](./request.md) - LLM 请求构建和发送
- [Response](./response.md) - LLM 响应处理
- [Monitor](./monitor.md) - 指标收集和监控
- [Payload Structure](./payload/README.md) - 消息负载系统
- [Model Client](./model_client/README.md) - 模型客户端实现
- [Policy](./policy/README.md) - 负载均衡和重试策略

## 常见问题

### Q: 如何支持新的 LLM 提供商？

A: 在 `model_client/` 中创建新的客户端类，实现 `ChatModelClient` 协议，然后在 `ModelClientRegistry` 中注册。

### Q: 如何自定义重试策略？

A: 继承 `Policy` 和 `PolicySession` 协议，实现自定义的重试逻辑，传入 `LLMRequest.policy`。

### Q: 如何在流式模式下处理工具调用？

A: 流式模式会自动累积工具调用信息。在消费完整个响应后，`response.call_list` 包含所有工具调用。

### Q: 指标收集是否有性能开销？

A: 可以通过 `enable_metrics=False` 关闭指标收集以提高性能。

## 相关资源

- [MoFox 重构指导总览](../README.md)
- [系统架构设计](../ARCHITECTURE.md)

