# GeminiChatClient：原生 Developer API

本文描述当前工作区实现，不是设计计划的完成声明。依赖固定为 **`google-genai==2.33.0`**（不是旧的 `google-generativeai`），入口仍为 `src.kernel.llm.LLMRequest` / `LLMResponse`，插件继续使用现有 `llm_api`，没有独立 `gemini_api`。

## 实现与协议边界

- `gemini_client.py` 调用官方 SDK 的 `aio.models.generate_content` / `generate_content_stream`。SDK 负责认证、序列化、HTTP 和基础流解码；MoFox 的 `gemini_codec.py` 显式转换消息、工具声明、响应与统一 `StreamEvent`，不自行实现 REST/SSE 解析器。
- MoFox 经 `kernel.network` 创建自己的 `httpx.AsyncClient`，通过公开的 `types.HttpOptions(httpx_async_client=...)` 注入 SDK。超时、环境代理、IPv4、连接池与关闭由这一层管理；不使用 SDK chats 管理第二份历史。
- 显式设置 `vertexai=False`，只支持 API key 模式的 **Gemini Developer API**。`vertex_ai` 明确拒绝，不支持 Vertex 的 ADC/OAuth、project/location 或资源路径。
- 默认 registry 已装配 Gemini。推荐 `client_type="gemini"`；现有原生别名 `aiohttp_gemini`、`gemini_v2`、`google` 也可分发。Gemini 不会静默改走 OpenAI。OpenAI-compatible 网关需显式选择 `client_type="openai"`，不能与本适配器混用。
- SDK 内层 HTTP 尝试配置为 `HttpRetryOptions(attempts=1)`，外层重试由 MoFox policy 管理。**AFC（automatic function calling）与 `automatic_continuation` 均显式关闭**；只传工具 schema，不把 Python callable 交给 SDK，工具由 MoFox/调用方执行。

## 配置

以下是直接传给 kernel 的单个 `ModelEntry` 的关键字段；完整、安全取 key 的例子见 [gemini_example.py](../../../examples/src/kernel/llm/gemini_example.py)。示例不保证任何模型 ID、API 版本或生成参数对目标账户可用。

```python
model = {
    "api_provider": "GeminiNative",
    "client_type": "gemini",
    "api_key": api_key,                  # 环境变量或 getpass；不要提交密钥
    "base_url": "https://generativelanguage.googleapis.com",
    "model_identifier": model_id,        # 调用方提供已开通的单个模型 ID
    "timeout": 30.0,                    # MoFox 秒；SDK 配置转换为毫秒
    "max_retry": 1,
    "retry_interval": 1.0,
    "max_tokens": 512,                  # 映射 max_output_tokens，必须为正整数
    "temperature": 0.2,                 # 必须为有限数；模型是否接受需另验
    "tool_call_compat": False,          # 使用原生 function calling
    "extra_params": {
        "trust_env": False,
        "force_ipv4": False,
        "gemini": {
            "api_version": "v1beta",
            "generation_config": {},   # SDK snake_case 字段；按目标模型显式设置
        },
    },
}
```

### base_url 与 API 版本

`base_url` 配置 **HTTPS 服务根地址（host，可带网关前缀）**，`extra_params.gemini.api_version` 独立设置为 `v1beta`（默认）、`v1` 或 `v1alpha`。不要填写完整的 `.../models/...:generateContent` 操作 URL，也不要添加 query、fragment、用户名或密码。SDK 拼装版本与操作路径，例如：

- `{base_url}/{api_version}/models/{model}:generateContent`
- `{base_url}/{api_version}/models/{model}:streamGenerateContent?alt=sse`

旧配置末尾的 `/v1beta`、`/v1alpha`、`/v1` 会拆分为版本；显式非默认版本与后缀冲突会报错。当前默认 `v1beta` 兼作迁移默认值，不能据此认为所有显式冲突都可检测；新配置请始终使用无版本服务根。网关前缀是否被 SDK 按预期保留，需要该网关的出站协议测试，不能仅凭 URL 校验通过就认定兼容。

