# GeminiChatClient：原生 Developer API

## 概述

`GeminiChatClient` 是 MoFox 内置的 Gemini 客户端，走 **Gemini 原生 Developer API**，而不是 OpenAI 兼容协议。它基于官方 `google-genai` SDK 实现，但只把 SDK 用在认证、序列化、HTTP 传输和基础流解码上；消息与工具的转换、上下文管理、工具执行、重试策略和统计仍由 MoFox 自己负责。

首期支持范围是**文本与图片输入、原生 function calling、非流式与流式 Chat**。Vertex AI、Gemini Embedding/ASR/Rerank、Live、Files 上传、音视频与图片生成均不在支持范围内，遇到这类输入会明确报错而不是静默降级。

本文描述当前工作区实现，不是设计计划的完成声明。依赖固定为 **`google-genai==2.33.0`**（不是旧的 `google-generativeai`），入口仍为 `src.kernel.llm.LLMRequest` / `LLMResponse`，插件继续使用现有 `llm_api`，没有独立 `gemini_api`。

---

## 实现与协议边界

- `gemini_client.py` 调用官方 SDK 的 `aio.models.generate_content` / `generate_content_stream`。SDK 负责认证、序列化、HTTP 和基础流解码；MoFox 的 `gemini_codec.py` 显式转换消息、工具声明、响应与统一 `StreamEvent`，不自行实现 REST/SSE 解析器。
- MoFox 经 `kernel.network` 创建自己的 `httpx.AsyncClient`，通过公开的 `types.HttpOptions(httpx_async_client=...)` 注入 SDK。超时、环境代理、IPv4、连接池与关闭由这一层管理；不使用 SDK chats 管理第二份历史。
- 显式设置 `vertexai=False`，只支持 API key 模式的 **Gemini Developer API**。`vertex_ai` 明确拒绝，不支持 Vertex 的 ADC/OAuth、project/location 或资源路径。
- 默认 registry 已装配 Gemini。推荐 `client_type="gemini"`；现有原生别名 `aiohttp_gemini`、`gemini_v2`、`google` 也可分发。注册分发和签名工具续轮共享大小写、首尾空白及连字符归一化规则；别名可续轮，但 provider、端点/API 版本和模型来源锁不会放宽。Gemini 不会静默改走 OpenAI。OpenAI-compatible 网关需显式选择 `client_type="openai"`，不能与本适配器混用。
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

---

### base_url 与 API 版本

`base_url` 配置 **HTTPS 服务根地址（host，可带网关前缀）**，`extra_params.gemini.api_version` 独立设置为 `v1beta`（默认）、`v1` 或 `v1alpha`。不要填写完整的 `.../models/...:generateContent` 操作 URL，也不要添加 query、fragment、用户名或密码。SDK 拼装版本与操作路径，例如：

- `{base_url}/{api_version}/models/{model}:generateContent`
- `{base_url}/{api_version}/models/{model}:streamGenerateContent?alt=sse`

旧配置末尾的 `/v1beta`、`/v1alpha`、`/v1` 会拆分为版本；显式非默认版本与后缀冲突会报错。当前默认 `v1beta` 兼作迁移默认值，不能据此认为所有显式冲突都可检测；新配置请始终使用无版本服务根。网关前缀是否被 SDK 按预期保留，需要该网关的出站协议测试，不能仅凭 URL 校验通过就认定兼容。

`model_identifier` 可带一次 `models/` 前缀，之后必须是单模型 ID；拒绝路径/操作注入和 Vertex 资源名。认证使用 SDK 的 API key 机制，禁止覆盖 `Authorization`、`x-goog-api-key`、`Host`、`Content-Type`；禁用自动重定向。

直接调用 `GeminiChatClient.create()` 时，实际模型由 `model_name` 决定，同样允许一次 `models/` 前缀；规范化后的同一 ID 用于 SDK 出站、历史来源校验和新响应的 replay state。不要求它等于 `model_set["model_identifier"]`，适配器也不会回写调用方配置。普通 `LLMRequest` 仍按所选配置传入模型名。

### 参数边界

- 仅在 `extra_params.gemini.generation_config` 下放 SDK 生成参数。未知字段及保留字段会报配置错误，不接受 OpenAI 风格的任意参数透传。
- 原生 Gemini 不接受顶层 `extra_params.tool_choice`。如果没有额外 Gemini 选项，使用：

  ```toml
  extra_params = {}
  ```

  Gemini 适配器已经根据本轮工具声明启用原生工具调用；不需要用 OpenAI 的 `tool_choice = "auto"` 打开它。若需显式指定自动工具选择，使用 Gemini 原生结构：

  ```toml
  extra_params = { gemini = { generation_config = { tool_config = { function_calling_config = { mode = "AUTO" } } } } }
  ```

  `tool_choice` 会导致配置错误，并提示使用上述结构；不要把它直接改名后放进 `generation_config`。
