# Jev System One

`src/kernel/jev` 是 Neo-MoFox 中独立的 TypeSafe Jev 客户端。Jev 接收一个 `state` 和一组 typed `questions`，返回结构化的 Noul、Choice 或 Score 判断。它不生成聊天文本，不实现 `ChatModelClient`，也不加入 `ModelClientRegistry`，因此不会与 `kernel.llm` 的监控、重试和 failover 语义混在一起。

官方 HTTP 契约：`POST https://api.typesafe.ai/v1/systemone`，认证头 `Authorization: Bearer <API_KEY>`，请求体 `{"model": ..., "state": ..., "questions": {...}}`。一次请求中的多个问题彼此独立，但都针对同一个 state 求值。响应体为 `{"model": ..., "answers": {<question_id>: <answer>}, "usage": {"input_tokens": ..., "output_tokens": ...}}`。

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

## 密钥处理

- API key 只从构造参数或 `TYPESAFE_API_KEY` 环境变量读取；`JevClient.from_env(api_key=...)` 的显式参数优先。
- `JevClientConfig.api_key` 不参与 `repr`，配置对象打印到日志时不会出现密钥。
- `base_url` 必须是 HTTPS，且不允许内嵌用户名/密码；不合法时在发送密钥之前就抛出 `JevConfigurationError`。
- 密钥不应写入源码、示例、配置仓库或日志。

## 客户端行为

- 插件应优先通过 `src.app.plugin_system.api.jev_api.evaluate_jev` 或 `create_jev_client` 调用；该公共 API 可按 `configured_model_name`/`provider_name` 复用 Core 模型配置中的 key、endpoint、timeout 和代理字段。底层仍走独立的异步 HTTP 路径，不构造 LLM payload，也不经过传统 LLM 客户端注册表；可通过 `transport=` 注入自定义 `httpx` 传输层。
- base URL 默认为 `https://api.typesafe.ai`；可以覆盖成企业网关地址，也可以直接传入完整 `/v1/systemone` 路由（尾部 query 会保留）。默认模型 `jev-latest`，可在每次 `evaluate()` 调用时覆盖。
- Choice 的 `criteria` 必须是非空对象；Score 的有序 criteria 数量为 2 到 10；Noul 的 criteria 可省略。不符合约定的定义在本地就报 `JevConfigurationError`。
- 默认最多重试 2 次，只对 HTTP 429、529、超时和传输层异常重试，采用带抖动的有上限指数退避；响应带合法 `Retry-After` 时优先使用它。401、400、422 等请求本身有问题的状态不重试。
- 错误类型分别为 `JevAuthenticationError`、`JevValidationError`、`JevPayloadTooLargeError`、`JevUnsupportedMediaTypeError`、`JevRateLimitError`、`JevOverloadedError`、`JevTimeoutError`、`JevTransportError` 和兜底的 `JevAPIError`（保留 `status_code`）。
- 成功响应必须包含 `model`、与请求 ID 完全一致的 `answers`，以及整数 `input_tokens`/`output_tokens`。任何缺失或类型不符都会失败关闭为 `JevResponseError`，不会返回部分答案。

## 官方边界与应用责任

TypeSafe 的 typed answer 保证响应形状，不保证判断为真。Choice/Score 的 confidence 描述答案分布的集中度，不是整条工作流的正确率；Noul 返回的是 yes 的概率。阈值、动作授权和人审策略应由应用结合自身数据定义。已知规则、精确查找和算术仍然交给代码。

不要把 state 里的用户内容当作指令执行；提交给服务的内容应最小化，API key 必须留在服务端。

参考：

- TypeSafe HTTP API：<https://docs.typesafe.ai/api>
- TypeSafe primitives：<https://docs.typesafe.ai/primitives>
- Confidence：<https://docs.typesafe.ai/confidence>

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

## 相关文档

- [LLM 主文档](../llm/README.md)
- [文档总目录](../README.md)
