# 启动探测与默认资源边界

`src/kernel/llm/model_client/runtime.py` 是给运行时调用的技术入口，不是新的聊天客户端或全供应商生命周期容器。现有 `ChatModelClient.create`、`LLMRequest`、流消费和插件 API 不变。

## 职责与调用链

- Bot 只决定何时执行预检、显示状态、停止来源并等待活动任务，再调用默认资源回收。
- `src/core/managers/llm_runtime.py` 负责领域提供商选择和有效配置准备，复用 `ModelConfig.get_model_set_by_name()`；配置合并失败逐项返回未验证，不降级为默认成功。
- `get_provider_probe(client_type)` 在技术分发边界选择能力；探测器接收普通技术条目，不读取 core/app 配置模型。
- Gemini adapter 使用官方 `google-genai==2.33.0` 执行 `models.list(page_size=1)`，不发送生成请求、不启用 SDK 重试。生成池与预检共享传输配置解析，但临时探测资源不借用生成池。

轻量共享契约 `probe_contract.py` 不依赖 adapter，`runtime.py` 重导出同一类型。探测结果 `ProviderProbeResult` 仅含状态 `available/unverified/unsupported`、安全说明、模型覆盖数量、分组数量和条目／组序号；core 追加按配置顺序的 provider 序号，便于定位而不输出敏感名称。不返回 SDK、池、lease、凭据、URL 或响应原文。`available` 仅说明该非生成探测完成，不保证模型生成、认证配额或计费可用。既有其他协议的 `/models` GET 仍保留 HTTP 状态记录行为，HTTP 4xx/5xx 本身不是请求异常。

能力明确声明是否需要逐模型有效配置、是否需要 provider 默认输入、是否允许取 key 失败后的匿名兼容探测。仅旧 `/models` 探测允许匿名回退，Gemini 不允许。不支持启动探测的独立评估协议不轮询密钥、不发网络请求。Gemini 无模型时只检查 provider 默认传输，明确标记未覆盖模型；有模型但全坏时不回退。普通错误隔离，取消继续传播。

## 资源所有权

`close_default_model_clients()` 只回收由内核惰性默认工厂拥有的资源，当前委托现有 `close_gemini_clients()`。它不创建默认实例、不遍历注入的 registry、不接管外部客户端；历史 OpenAI/Anthropic 缓存不因这个名字自动纳入回收。

Gemini 关闭仍为当前循环 idle-only。活动请求必须先完成响应关闭；超时不能以强关共享 client 代替。Bot 的正常/兜底回收都受现有共同截止时间约束，终端与信号在 finally 中独立恢复。

调用方显式创建的 Gemini client 应在原循环关闭；借用 transport 由调用方最终关闭。Jev 每次 evaluate 拥有自己的 HTTP client，自建 transport 随请求结束关闭，但外部注入 transport 通过借用代理复用，成功、失败或取消均不转移关闭权。

## 配置桥接与兼容

`src/core/config/jev.py::resolve_jev_config()` 处理具名配置到本项目 `JevClientConfig` 的翻译及地址/凭据绑定。插件 `jev_api` 只选择配置来源、委派并构造客户端；参数、返回类型、异常、覆盖优先级及原密钥轮询顺序不变。Jev 原 `trust_env=True` 默认与模型显式覆盖保持，不顺便合并全局 advanced 参数。

工具续轮的来源目标匹配由注册表技术入口负责，通用执行器不导入 Gemini codec。目标仍精确绑定 `api_provider + 规范化端点/版本 + model_identifier`，不放宽签名来源锁。SDK 异常的安全日志标记由错误归一化层产生，执行器不猜第三方包名。

离线示例：`examples/src/core/llm_runtime_example.py`。该示例使用替身探测器，默认不联网；真实服务与维护者人工审核需另行执行。
