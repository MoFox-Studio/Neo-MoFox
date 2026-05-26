# LLM 模块文档（V1.2.0）

## 概述

LLM 模块是 Neo-MoFox 中用于与大语言模型交互的核心组件。它提供了一个统一、灵活的接口，支持多种 LLM 提供商（OpenAI、Anthropic）、负载均衡、重试策略、上下文管理、文本嵌入、文档重排序和指标收集等功能。

### 核心设计原则

- **标准化消息格式**：使用 LLMPayload（role + content）统一表示消息单元
- **灵活的响应处理**：支持 wait（非流式）和 sync for（流式）两种消费方式
- **独立的策略管理**：负载均衡和重试由 policy 模块独立处理
- **全面的请求能力**：支持 Chat Completions、Embeddings、Rerank 三种请求类型
- **智能上下文管理**：内置 payload 裁剪、压缩和结构校验
- **可观测性**：内置指标收集和监控功能
- **模块独立性**：LLM 模块不依赖 core/config 的实现细节，配置通过 model_set 参数直接传入

## 模块结构

`
kernel/llm/
├── __init__.py              # 公开 API 导出
├── exceptions.py            # 标准化异常类
├── roles.py                 # 消息角色枚举
├── types.py                 # 类型定义（ModelEntry、ModelSet、RequestType）
├── context.py               # 上下文管理器（裁剪、压缩、校验）
├── request.py               # LLM 聊天请求类
├── response.py              # LLM 响应类
├── embedding_request.py     # Embedding 请求类
├── embedding_response.py    # Embedding 响应类
├── rerank_request.py        # Rerank 请求类
├── rerank_response.py       # Rerank 响应类
├── token_counter.py         # Token 计数工具
├── tool_call_compat.py      # 工具调用兼容性支持
├── request_inspector.py     # 请求体调试 WebUI
├── monitor.py               # 指标收集和监控
├── model_client/            # 模型客户端实现
│   ├── base.py              # 客户端接口与 StreamEvent
│   ├── openai_client.py     # OpenAI 实现
│   ├── anthropic_client.py  # Anthropic 实现
│   ├── registry.py          # 客户端注册表
│   └── __init__.py
├── payload/                 # 消息负载定义
│   ├── content.py           # 内容类型定义
│   ├── payload.py           # 负载结构
│   ├── tooling.py           # 工具调用相关
│   └── __init__.py
└── policy/                  # 负载均衡和重试
    ├── base.py              # 策略接口
    ├── round_robin.py       # 轮询策略
    ├── load_balanced.py     # 动态负载均衡策略
    └── __init__.py
`

## 快速开始

### Chat Completions 请求

`python
from src.kernel.llm import LLMRequest, LLMPayload, Text, ROLE

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
request = LLMRequest(model_set=model_set, request_name="my_request")
request.add_payload(LLMPayload(ROLE.SYSTEM, Text("You are a helpful assistant.")))
request.add_payload(LLMPayload(ROLE.USER, Text("Hello!")))

# 非流式方式
response = await request.send(stream=False)
message = await response
print(message)

# 流式方式
response = await request.send(stream=True)
async for chunk in response:
    print(chunk, end="", flush=True)
`

### Embedding 请求

`python
from src.kernel.llm import EmbeddingRequest

request = EmbeddingRequest(model_set=model_set)
request.add_input("Hello world")
request.add_input("What is AI?")

response = await request.send()
print(f"向量数量: {len(response.embeddings)}")
print(f"向量维度: {len(response.embeddings[0])}")
`

### Rerank 请求

`python
from src.kernel.llm import RerankRequest

request = RerankRequest(model_set=model_set)
request.set_query("Python 编程")
request.add_document({"title": "Python 教程", "content": "..."})
request.add_document({"title": "Java 指南", "content": "..."})

response = await request.send()
for item in response.results:
    print(f"{item.document['title']}: {item.score:.4f}")
`

## 核心概念

### 1. 请求类型（Request Types）

| 类型 | 枚举值 | 说明 |
|---|---|---|
| Chat Completions | COMPLETIONS | 对话补全，最常用的 LLM 交互方式 |
| Embeddings | EMBEDDINGS | 文本向量嵌入，用于语义搜索、聚类等 |
| Rerank | RERANK | 文档重排序，对初步检索结果精细排序 |

### 2. 消息角色（Roles）

