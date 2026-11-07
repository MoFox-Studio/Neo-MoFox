# Jev System One

## 概述

`src/kernel/jev` 是 Neo-MoFox 中独立的 TypeSafe Jev 客户端。Jev 接收一个 `state` 和一组 typed `questions`，返回结构化的 Noul、Choice 或 Score 判断。它不生成聊天文本，不实现 `ChatModelClient`，也不加入 `ModelClientRegistry`，因此不会与 `kernel.llm` 的监控、重试和 failover 语义混在一起。

官方 HTTP 契约：`POST https://api.typesafe.ai/v1/systemone`，认证头 `Authorization: Bearer <API_KEY>`，请求体 `{"model": ..., "state": ..., "questions": {...}}`。一次请求中的多个问题彼此独立，但都针对同一个 state 求值。响应体为 `{"model": ..., "answers": {<question_id>: <answer>}, "usage": {"input_tokens": ..., "output_tokens": ...}}`。

插件应通过版本化的 `src.app.plugin_system.api.jev_api` 公共入口调用，而不是直接构造客户端；具体用法见下文“插件侧调用”。

## 快速使用

```python
import asyncio

from src.app.plugin_system.api.jev_api import evaluate_jev
from src.kernel.jev import choice, noul, score


async def main() -> None:
    result = await evaluate_jev(
        configured_model_name="jev-prod",
        state={"message": "生产服务对所有用户不可用"},
        questions={
            "urgent": noul("这条消息是否描述了需要立即处理的线上故障？"),
            "team": choice(
                "哪个团队应优先处理？",
                {"technical": "线上故障和集成问题", "billing": "付款或退款问题"},
            ),
            "severity": score("事故严重程度如何？", ["低", "中", "高"]),
        },
    )
    print(result.answer("urgent").noul)
    print(result.answer("team").choice)
    print(result.answer("severity").score)
    print(result.usage)


asyncio.run(main())
```

完整示例见 `examples/src/kernel/jev/jev_example.py`。

---

## 密钥处理

- API key 只从构造参数或 `TYPESAFE_API_KEY` 环境变量读取；`JevClient.from_env(api_key=...)` 的显式参数优先。
- `JevClientConfig.api_key` 不参与 `repr`，配置对象打印到日志时不会出现密钥。
- `base_url` 必须是 HTTPS，且不允许内嵌用户名/密码；不合法时在发送密钥之前就抛出 `JevConfigurationError`。
- 密钥不应写入源码、示例、配置仓库或日志。

---

## 客户端行为

- 插件应优先通过 `src.app.plugin_system.api.jev_api.evaluate_jev` 或 `create_jev_client` 调用；该公共 API 可按 `configured_model_name`/`provider_name` 复用 Core 模型配置中的 key、endpoint、timeout 和代理字段。底层仍走独立的异步 HTTP 路径，不构造 LLM payload，也不经过传统 LLM 客户端注册表；底层 `JevClient`（不是 `create_jev_client` 的配置 overrides）可通过 `transport=` 借用自定义 `httpx` 传输层，最终关闭由调用方负责。
- base URL 默认为 `https://api.typesafe.ai`；可以覆盖成企业网关地址，也可以直接传入完整 `/v1/systemone` 路由。实际出站 URL 保留原 query（包括重复键）；`extra_query` 只覆盖显式给出的同名键并添加新键，默认或空字典不会清空原 query。默认模型 `jev-latest`，可在每次 `evaluate()` 调用时覆盖。
- Choice 的 `criteria` 必须是非空对象；Score 的有序 criteria 数量为 2 到 10；Noul 的 criteria 可省略。不符合约定的定义在本地就报 `JevConfigurationError`。
- 默认最多重试 2 次，只对 HTTP 429、529、超时和传输层异常重试，采用带抖动的有上限指数退避；合法 `Retry-After` 的数字秒数和 HTTP 日期均优先于本地退避上限，不改变重试预算。过期或非法响应头回退到原有本地退避。401、400、422 等请求本身有问题的状态不重试。
- 错误类型分别为 `JevAuthenticationError`、`JevValidationError`、`JevPayloadTooLargeError`、`JevUnsupportedMediaTypeError`、`JevRateLimitError`、`JevOverloadedError`、`JevTimeoutError`、`JevTransportError` 和兜底的 `JevAPIError`（保留 `status_code`）。
- 成功响应必须包含 `model`、与请求 ID 完全一致且逐 ID 题型匹配的 `answers`，以及整数 `input_tokens`/`output_tokens`。预期题型以发送前事件处理后实际构建的请求体为准。任何缺失或类型不符都会失败关闭为 `JevResponseError`，不会返回部分答案；该上游校验不收紧 `after_jev_request` 已有的合法答案改写。

