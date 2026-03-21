# distribution 子模块

对应源码目录：src/core/transport/distribution

## 模块目标

distribution 负责把 ON_MESSAGE_RECEIVED 事件转换为“按流驱动”的对话执行节奏。

## 关键文件

- distributor.py: 事件订阅入口，消息写流与驱动器启动。
- stream_loop_manager.py: 每个 stream 的 loop 任务生命周期管理。
- loop.py: conversation_loop 与 run_chat_stream 执行驱动。
- tick.py: ConversationTick 轻量事件结构。
- __init__.py: 模块导出与 initialize_distribution。

## 初始化流程

initialize_distribution 会订阅两个关键事件：

- ON_MESSAGE_RECEIVED -> _on_message_received
- ON_ALL_PLUGIN_LOADED -> _on_all_plugins_loaded

后者用于在插件全部完成后统一启动 StreamLoopManager。

## _on_message_received 执行步骤

1. 从事件参数提取 Message。
2. 调用 StreamManager.get_or_create_stream 获取流。
3. 调用 StreamManager.add_message 落库并更新上下文。
4. 更新 last_message_time（用于消息缓冲窗口）。
5. 若 StreamLoopManager 已运行且该流无任务，则启动 start_stream_loop。

## StreamLoopManager 职责

- 管理 stream_loop_task 的创建、停止、重启。
- 每流锁防并发重复启动。
- 维护 wait state 和 chatter generator。
- 与 WatchDog 协作执行喂狗与卡死重启。
- 统计 active_streams、process_cycles、failures。

## run_chat_stream 核心逻辑

1. 消费 conversation_loop 产出的 tick。
2. 执行 wait_state_check 和 message_buffer_check。
3. 获取或创建 chatter 生成器。
4. 发布 ON_CHATTER_STEP 事件（支持 continue=False 中断本 tick）。
5. 执行 anext(chatter_gene) 并处理 Success/Failure/Wait/Stop。
6. 异常时回收生成器并更新失败统计。

## 性能与稳定性要点

- tick_interval 控制轮询频率。
- message buffer 用于高频消息合并，避免 chatter 过度抖动。
- WatchDog 重启有 cooldown 和 inflight 防抖保护。

## 排障建议

- 流不启动：检查 ON_ALL_PLUGIN_LOADED 是否触发及 slm.is_running。
- 流频繁重启：检查 watchdog 阈值与处理耗时。
- 长时间不响应：检查 wait 状态和缓冲跳过计数是否持续累积。
