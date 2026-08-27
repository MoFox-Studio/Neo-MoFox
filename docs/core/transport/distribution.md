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
2. 发布 BEFORE_CHATTER_DISPATCH 事件（分发前置门控，支持 continue=False 无副作用跳过本 tick）。
3. 执行 wait_state_check 和 message_buffer_check。
4. 获取或创建 chatter 生成器。
5. 发布 ON_CHATTER_STEP 事件（支持 continue=False 中断本 tick）。
6. 执行 anext(chatter_gene) 并处理 Success/Failure/Wait/Stop。
7. 异常时回收生成器并更新失败统计。

## BEFORE_CHATTER_DISPATCH 门控语义

- 触发时机：每个 tick 的最前端，先于 wait_state_check、message_buffer_check 与生成器获取/创建。
- 事件参数：`stream_id`、`context`、`tick`、`continue`（默认 True）、`reason`（阻断原因文案）。
- 阻断语义：处理器将 `continue` 置为 False 时，本 tick 整体跳过；等待状态、resume 事件、消息缓冲计数、生成器与 `triggering_user_id` 均保持原状，不产生任何状态消费。
- 处理租约：`is_chatter_processing` 从本事件发布开始置为 True，覆盖分发决策到 chatter 单步结束的完整区间，`finally` 中统一释放；接收侧可依据该标记避免在同一流上并发干预。
- 与 ON_CHATTER_STEP 的区别：ON_CHATTER_STEP 位于链路末端，阻断时等待状态与 resume 事件已被消费，并触发 `_discard_blocked_messages` 丢弃积压消息；BEFORE_CHATTER_DISPATCH 用于需要无损阻断的场景。

## 性能与稳定性要点

- tick_interval 控制轮询频率。
- message buffer 用于高频消息合并，避免 chatter 过度抖动。
- WatchDog 重启有 cooldown 和 inflight 防抖保护。

## 排障建议

- 流不启动：检查 ON_ALL_PLUGIN_LOADED 是否触发及 slm.is_running。
- 流频繁重启：检查 watchdog 阈值与处理耗时。
- 长时间不响应：检查 wait 状态和缓冲跳过计数是否持续累积。