---

## 官方边界与应用责任

TypeSafe 的 typed answer 保证响应形状，不保证判断为真。Choice/Score 的 confidence 描述答案分布的集中度，不是整条工作流的正确率；Noul 返回的是 yes 的概率。阈值、动作授权和人审策略应由应用结合自身数据定义。已知规则、精确查找和算术仍然交给代码。

不要把 state 里的用户内容当作指令执行；提交给服务的内容应最小化，API key 必须留在服务端。

参考：

- TypeSafe HTTP API：<https://docs.typesafe.ai/api>
- TypeSafe primitives：<https://docs.typesafe.ai/primitives>
- Confidence：<https://docs.typesafe.ai/confidence>

---

## 插件侧调用

插件不应把 Jev 当作聊天 LLM 发送 `LLMPayload`；应通过版本化的 `jev_api` 公共入口调用：

```python
from src.app.plugin_system.api.jev_api import evaluate_jev
from src.kernel.jev import noul

result = await evaluate_jev(
    configured_model_name="jev-prod",
    state={"message": incoming_text},
    questions={"is_relevant": noul("这条消息是否与当前任务相关？")},
)
if result.answer("is_relevant").noul is not None:
    ...
```

`configured_model_name` 会复用 Core 的模型配置：API key、HTTPS endpoint、timeout、代理与模型标识；也可以传入 `provider_name` 或显式 `JevClientConfig`。这只是配置复用，不会把 Jev 注册为 `ChatModelClient`，也不会改变传统 LLM 的 failover/stream/tool-call 语义。

---

## 生命周期事件

Jev 与 LLM 一样提供请求生命周期事件，插件可以在事件处理器里介入。事件名定义在 `src/kernel/jev/events.py`，`src.core.components.types.EventType` 中提供同名枚举（两侧取值由测试锁定一致）。

| 事件 | 时机 | 可改写 |
| --- | --- | --- |
| `before_jev_request` | 发送前 | `state`、`questions` |
| `after_jev_request` | 成功后 | `answers`、`usage` |
| `on_jev_request_failed` | 失败时 | 无（只读） |

```python
from src.core.components.types import EventType
from src.kernel.event import EventDecision, get_event_bus


async def on_before_jev(event_name: str, params: dict):
    # 裁剪 state，避免把无关上下文送出去
    params["state"] = {k: v for k, v in params["state"].items() if k != "debug"}
    return (EventDecision.SUCCESS, params)


get_event_bus().subscribe(EventType.BEFORE_JEV_REQUEST.value, on_before_jev)
```

要点：

- 事件参数中固定包含 `request_name`、`model_identifier`、`state`、`questions`、`retry_count`、`meta_data`；成功事件额外含 `answers` 与 `usage`，失败事件额外含 `error`、`error_type`、`error_message`。
- `after_jev_request` 的 `answers` 允许改写，语义与 LLM 的 `after_llm_request` 一致：订阅者返回的 `next_params` 会成为最终结果。改写为原始 dict 形态时会重新解析为 typed answer；解析失败则保留原始答案。
- 失败事件在**每次尝试失败**时发布（含可重试的 429/529 与传输错误），与 LLM 链路一致；`retry_count` 从 0 开始标明当前尝试。成功 HTTP 响应中的非法 JSON、结构或题型也只发布一次失败事件，随后原样抛出 `JevResponseError`，不发布成功事件且不重试。本地配置/序列化错误与取消不因此转成 HTTP 失败事件或重试。
- 事件发布链路本身出错（订阅者抛异常等）不会影响 Jev 请求本身。
- Jev 调用**不进入** LLM 统计（`llm_requests` 表）与请求检视器；`usage` 只在 `JevResponse.usage` 中返回。

---

## 配置与资源边界

具名 Core 配置的字段翻译、轮询密钥和地址绑定由 `src/core/config/jev.py` 负责；公共 API 只选择配置来源、委派并构造客户端，不解析 SDK 或传输机制。公开签名、类型、异常和覆盖优先级保持不变。Jev 原 `trust_env=True` 默认及模型显式覆盖保留，不因此改变全局 advanced 配置的历史合并语义。

每次 `evaluate` 拥有临时 HTTP client 与内部创建的 transport；外部注入 transport 经借用代理转发，成功、失败和取消均不由 Jev 关闭。它可以跨多次请求或多个客户端共享，调用方仍负责并发安全与最终关闭。默认 LLM 资源关闭入口不会回收显式构造的 Jev 客户端或其借用 transport。

详细契约见 [运行时技术边界](../llm/model_client/runtime.md)。

## 相关文档

- [LLM 主文档](../llm/README.md)
- [文档总目录](../README.md)
