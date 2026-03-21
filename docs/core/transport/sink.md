# sink 子模块

对应源码目录：src/core/transport/sink

## 模块目标

sink 子模块在适配器和 core 之间提供传输桥接层，统一 CoreSink 创建与生命周期。

## 关键文件

- sink_manager.py: SinkManager 管理 active sink 与回调。
- sink_factory.py: 根据 adapter 配置选择 sink 实现。
- core_sink_impl.py: InProcessCoreSinkImpl 实现。
- __init__.py: 对外导出。

## 关键协作关系

- AdapterManager 启动适配器时调用 SinkManager.setup_adapter_sink。
- SinkManager 创建 message_callback 并绑定到 MessageReceiver.receive_envelope。
- Adapter 通过 core_sink.send/push_outgoing 进入 transport 链路。

## InProcessCoreSinkImpl 行为

- send/send_many: Adapter -> Core，转入 message_callback。
- push_outgoing: Core -> Adapter，广播给 outgoing handlers。
- close: 取消 sink 内部登记任务并清理 handler。

## 约束与兼容

- sink_factory 仅支持进程内 sink。
- run_in_subprocess=True 会直接抛 NotImplementedError。
- send_upstream 是兼容旧命名，等价 send。

## 排障建议

- 适配器收发无响应先看 sink 是否创建成功并注入 adapter.core_sink。
- teardown 后仍有任务，检查 close 中 task_manager 取消是否执行。
