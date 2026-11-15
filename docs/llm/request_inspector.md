# 原生请求监视器

`RequestInspector` 是 LLM 内核的进程内观测服务，不提供 HTTP 端点，也不依赖
WebUI。原生适配器直接报告最终 SDK 参数、已解析的响应和 SDK 原始事件；不使用
SDK 包装器、代理客户端或运行时猴子补丁。WebUI 只负责认证、读取快照和展示。

## 支持范围

- OpenAI Chat Completions、OpenAI Responses、Anthropic Messages。
- 兼容 OpenAI 协议的嵌入调用，以及现有 SDK 原生重排序调用。
- 聊天流式/非流式、框架重试、模型切换、流消费重试、取消及异常。
- 工具定义、模型返回的调用 ID/名称/参数，以及请求中已有的工具结果。

不跟踪工具的实际执行，不重放请求，不新增供应商协议，不记录 ASR，不持久化历史。
采集独立于 `enable_metrics`；历史 LLM 用量统计与当前缓冲统计是不同的数据集。

## 公共读取接口

```python
from src.kernel.llm import get_inspector

inspector = get_inspector()
snapshot = inspector.snapshot()
# {instance_id, revision, capacity, records, stats}

for summary in snapshot["records"]:
    detail = inspector.get_record(summary["id"])
    if detail is None:
        continue  # 两次读取之间可能已经清空或淘汰
    print(detail["params"])       # 实际 SDK 参数的完整 JSON 层级
    print(detail["response"])     # 标准化模型结果
    print(detail["raw_response"]) # 非流式 SDK JSON
    print(detail["raw_events"])   # 按接收顺序保存的原始 SDK 流事件

result = inspector.clear_snapshot()
# {instance_id, revision, cleared_count}
```

- 默认保留最近 **200 条**记录，超过容量时按创建顺序淘汰最早记录，包括尚未完成的记录。
- 不持久化；进程重启后数据丢失，`instance_id` 改变。
- 每次缓冲修改递增 `revision`，单条记录修改递增 `version`。读取不会推进版本。
- ID 单调递增，清空不重置。读取结果与内部状态隔离。
- `snapshot()` 在同一锁内返回摘要及当前缓冲统计，不包含请求/响应正文。
- 清空只删除观测记录，不取消 SDK 请求；迟到的更新不会重新插入已删除记录。
- `list_summaries()`、`clear()` 和 `import_json()` 保留；`clear()` 仍返回删除数量。

## 详情结构

| 字段 | 含义 |
| --- | --- |
| `instance_id / id / version` | 进程实例、记录及记录版本 |
| `logical_request_id / attempt` | 一次逻辑发送及其从 1 开始的调用尝试序号 |
| `api_name / model / api_provider / request_name` | 调用标识 |
| `status / stream` | 生命周期状态及流式标记 |
| `ts / started_at / finished_at` | 创建、开始和结束时间（Unix 秒） |
| `duration_ms / first_token_ms` | 尝试耗时、首个文本/推理/工具参数增量耗时 |
| `tokens_in / tokens_out` | 供应商实际返回的 token，缺失为 `null` |
| `estimated_input_tokens` | 独立的输入估算，不替代实际 token |
| `params / metadata` | 原样保留字段名和内容的 SDK 参数及请求元数据 |
| `rendered` | 从同一参数快照派生的概览、消息和工具视图 |
| `response` | `kind=chat/embedding/rerank` 的标准化输出 |
| `raw_response / raw_events` | SDK 原始响应或原始流事件 |
| `error` | 错误类型、异常字符串参数和可用的 HTTP 状态码；内容不脱敏 |
| `capture_info` | 原始数据预算、截断及不可用标记；兼容字段 `redacted` 恒为 `false` |

`params` 保留 `messages/input/system/instructions/tools`、生成参数和
`extra_body/extra_headers/extra_query` 的真实层级，未知字段也会保留。
展示方可直接将其渲染成结构树，不必重新解析供应商协议。

**这里的原始 JSON 是 SDK 对象与调用参数，不是 HTTP 抓包。** SDK 自动注入的
网络字段不保证出现在参数快照中。Anthropic 流使用 SDK `messages.stream`，记录
`stream=true` 表示实际流模式；该标记不额外传给此 SDK 方法。

## 状态与重试

- `captured`：手工捕获或导入，不能推断调用执行成功。
- `pending`：已分配调用尝试，尚未得到最终 SDK 参数。
- `running`：开始原生调用，尚未收到流事件。
- `streaming`：已接收原始流事件或有效内容增量。
- `succeeded / failed / cancelled`：执行终态；重复终态通知不覆盖已有结果。