- `candidate_count` 固定为 1；`max_output_tokens`、`temperature` 必须用上面的 MoFox 字段设置。`tools`、`system_instruction`、`http_options`、AFC、自动续接和 continuation token 由适配器控制，不能覆盖。
- `extra_params.headers` 可加非保留请求头；非空 `query` / `body` 不支持。SDK 类型校验不等于目标模型能力校验，thinking 等参数必须另行确认。
- `force_ipv4=True` 会安装显式 transport，当前 httpx 下会绕过环境代理。需要环境代理时使用 `trust_env=True`、`force_ipv4=False`；不要声称两者可同时生效。

### 启动预检的覆盖范围

启用预检时，Bot 使用与生成请求相同的有效配置合并入口，按密钥快照、服务根/API 版本、headers、timeout、trust_env 和 force_ipv4 分组，每组执行一次官方 SDK `models.list`。这只是非生成连通性检查，不证明目标模型可生成、配额可用或工具能力已验收；不会为了预检发送生成请求。

单项配置解析失败或某组网关失败不会阻断其余有效组，日志只报告序号、计数和异常类型，不输出请求头值或凭据。没有配置模型时只检查 provider 默认配置，并明确未覆盖模型传输设置；已配置模型全部无效时不会退回默认配置制造通过。网关不支持 `models.list` 时标为未验证，不自动切换协议；预检失败本身不阻断启动。

---

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

内部工具 ID 在单份响应内保证唯一，省略原生 ID 的调用会避让整份响应中已有的原生 ID；原生 ID（包括省略值）单独保存用于工具结果回传。重复非空原生 ID 无法无歧义续接时明确拒绝，不会把两套工具参数按同一 ID 归并。

`STOP` 与文本 `MAX_TOKENS` 可返回；后者表示截断。截断工具参数、`partial_args` / `will_continue`、未知或缺失结束原因拒绝，不执行工具；安全阻断转为 `LLMContentFilterError`，无候选等协议异常转为 `LLMAPIError`。usage 分开记录输出与 thought token，不将 reasoning 重复计入 completion。

---

## thought signature 与工具回合来源锁

`thought_signature` 是原始 **Part 级**不透明数据，不是公开的 reasoning signature 文本。codec 将原始 Parts、原生/内部 call ID 映射、标准内容指纹放进内部 `provider_state`，`response.to_payload()` 和自动追加路径会保留状态。不要手工只拼文本和 `ToolCall` 来替代完整 assistant payload，也不要打印、序列化到公开日志或编辑原始状态。

Gemini 请求观测会将最终 SDK `model`、`contents`、`config` 与 `stream` 参数保存在进程内请求检查记录中，并以标准化消息/工具视图供已通过 WebUI 鉴权的用户检查；SDK 响应及流事件也按公共检视器契约记录。原生 Parts 中的 thought signature 与其他 SDK JSON 字段原样保留；不会额外采集内部 `provider_state`、客户端配置的 API key 或自动认证头。数据仅保留在进程内、容量有限且不脱敏；消息正文、工具参数与响应可能含敏感业务数据，应只通过受鉴权 WebUI 查看，不要直接导出或分享记录。

重放会核对来源 **`api_provider` + 规范化 endpoint/API 版本 + 实际模型 ID** 以及标准内容指纹；修改签名绑定的文本、工具名称或参数，或切换目标，会在发送 HTTP 前抛 `LLMContextError`。即使保留 A 的配置而直接指定 `model_name="B"`，也不能把 A 的历史签名发给 B；没有 Gemini 回放状态的历史可正常请求 B，新响应状态记录 B。

用现有响应继续工具回合：

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

---

## 运行时职责边界

Bot 仅安排启动探测和退出时机，不构造 SDK、不解析版本/认证/传输配置、不维护 Gemini 池分支。core 的 `llm_runtime` 复用领域配置合并入口；kernel 的探测能力与生成使用同一传输快照，临时探测资源独立于生成池。无模型、坏配置、不支持枚举及取消均保留明确边界，见 [运行时技术边界](./runtime.md)。

来源目标匹配在注册表技术边界完成，目标组成和别名规则不放宽；通用请求执行器不再导入 codec 或判断 SDK 包名。异常归一化层负责安全日志标记。默认资源关闭入口只回收内核拥有的默认实例，不接管注入客户端。

## 资源与运行示例

从仓库根目录手动运行（**会向目标服务发请求并可能计费**）：

