# LLM 模块文档（V2.0.0）

## 概述

LLM 模块是 Neo-MoFox 中用于与大语言模型交互的核心组件。它提供了统一、灵活的接口，支持多种 LLM 提供商（OpenAI、Anthropic 等）、多种请求类型（对话补全、Embedding、Rerank）、负载均衡、重试策略、上下文管理、流式推理内容（reasoning）解析以及多级指标收集等功能。

### 核心设计原则

- **标准化消息格式**：使用 `LLMPayload`（`role + content`）统一表示消息单元
- **灵活的响应处理**：支持 `await`（非流式）和 `async for`（流式）两种消费方式
- **多请求类型**：统一支持对话补全（Completions）、Embedding、Rerank 三种请求
- **独立的策略管理**：负载均衡和重试由 `policy` 模块独立处理，默认使用 `LoadBalancedPolicy`
- **上下文感知**：内建上下文预算管理、压缩与裁剪、对话结构校验
- **可观测性**：内置内存级指标收集（`MetricsCollector`）与持久化统计（`LLMStatsCollector`）
- **模块独立性**：LLM 模块不依赖 `core/config` 的实现细节，配置通过 `model_set` 参数直接传入

## 模块结构

```
kernel/llm/
├── __init__.py              # 公开 API 导出
├── types.py                 # 类型定义（ModelEntry, ModelSet, RequestType）
├── exceptions.py            # 标准化异常类 + classify_exception
├── roles.py                 # 消息角色枚举
├── request.py               # LLMRequest 门面 + 模型校验
├── request_execution.py     # 请求执行主流程（生命周期管理）
├── request_inspector.py     # 请求调试与检查工具
├── response.py              # LLMResponse 包装层
├── stream_state.py          # 流式状态归并（LLMStreamReducer）
├── tool_call_compat.py      # 工具调用兼容解析
├── token_counter.py         # Token 计数工具
├── context.py               # LLMContextManager 门面
├── context_budget.py        # 上下文预算与压缩
├── context_structure.py     # 上下文结构校验
├── monitor.py               # 内存级指标收集（MetricsCollector）
├── observation.py           # 请求观测与遥测分发
├── embedding_request.py     # Embedding 请求
├── embedding_response.py    # Embedding 响应
├── rerank_request.py        # Rerank 请求
├── rerank_response.py       # Rerank 响应
├── model_client/            # 模型客户端实现
│   ├── base.py              # 客户端接口协议（Chat/Embedding/Rerank）
│   ├── openai_client.py     # OpenAI 实现
│   ├── anthropic_client.py  # Anthropic 实现
│   ├── registry.py          # 客户端注册表
│   ├── shared.py            # 共享工具
│   └── __init__.py
├── payload/                 # 消息负载定义
│   ├── content.py           # 内容类型定义（Text/Image/Audio/Video/File/ReasoningText）
│   ├── payload.py           # LLMPayload 结构
│   ├── tooling.py           # 工具调用相关（LLMUsable/ToolCall/ToolResult/ToolRegistry）
│   └── __init__.py
├── policy/                  # 负载均衡和重试
│   ├── base.py              # 策略接口（Policy/PolicySession/ModelStep）
│   ├── round_robin.py       # 轮询策略
│   ├── load_balanced.py     # 负载均衡策略（默认）
│   └── __init__.py
└── stats/                   # 持久化统计
    ├── collector.py         # LLMStatsCollector
    ├── config.py            # LLMStatsConfig
    ├── database.py          # SQLite 存储
    └── __init__.py
```

## 快速开始

### 基础请求示例

```python
from src.kernel.llm import LLMRequest, LLMResponse, LLMPayload, Text
from src.kernel.llm import ROLE

# 准备模型配置（完整字段参见 ModelEntry 定义）
model_set = [
    {
        "api_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": "gpt-4",
        "api_key": "sk-...",
        "client_type": "openai",
        "max_retry": 3,
        "timeout": 30.0,
        "retry_interval": 1.0,
        "price_in": 0.03,
        "cache_hit_price_in": 0.015,
        "price_out": 0.06,
        "temperature": 0.7,
        "max_tokens": 4096,
        "max_context": 8192,
        "tool_call_compat": False,
        "extra_params": {},
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
- **ReasoningText**: 推理过程文本（如 DeepSeek-R1、OpenAI o1 等模型的思考链）
- **Image**: 图像内容（支持 URL、数据 URL、本地路径）
- **Audio**: 音频内容
- **Video**: 视频内容
- **File**: 文件内容

### 3. 工具调用（Tool Calling）

工具系统基于 `LLMUsable` 协议，支持 `ToolRegistry` 集中管理：

```python
from src.kernel.llm import ToolCall, ToolResult, LLMUsable, ToolRegistry

# 实现 LLMUsable 协议
class MyTool(LLMUsable):
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

# 添加到 payload
request.add_payload(LLMPayload(ROLE.TOOL, MyTool))  # 类直接传入

# 处理工具调用
for tool_call in response.call_list or []:
    result = await execute_tool(tool_call)
    request.add_payload(LLMPayload(ROLE.TOOL_RESULT, ToolResult(result)))