同一次正常 `send()` 的框架重试、模型切换以及流式消费重试共享逻辑 ID，每次实际
尝试分别占一条记录。工具结果触发的新一轮 `send()` 使用新逻辑 ID。SDK 内部的
重试不拆分记录。

执行层创建句柄，原生适配器复用它；直接调用适配器则单独建档。流生成器显式
持有句柄，因此在另一任务中消费时也不会串记录。未接入原生观测的自定义客户端
不伪造 SDK 请求体和原始数据，标记不可用。

外层超时记为 `failed`，调用方取消记为 `cancelled`。流错误/取消保留已收到的
部分响应，清理资源不会吞掉原始异常。观测失败不改变业务返回或重试决策，最多
按尝试记录一条只含错误类型的诊断，不输出异常正文或异常堆栈。

## 提前停止消费

```python
response = await request.send(stream=True)
try:
    async for chunk in response:
        print(chunk)
        if should_stop:
            break
finally:
    await response.aclose()
```

`aclose()` 幂等，不主动读取剩余流内容；即使尚未开始迭代，也会关闭已经取得的 SDK
资源。正常消费、消费异常及回调异常会清理资源。普通 `break` 不保证 Python
立即关闭异步生成器，所以提前停止时应显式调用 `aclose()`。监视器不会启动后台
任务替调用方消费流，也不会将未消费的调用伪造为成功。

直接调用原生适配器时，返回五元组中的第三项仍是原生异步生成器。调用方同样
应在 `finally` 中执行 `await stream.aclose()`；即使尚未消费任何事件，也会结束
记录并关闭已打开的 SDK 流，重复关闭不会重复释放资源。

适配器返回前仅进入生成器的清理作用域，并消耗一个内部启动占位，不读取 SDK
事件、不向调用方输出空事件，也不新增 SDK 或迭代器包装器。Anthropic 的流管理器
延迟到真正消费时创建；未消费就关闭时，不创建请求协程或进入传输上下文。

## 安全与容量

- **不执行任何脱敏。** 请求头、查询参数、提示词、工具定义及参数、模型响应、流事件、
  元数据和错误中的可序列化键名及内容原样保留；不扫描正文、不学习密钥、不改写字段名。
- `capture_info.redacted` 为兼容既有读取契约保留，恒为 `false`，不表示内容不含敏感信息。
- 采集接口不再接受 `secrets` 参数，也不从模型配置提取密钥用于观测；不会额外捕获 SDK
  自动注入的认证头，但最终 SDK 参数或响应中已经出现的凭据不会被隐藏。
- 展示、复制和导出使用同一份独立快照；不能将其当成可公开分享的数据。
- 不支持的对象使用 `[unavailable]`，并标记不完整；不调用任意对象 `repr/str`
  兜底。其他可序列化字段仍然保留。
- 单条原始响应和流事件共用默认 **2 MiB** 的紧凑 UTF-8 JSON 预算。超限保留
  前缀/部分结构，仍返回合法 JSON，并设置 `raw_truncated=true`；标准化内容继续采集。
- `raw_bytes` 是实际保留的紧凑 JSON 字节数，不是网络流量或 Python 堆占用。
- 该预算不限制请求正文和标准化结果的大小。200 条容量也不等于固定总内存上限。
- 请求头、正文、响应及错误可能包含明文 API Key、Cookie、个人信息等敏感内容。
  外部接口必须认证并限制访问；导出、截图或分享前需自行移除敏感数据，不应公开暴露监视器。
- 取消脱敏不改变异常传播、模型调用、流清理、预算及快照隔离规则。异常只读取已有字符串
  参数，不通过任意对象的 `str/repr` 获取内容；日志仍不输出完整请求、响应或异常正文。

## 验证

```shell
uv sync --locked
uv run pytest test/kernel/llm/test_request_monitor.py test/kernel/llm/test_request_inspector.py test/kernel/llm/test_request_monitor_regressions.py test/kernel/llm/test_request_inspector_fidelity.py --no-cov
uv run pytest test/kernel/llm --no-cov
uv run python examples/src/kernel/llm/request_inspector_example.py
```

测试仅使用受控的模拟 SDK 对象，不读取真实密钥、不访问供应商。示例只使用
内存监视器，不执行模型调用。WebUI 接口测试可将 `NEO_MOFOX_ROOT` 指向本仓库，
联合验证真实监视器、管理器、路由及 Pydantic 数据契约。