```powershell
$env:GEMINI_MODEL_ID = '<账户已开通的模型 ID>'
# GEMINI_API_KEY 可由安全环境提供；未设置时示例用 getpass 隐藏输入。
uv run python examples/src/kernel/llm/gemini_example.py --help
uv run python examples/src/kernel/llm/gemini_example.py
```

可用 `GEMINI_BASE_URL`、`GEMINI_API_VERSION` 显式配置原生协议网关/版本；不承诺这些组合已通过真实服务验证。示例只发一个非流式文本请求，通过 public `LLMRequest` 与 `ModelClientRegistry` 注入独立 client，并在 `finally` 中 `await client.aclose()`。

client 池按事件循环隔离且有容量上限。client 的 `aclose()` 只关闭当前循环空闲资源，不能替代关闭活动流；待回收条目先从缓存摘除，普通关闭错误逐项隔离，其他空闲项仍会被尝试。默认共享实例由运行时在聊天请求退出后回收，独立实例由创建者在原事件循环结束前关闭。借用的 transport 不转移给 SDK 或 MoFox 关闭。

`LLMResponse` 提供 `await response.aclose()` 和 `async with response:`；可能提前 break 时必须使用该作用域或 finally 显式关闭，普通 async-for break 不保证立即释放保留引用的迭代器。消费入口只有成功领取消费权后才承担关闭责任，被拒绝的重复消费者不能关闭正在工作的消费者。提前关闭会清除未完成工具调用，不能继续把半成品当完整结果执行。

流关闭不仅释放 lease 逻辑占用，还由每次请求独立的 owner 等待实际 HTTP 响应关闭。SDK 的外层生成器关闭本身不能证明底层 body 已释放，因此 MoFox 通过公开 HTTPX response hook 追踪所属响应，不关闭共享客户端代替逐流清理。正常耗尽、显式关闭与取消路径的确定性释放由真实固定 SDK 配合未读尾部的离线流测试约束，而非依赖 GC；底层关闭失败仍可能发生并需上层处理，不能用 active 归零推断成功。

读取主错与关闭错误同时发生时，托管消费层保留既定 provider 分类错误；消费者 callback 或上下文体抛错时保留调用方异常，不把 callback 当作 provider 重试。已有取消不会被普通关闭错误替换；清理期间实际到达的新取消仍传播。没有主异常时，关闭失败必须可见；直接重复关闭 adapter 仍报告缓存失败，但不重复执行真实 body 关闭。

流式读取失败且源关闭失败时，不创建 replacement、不增加 `retry_count`、不推进策略步骤；对外仍抛原分类主错，该逻辑请求不再由响应外层重试。关闭失败只增加固定阶段和错误类型的安全次要诊断，不输出供应商错误原文、URL 或 SDK traceback。关闭成功时继续使用原有剩余预算与暴露规则；非流式重试不受此规则影响。

推荐的提前退出写法：

```python
response = await request.send(stream=True)
async with response:
    async for event in response.stream_events():
        if should_stop(event):
            break
```

`should_stop` 为调用方业务条件；此作用域保证退出时等待响应关闭，不会自动执行工具。

---

## 限制与证据等级

- 首期范围是文本、图片输入、原生工具和非流/流式 Chat。Vertex、Gemini Embedding/ASR/Rerank、Live、Files 上传、音视频/图片生成、SDK 内建执行工具、自动长解码续接不在支持范围内。
- 非支持媒体输入、媒体生成输出、可执行代码及未知非首期 Part 会明确拒绝，不把它们当作空白成功。签名-only Part 可以保留，不能据此宣称支持媒体生成或任意 Gemini wire 扩展。
- 工具 schema 转换不是对 Gemini 所有 JSON Schema 约束的服务端兼容性证明；不完整参数和重复非空原生调用 ID 会明确拒绝，其他网关特有协议行为仍需专项联调。
- replay state 是进程内续接状态，不是受认证的持久化协议或跨重启恢复保证；不要把来自不可信输入的状态当作可信签名历史。
- 本地 token 预算仍是框架估算，不是 Gemini `countTokens` 的精确计数。思考参数、模型可用性、真实计费及网关路径语义依赖服务端。
- 仓库有真实 SDK 配合模拟 HTTP 的契约/转换/生命周期测试，以及消费权、HTTP body 关闭、工具 ID/别名和停机/预检的定向回归。离线测试不等于真实服务联调；本文与示例不声明真实账户已联调、全部测试通过或全仓 100% 覆盖率。示例本轮只检查离线入口，不运行联网调用。

---

## 相关文档

- [客户端与 registry](./README.md)
- [Request](../request.md) / [Response](../response.md)
- [`gemini_client.py`](../../../src/kernel/llm/model_client/gemini_client.py)
- [`gemini_codec.py`](../../../src/kernel/llm/model_client/gemini_codec.py)