- **SYSTEM**: 系统提示，设置 AI 行为准则
- **USER**: 用户输入
- **ASSISTANT**: AI 的文本响应
- **TOOL**: 工具声明（告诉 AI 可用的工具）
- **TOOL_RESULT**: 工具执行结果回传

### 3. 消息内容（Content）

支持多种内容类型：
- **Text**: 文本消息
- **Image**: 图像内容（支持 URL、数据 URL、本地路径）
- **Audio**: 音频内容
- **ReasoningText**: 推理/思考内容（Anthropic thinking）
- **Video**: 视频内容（占位）

### 4. 上下文管理（Context Manager）

LLMContextManager 提供智能的对话上下文管理：
- payload 写入接管和结构校验
- 基于 payload 数量和 token 的自动裁剪
- 对话组压缩（compression hook）
- Reminder 延迟注入

`python
from src.kernel.llm import LLMContextManager

ctx = LLMContextManager(max_payloads=40)
request = LLMRequest(model_set=model_set, context_manager=ctx)
`

### 5. Token 计数（Token Counter）

`python
from src.kernel.llm import count_payload_tokens, count_text_tokens

tokens = count_payload_tokens(payloads, model_identifier="gpt-4")
text_tokens = count_text_tokens("Hello world", model_identifier="gpt-4")
`

### 6. 工具调用兼容（Tool Call Compat）

为不支持原生 tool call 的模型提供 prompt 注入兼容方案：

`python
from src.kernel.llm.tool_call_compat import (
    build_tool_call_compat_prompt,
    parse_tool_call_compat_response,
)
`

### 7. 负载均衡和重试

通过 policy 自动管理多个模型的轮询和重试。支持两种策略：

- **RoundRobinPolicy**: 简单轮询策略
- **LoadBalancedPolicy**: 动态负载均衡，综合 token 用量、延迟和失败惩罚

`python
from src.kernel.llm.policy import LoadBalancedPolicy

request.policy = LoadBalancedPolicy()
`

### 8. 指标收集

自动追踪每次请求的性能指标：

`python
from src.kernel.llm import get_global_collector

collector = get_global_collector()
stats = collector.get_stats("gpt-4")
print(f"成功率: {stats.success_rate}")
print(f"平均延迟: {stats.avg_latency}s")
`

## 详细文档索引

- [Types](./types.md) - 类型定义和别名
- [Exceptions](./exceptions.md) - 异常类和错误分类
- [Roles](./roles.md) - 消息角色定义
- [Request](./request.md) - LLM 聊天请求构建和发送
- [Response](./response.md) - LLM 响应处理
- [Context](./context.md) - 上下文管理和裁剪
- [Embedding](./embedding.md) - 向量嵌入请求
- [Rerank](./rerank.md) - 文档重排序请求
- [Token Counter](./token_counter.md) - Token 计数工具
- [Tool Call Compat](./tool_call_compat.md) - 工具调用兼容性
- [Monitor](./monitor.md) - 指标收集和监控
- [Payload Structure](./payload/README.md) - 消息负载系统
- [Model Client](./model_client/README.md) - 模型客户端实现
- [Policy](./policy/README.md) - 负载均衡和重试策略

## 常见问题

### Q: 如何支持新的 LLM 提供商？

A: 在 model_client/ 中创建新的客户端类，实现 ChatModelClient 协议，然后在 ModelClientRegistry 中注册。

### Q: 如何自定义重试策略？

A: 继承 Policy 和 PolicySession 协议，实现自定义的重试逻辑，传入 LLMRequest.policy。

### Q: 如何在流式模式下处理工具调用？

A: 流式模式会自动累积工具调用信息。在消费完整个响应后，response.call_list 包含所有工具调用。

### Q: 指标收集是否有性能开销？

A: 可以通过 enable_metrics=False 关闭指标收集以提高性能。

### Q: Chat Completions、Embeddings 和 Rerank 的区别？

A: LLMRequest 用于对话和文本生成，EmbeddingRequest 用于获取文本向量，RerankRequest 用于对文档按相关性排序。

### Q: 上下文管理器如何控制对话长度？

A: 通过 max_payloads 限制 payload 数量，或通过 compression_hook 实现对话压缩。

## 相关资源

- [MoFox 重构指导总览](../README.md)
- [系统架构设计](../ARCHITECTURE.md)
