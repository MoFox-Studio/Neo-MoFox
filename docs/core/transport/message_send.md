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
6. 将待发送的二进制媒体登记到 MediaManager，确认文件和索引可回查；缓存失败则不下发。
7. 调用 adapter._send_platform_message 真正下发。
8. 发送成功后调用 StreamManager.add_sent_message_to_history 写入发送历史（剥离 base64 数据，保留媒体 ID 和上下文模式）。

## 插件媒体上下文

`src.app.plugin_system.api.send_api.send_media` 统一发送图片、表情包、语音和视频。`context_mode` 决定该条媒体在后续聊天中的表达方式：

- `placeholder`（默认）：未提供描述时历史文本为 `[类型(媒体 ID)]`；提供 `processed_plain_text` 时作为插件描述，写成 `[类型(媒体 ID):描述]`，不调用识别模型。支持图片、表情包、语音和视频。
- `description`：发送前调用该媒体类型对应的识别服务，将识别结果附在历史文本中；没有结果时退回占位符。
- `native`：请求聊天流程内联原始媒体，目前仅支持图片；是否能交给模型取决于聊天插件及模型的图片输入能力。

```python
from src.app.plugin_system.api.send_api import send_media, send_voice

await send_media("image", image_base64, stream_id, context_mode="native")
await send_media("voice", voice_base64, stream_id, context_mode="description")
await send_media(
    "image", image_base64, stream_id,
    processed_plain_text="生成的封面图",
)
await send_voice(
    voice_base64, stream_id,
    processed_plain_text="今天会晚些回来",
)
```

`send_voice` 省略 `processed_plain_text` 时使用默认占位符；提供原文时由框架添加语音标记和可回查的媒体 ID。不需要调用方自行拼接 `[语音]`。

图片、表情包、语音、视频都必须先缓存成功才能发送；缓存失败返回 False。HTTP(S) 与本地 `file://` 资源先读取字节（单个资源上限 100 MiB）再转换为 Base64，经相同的缓存和发送链路；URL 读取失败或资源超限时报错，不下发消息。插件描述只进入历史上下文，不随媒体段作为文字发给平台。`send_image`、`send_emoji`、`send_voice` 和 `send_video` 通过默认模式发送；已有 `[类型]`、`[类型:描述]` 或 `[类型(媒体 ID):描述]` 文本不会被重复包裹，旧 ID 由当前媒体实际缓存的 ID 替换。历史消息中的 `context_mode` 属于单个媒体项，聊天插件按媒体项筛选原生图片，旧历史的 `include_in_context` 标记仍可识别。

## 关键约束

- adapter_signature 推断依赖 registry 中 adapter_cls.platform。
- 出站拦截属于预期行为，send_message 返回 True 但不发送。
- stream_id 缺失时会跳过历史写入并记录告警。

## 常见问题

- 找不到 adapter：检查 adapter 是否已启动且平台注册值匹配。
- 消息发出但无历史：检查 stream_id 和 get_or_create_stream 参数。
- 事件拦截导致不发：检查 ON_MESSAGE_SENT 订阅者返回决策。
