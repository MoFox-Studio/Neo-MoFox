# message_receive 子模块

对应源码目录：src/core/transport/message_receive

## 模块目标

message_receive 负责将适配器输入的 MessageEnvelope 转换为 core 统一 Message，并通过事件分发给下游处理。

## 关键文件

- receiver.py: MessageReceiver 主入口与路由策略。
- converter.py: MessageEnvelope <-> Message 双向转换器。
- utils.py: stream_id 推导、chat_type 推断、base64 规范化。
- __init__.py: 单例管理（init/get/reset）。

## 处理流程

1. receive_envelope 接收 envelope 与 adapter_signature。
2. 校验 direction 必须为 incoming。
3. 按 message_info.message_type 路由：
- 标准消息（message/group/private）走 _handle_message。
- 其他消息走 _handle_other。
4. _handle_message 调用 converter.envelope_to_message。
5. 更新 person_info 后发布 ON_MESSAGE_RECEIVED。
6. _handle_other 先发布 ON_RECEIVED_OTHER_MESSAGE，若被处理器填充 processed，再回灌为标准消息。

## converter 细节

- _parse_segments 递归解析消息段，支持嵌套 seglist。
- 媒体段可触发 VLM 识别转换为文本描述。
- reply/@/unknown 段会落入统一结构字段。
- message_to_envelope 用于发送链路复用，支持媒体段和 reply 段重建。

## 关键约束

- stream_id 统一通过 extract_stream_id 生成，依赖 ChatStream 哈希规则。
- 单段解析失败不会中断整条消息解析。
- 对未声明 message_type 的旧 envelope，回退到 has_segments 判断。

## 调试建议

- 收不到消息先看 direction 是否为 incoming。
- 转换失败先检查 message_info 和 message_segment 字段完整性。
- 非标准消息被丢弃时，检查 ON_RECEIVED_OTHER_MESSAGE 处理器是否写入 processed。