`model_identifier` 可带一次 `models/` 前缀，之后必须是单模型 ID；拒绝路径/操作注入和 Vertex 资源名。认证使用 SDK 的 API key 机制，禁止覆盖 `Authorization`、`x-goog-api-key`、`Host`、`Content-Type`；禁用自动重定向。

### 参数边界

- 仅在 `extra_params.gemini.generation_config` 下放 SDK 生成参数。未知字段及保留字段会报配置错误，不接受 OpenAI 风格的任意参数透传。
- `candidate_count` 固定为 1；`max_output_tokens`、`temperature` 必须用上面的 MoFox 字段设置。`tools`、`system_instruction`、`http_options`、AFC、自动续接和 continuation token 由适配器控制，不能覆盖。
- `extra_params.headers` 可加非保留请求头；非空 `query` / `body` 不支持。SDK 类型校验不等于目标模型能力校验，thinking 等参数必须另行确认。
- `force_ipv4=True` 会安装显式 transport，当前 httpx 下会绕过环境代理。需要环境代理时使用 `trust_env=True`、`force_ipv4=False`；不要声称两者可同时生效。

## 消息、媒体和工具映射

| MoFox 输入 | Gemini 编码 / 首期边界 |
| --- | --- |
| SYSTEM `Text` | `system_instruction`；SYSTEM 中非文本明确拒绝 |
| USER / ASSISTANT `Text` | `Content(role="user" / "model")` 的文本 Part |
| `Image` | 解码现有 base64 内容并用 Pillow 识别 MIME，编码 inline bytes |
| `LLMUsable.to_schema()` / ROLE.TOOL | 转 `FunctionDeclaration(parameters_json_schema=...)`；保留项目 schema 规范化与 reason 参数注入，冲突的同名声明拒绝 |
| `ToolCall` | `function_call`；要求对象参数与稳定内部 ID |
| `ToolResult` | `function_response`；按历史 call ID 找名称及原生 ID，无匹配则 `LLMContextError` |
| `ReasoningText` | 返回的 thought 文本可读；没有对应 Gemini replay state 的外来 reasoning 不作为签名思考重新发送 |
| `Audio` / `Video` / 通用 `File` 等其他输入 | 明确 `LLMConfigurationError`，不转字符串、不静默丢弃 |

图片当前只接受可可靠识别的 PNG、JPEG、WebP、HEIC、HEIF MIME；后两者还取决于本机 Pillow 解码能力。适配器本身不抓远程图片、不上传 Files；请按现有 `Image` 的输入约定准备内容。输入能力不表示支持媒体生成。

非流响应逐 Part 读取文本、thought 和工具调用；不依赖 SDK 的 `.text` 快捷视图。流式先预取首 chunk，使真实建连纳入 attempt 超时，再输出文本/推理增量；**工具调用只在流正常结束并验证参数完整后发布**，不提供可执行的半成品参数增量。消费完成后再读取 `response.call_list`、`stop_reason` 和 usage。

`STOP` 与文本 `MAX_TOKENS` 可返回；后者表示截断。截断工具参数、`partial_args` / `will_continue`、未知或缺失结束原因拒绝，不执行工具；安全阻断转为 `LLMContentFilterError`，无候选等协议异常转为 `LLMAPIError`。usage 分开记录输出与 thought token，不将 reasoning 重复计入 completion。

## thought signature 与工具回合来源锁

`thought_signature` 是原始 **Part 级**不透明数据，不是公开的 reasoning signature 文本。codec 将原始 Parts、原生/内部 call ID 映射、标准内容指纹放进内部 `provider_state`，`response.to_payload()` 和自动追加路径会保留状态。不要手工只拼文本和 `ToolCall` 来替代完整 assistant payload，也不要打印、序列化到公开日志或编辑原始状态。

Gemini 请求观测只提交模型名及内容/工具数量，不提交原始 Parts 或签名；这不代表调用方自行 dump `provider_state` 是安全的。普通用户文本、工具结果同样应按敏感数据处理。

