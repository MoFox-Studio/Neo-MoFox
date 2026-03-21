# Transport 模块文档

本目录对应 src/core/transport，负责消息接收、分发、路由、发送与 Sink 协调。

## 模块职责

- 接收来自适配器或外部输入的消息
- 按规则分发到命令、聊天、工具或事件处理链路
- 承担消息输出发送与平台落地

## 文档导航

- [message_receive](./message_receive.md): MessageEnvelope 转 Message 的接收与转换。
- [distribution](./distribution.md): ON_MESSAGE_RECEIVED 到流驱动器的分发调度。
- [message_send](./message_send.md): Message 转 Envelope 并下发到 Adapter。
- [sink](./sink.md): CoreSink 实现、工厂与 SinkManager。
- [router](./router.md): HTTPServer 单例与 Router 挂载支撑。
- [troubleshooting](./troubleshooting.md): 常见链路故障与定位路径。

## 当前代码结构

- distribution/: 消息分发链路
- message_receive/: 消息接收能力
- message_send/: 消息发送能力
- router/: 路由能力（含 HTTP 路由相关）
- sink/: Sink 管理与输出聚合
- __init__.py: 对外导出

## 传输主链路

### 入站链路（Adapter -> Core）

1. Adapter 通过 CoreSink.send 送入 MessageEnvelope。
2. SinkManager 的 message_callback 调用 MessageReceiver.receive_envelope。
3. MessageReceiver 使用 MessageConverter 解析为 Message。
4. MessageReceiver 发布 ON_MESSAGE_RECEIVED 事件。
5. distribution/distributor 订阅该事件，写入 StreamManager 并启动流驱动器。
6. StreamLoopManager 驱动 run_chat_stream，推进 chatter 执行。

### 出站链路（Core -> Adapter）

1. 业务侧构建 Message 后调用 MessageSender.send_message。
2. MessageSender 根据 platform 推断或使用指定 adapter_signature。
3. MessageConverter.message_to_envelope 构建 MessageEnvelope。
4. 发布 ON_MESSAGE_SENT 事件（允许拦截）。
5. 调用 Adapter._send_platform_message 发送到平台。
6. 写入 sent message 历史（StreamManager）。

## 关键事件

- ON_MESSAGE_RECEIVED: 入站消息进入 core 分发主链路。
- ON_RECEIVED_OTHER_MESSAGE: 非标准 envelope 的二次处理入口。
- ON_ALL_PLUGIN_LOADED: 启动 StreamLoopManager。
- ON_CHATTER_STEP: 每个 tick 前的对话步进钩子。
- ON_MESSAGE_SENT: 出站消息下发前的拦截与补充入口。

## 运行时协作关系

- 与 managers 协作：stream_manager、adapter_manager、event_manager。
- 与 components 协作：EventType、BaseAdapter、BaseRouter。
- 与 kernel 协作：logger、event bus、task_manager、watchdog。

## 配置与节奏

transport 关键节奏依赖 bot 配置：

- tick_interval: 会话循环 Tick 间隔。
- stream warning/restart threshold: WatchDog 触发阈值。
- message buffer 参数: 控制消息合并与强制放行策略。

## 设计约束

- MessageReceiver 仅处理 incoming 方向 envelope。
- 适配器子进程模式已移除，SinkFactory 仅支持进程内 CoreSink。
- stream_id 统一使用哈希规则生成，避免跨平台碰撞。

## 推荐阅读顺序

1. message_receive
2. distribution
3. message_send
4. sink
5. router
6. troubleshooting

## 依赖关系

- 上游输入：adapter、chatter、command、router。
- 下游执行：core/managers、platform adapter、kernel 事件与并发设施。
