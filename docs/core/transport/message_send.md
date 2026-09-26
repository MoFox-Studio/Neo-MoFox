# message_send 子模块

对应源码目录：src/core/transport/message_send

## 模块目标

message_send 负责将 core 侧 Message 下发到目标适配器，并在发送前后接入事件与历史记录。

## 关键文件

- message_sender.py: MessageSender 主实现。
- converter.py: 复用 message_receive 的 MessageConverter。
- __init__.py: 单例入口。

## send_message 流程

1. 确定目标 adapter_signature（显式传入或按 platform 推断）。
2. 获取 AdapterManager 并查找活跃 adapter 实例。
3. 调用 _apply_bot_sender_info 用 bot 信息覆盖 sender 字段。
4. 使用 MessageConverter.message_to_envelope 转换消息。
5. 发布 ON_MESSAGE_SENT 事件，若 decision=STOP 则中止发送。
6. 调用 adapter._send_platform_message 真正下发。
7. 对已发送的二进制媒体调用 MediaManager.store_media，登记可回查的媒体 ID。
8. 调用 StreamManager.add_sent_message_to_history 写入发送历史（剥离 base64 数据，保留媒体 ID 和上下文模式）。

## 插件媒体上下文

`src.app.plugin_system.api.send_api.send_media` 统一发送图片、表情包、语音和视频。`context_mode` 决定该条媒体在后续聊天中的表达方式：

- `placeholder`（默认）：二进制媒体缓存成功后在历史文本中保留带 ID 的占位符；资源 URI 只保留文本占位符，不调用识别模型。
- `provided`：要求提供非空 `processed_plain_text`，将其原样写入历史上下文，不调用识别模型，也不在文本中自动追加媒体 ID。二进制媒体仍按 ID 缓存以供回查。
- `caption`：要求提供非空 `processed_plain_text`，将调用者描述写成 `[类型:描述]`，不调用识别模型；二进制媒体缓存成功后历史文本为 `[类型(媒体 ID):描述]`，缓存失败或资源 URI 不附 ID。支持图片、表情包、语音和视频。
- `description`：发送前调用该媒体类型对应的识别服务，将识别结果附在历史文本中；没有结果时退回占位符。
- `native`：请求聊天流程内联原始媒体，目前仅支持 base64 图片；是否能交给模型取决于聊天插件及模型的图片输入能力。

```python
from src.app.plugin_system.api.send_api import send_media, send_voice

await send_media("image", image_base64, stream_id, context_mode="native")
await send_media("voice", voice_base64, stream_id, context_mode="description")
await send_media(
    "image", image_base64, stream_id,
    processed_plain_text="生成的封面图", context_mode="caption",
)
await send_voice(
    voice_base64, stream_id,
    processed_plain_text="今天会晚些回来",
)
```

`send_voice` 省略 `processed_plain_text` 时使用默认占位符；明确提供非空原文时使用 `caption`，由框架添加语音标记，并仅在媒体缓存成功后附加可回查的 ID。不需要调用方自行拼接 `[语音]`。如需保持原文完全不变，显式使用 `send_media(..., context_mode="provided")`。

HTTP(S) 与 `file://` 资源 URI 支持 `placeholder`、`provided` 和 `caption`；框架不下载资源，也不生成可回查的媒体 ID。`file://` 能否到达目标平台由适配器和其运行环境决定，特定平台的自定义段类型仍需使用对应接口。`provided` 和 `caption` 的文案只进入历史上下文，不随媒体段作为文字发给平台。`send_image`、`send_emoji` 和 `send_video` 等原有入口的默认行为不变；需要调用者描述与媒体 ID 同时写入历史时，应显式调用 `send_media(..., context_mode="caption")`。历史消息中的 `context_mode` 属于单个媒体项，聊天插件按媒体项筛选原生图片，旧历史的 `include_in_context` 标记仍可识别。

## 关键约束

- adapter_signature 推断依赖 registry 中 adapter_cls.platform。
- 出站拦截属于预期行为，send_message 返回 True 但不发送。
- stream_id 缺失时会跳过历史写入并记录告警。

## 常见问题

- 找不到 adapter：检查 adapter 是否已启动且平台注册值匹配。
- 消息发出但无历史：检查 stream_id 和 get_or_create_stream 参数。
- 事件拦截导致不发：检查 ON_MESSAGE_SENT 订阅者返回决策。