重放会核对来源 **`api_provider` + 规范化 endpoint/API 版本 + 模型 ID** 以及标准内容指纹；修改签名绑定的文本、工具名称或参数，或切换目标，会抛 `LLMContextError`。用现有响应继续工具回合：

```python
# response 已完整消费，且其 assistant payload 已自动追加（默认行为）。
for call in response.call_list or []:
    result = await execute_allowed_tool(call)  # 业务侧白名单、参数校验及副作用控制
    response.add_payload(LLMPayload(
        ROLE.TOOL_RESULT, ToolResult(result, call_id=call.id, name=call.name)
    ))
next_response = await response.send(stream=False)
await next_response
```

`response.send()` 在当前响应有工具调用和 Gemini 状态时，将下一步候选限制为该来源；没有匹配配置则拒绝，不跨模型/provider 回传签名。调用方重建 `LLMRequest`、修改历史或绕开此路径时不能假定同样继承锁；对更早历史的 Gemini 状态仍会做来源校验。失败后应明确终止/重开工具回合，不自动重做已执行的副作用工具；生产工具循环还需业务级最大轮数与幂等控制。

## 资源与运行示例

从仓库根目录手动运行（**会向目标服务发请求并可能计费**）：

```powershell
$env:GEMINI_MODEL_ID = '<账户已开通的模型 ID>'
# GEMINI_API_KEY 可由安全环境提供；未设置时示例用 getpass 隐藏输入。
uv run python examples/src/kernel/llm/gemini_example.py --help
uv run python examples/src/kernel/llm/gemini_example.py
```

可用 `GEMINI_BASE_URL`、`GEMINI_API_VERSION` 显式配置原生协议网关/版本；不承诺这些组合已通过真实服务验证。示例只发一个非流式文本请求，通过 public `LLMRequest` 与 `ModelClientRegistry` 注入独立 client，并在 `finally` 中 `await client.aclose()`。

client 池按事件循环隔离且有容量上限。client 的 `aclose()` 只关闭当前循环空闲资源，不能替代关闭活动流。`LLMResponse` 提供 `await response.aclose()` 和 `async with response:`；可能提前 break 时必须使用该作用域或 finally 显式关闭，普通 async-for break 不保证立即释放保留引用的迭代器。关闭会清除未完成工具调用，不能继续把半成品当完整结果执行。完整耗尽、回调取消及显式关闭都会结束 stream 所有权，不依赖 GC。默认共享实例由运行时关闭，独立创建的实例由调用方负责关闭；应在原事件循环结束前完成。

## 限制与证据等级

- 首期范围是文本、图片输入、原生工具和非流/流式 Chat。Vertex、Gemini Embedding/ASR/Rerank、Live、Files 上传、音视频/图片生成、SDK 内建执行工具、自动长解码续接不在支持范围内。
- 非支持媒体输入、媒体生成输出、可执行代码及未知非首期 Part 会明确拒绝，不把它们当作空白成功。签名-only Part 可以保留，不能据此宣称支持媒体生成或任意 Gemini wire 扩展。
- 工具 schema 转换不是对 Gemini 所有 JSON Schema 约束的服务端兼容性证明；不完整参数变体拒绝，重复/异常调用 ID 等网关行为也需专项验证。
- replay state 是进程内续接状态，不是受认证的持久化协议或跨重启恢复保证；不要把来自不可信输入的状态当作可信签名历史。
- 本地 token 预算仍是框架估算，不是 Gemini `countTokens` 的精确计数。思考参数、模型可用性、真实计费及网关路径语义依赖服务端。
- 仓库有真实 SDK 配合模拟 HTTP 的契约/转换/生命周期测试；离线测试不等于真实服务联调。本文与示例不声明真实账户已联调、全部测试通过或 100% 覆盖率。本次文档交付仅执行离线静态检查，不运行联网示例。

## 相关实现

- [客户端与 registry](./README.md)
- [Request](../request.md) / [Response](../response.md)
- [`gemini_client.py`](../../../src/kernel/llm/model_client/gemini_client.py)
- [`gemini_codec.py`](../../../src/kernel/llm/model_client/gemini_codec.py)
