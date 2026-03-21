# Transport 排障手册

本手册针对 transport 链路的高频故障提供定位顺序。

## 入站消息无响应

排查顺序：

1. 检查 Adapter 是否向 core_sink.send 发送 incoming envelope。
2. 检查 MessageReceiver 是否收到且 direction 为 incoming。
3. 检查 converter 是否抛出字段缺失异常。
4. 检查 ON_MESSAGE_RECEIVED 订阅链是否执行。
5. 检查 distributor 是否成功写入 StreamManager。
6. 检查 StreamLoopManager 是否 running 且 stream_loop_task 已创建。

## 非标准消息被丢弃

排查顺序：

1. 检查 _handle_other 是否触发 ON_RECEIVED_OTHER_MESSAGE。
2. 检查事件处理器是否写入 params.processed。
3. 若 processed 为空，消息按设计被丢弃。

## 出站消息未发送

排查顺序：

1. 检查 adapter_signature 是否成功推断。
2. 检查 AdapterManager.get_adapter 是否返回实例。
3. 检查 ON_MESSAGE_SENT 订阅者是否返回 STOP。
4. 检查 adapter._send_platform_message 执行异常。

## 流循环卡住或频繁重启

排查顺序：

1. 检查 watchdog warning/restart 阈值配置。
2. 检查 chatter 执行时间是否持续超 tick_interval。
3. 检查 wait 状态是否长期不满足恢复条件。
4. 检查 message buffer 跳过计数是否异常持续增长。

## HTTP 路由不可达

排查顺序：

1. 检查 HTTPServer 是否已启动。
2. 检查 RouterManager 是否成功 mount 路由。
3. 检查 host/port 与实际访问地址是否一致。
4. 检查 route_path 是否与预期一致。

## 建议日志关注点

- message_receiver: 消息接收与转换
- distributor: 分发与流创建
- stream_loop_manager / conversation_loop: tick 与驱动器状态
- message_sender: 发送成功与失败
- sink_manager / core_sink_impl: bridge 层收发
- http_server: 路由服务生命周期