```

### 4. 上下文管理（ContextManager）

`LLMContextManager` 统一管理对话上下文的生命周期：

- **结构校验**：确保 SYSTEM→USER↔ASSISTANT 对话结构合法，校验 tool-call/tool-result 配对
- **预算控制**：根据模型 `max_context` 自动裁剪和压缩历史 payload
- **Reminder 注入**：支持按 bucket/name 注册并注入运行时提醒文本
- **自定义压缩**：通过 `AsyncContextCompressionHandler` 回调实现自定义压缩逻辑

```python
from src.kernel.llm import LLMContextManager, ReminderSourceSpec

context_mgr = LLMContextManager(
    context_compression_handler=my_compression_handler,
    reminder_sources=[ReminderSourceSpec(bucket="system", names=("tips",))]
)
request = LLMRequest(model_set=models, context_manager=context_mgr)
```

### 5. Embedding 请求

```python
from src.kernel.llm import EmbeddingRequest

req = EmbeddingRequest(model_set=embedding_models, request_name="doc_embed")
req.add_input("Hello world").add_input("Another text")
response = await req.send()
# response.embeddings: list[list[float]]
```

### 6. Rerank 请求

```python
from src.kernel.llm import RerankRequest

req = RerankRequest(model_set=rerank_models, request_name="doc_rerank")
req.set_query("What is AI?")
req.add_document("AI is artificial intelligence...")
req.add_document("Machine learning is...")
response = await req.send()
# response.results: list[RerankItem]  按相关性排序
for item in response.results:
    print(f"score={item.relevance_score}, doc={item.document}")
```

### 7. 负载均衡和重试

通过 `policy` 自动管理多个模型的负载均衡和重试。默认使用 `LoadBalancedPolicy`：

```python
from src.kernel.llm.policy import RoundRobinPolicy, LoadBalancedPolicy, create_policy

# 使用命名策略
request.policy = create_policy("round_robin")

# 或直接实例化
request.policy = LoadBalancedPolicy()
```

模型配置参数：
- `max_retry`: 每个模型的最大重试次数
- `retry_interval`: 重试间隔（秒）
- `timeout`: 请求超时（秒）

### 8. 指标收集

框架提供两级指标系统：

**内存级（MetricsCollector）**：实时追踪每次请求的性能指标：

```python
from src.kernel.llm import get_global_collector

collector = get_global_collector()
stats = collector.get_stats("gpt-4")
print(f"成功率: {stats['success_rate']}")
print(f"平均延迟: {stats['avg_latency']}s")
```

**持久化级（LLMStatsCollector）**：基于 SQLite 的持久化统计，支持按模型/请求名聚合查询：

```python
from src.kernel.llm import init_llm_stats, get_llm_stats_collector

await init_llm_stats(db_path="data/llm_stats/llm_stats.db")
collector = get_llm_stats_collector()
summary = await collector.get_summary()
by_model = await collector.get_by_model()
cache_rate = await collector.get_cache_hit_rate()
```

## 详细文档索引

- [Exceptions](./exceptions.md) - 异常类和错误分类（含 `classify_exception`）
- [Roles](./roles.md) - 消息角色定义
- [Request](./request.md) - LLM 请求构建和发送（含 Embedding/Rerank）
- [Response](./response.md) - LLM 响应处理（含 reasoning 与 tool_call_compat）
- [Monitor](./monitor.md) - 内存级指标收集和监控
- [Payload Structure](./payload/README.md) - 消息负载系统（含 ReasoningText/Video/File）
- [Model Client](./model_client/README.md) - 模型客户端实现（OpenAI + Anthropic）
- [Policy](./policy/README.md) - 负载均衡和重试策略（RoundRobin + LoadBalanced）

## 常见问题

### Q: 如何支持新的 LLM 提供商？

A: 在 `model_client/` 中创建新的客户端类，实现 `ChatModelClient` 协议（及可选的 `EmbeddingModelClient`、`RerankModelClient`），然后在 `ModelClientRegistry` 中注册。

### Q: 如何自定义重试策略？

A: 实现 `Policy` 和 `PolicySession` 协议，或通过 `set_default_policy_factory` 注入自定义工厂。

### Q: 如何在流式模式下处理工具调用和推理内容？

A: 流式模式通过 `LLMStreamReducer` 自动累积工具调用和 reasoning 片段。消费完响应后，`response.call_list` 和 `response.reasoning_parts` 包含完整信息。

### Q: 如何启用持久化 LLM 统计？

A: 在 kernel 启动时调用 `await init_llm_stats(db_path="data/llm_stats/llm_stats.db")`，之后通过 `get_llm_stats_collector()` 获取收集器实例。

### Q: 上下文管理的自动裁剪何时触发？

A: 当 token 计数超过 `model.max_context * context_reserve_ratio`（默认为 0.95）时触发裁剪；可通过 `extra_params.context_reserve_ratio` 和 `extra_params.context_reserve_tokens` 调整。

### Q: 指标收集是否有性能开销？

A: 内存级指标可以通过 `enable_metrics=False` 关闭。持久化统计仅在显式调用 `init_llm_stats` 后生效。

## 相关资源

- [MoFox 重构指导总览](../README.md)
- [系统架构设计](../ARCHITECTURE.md)

