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
8. 发送成功后调用 StreamManager.add_message_to_history（出站方向）写入发送历史（剥离 base64 数据，保留媒体 ID 和上下文模式）。

## 历史读取与信封转换

### 消息 ID 与发送时间

几个字段分别用于不同的事情：

| 字段 | 含义 | 是否决定历史顺序 |
|------|------|------------------|
| `id` | 数据库自动分配的整数主键，表示记录入库时分配的编号 | 仅在发送时间相同时用于稳定排序 |
| `message_id` | 平台消息 ID，用于查原消息、回复和去重 | 不参与历史排序 |
| `time` | 消息原发送时间，不是补录时间 | 历史排序的主要依据 |

例如，17:00 的消息先入库，15:00 的旧消息后来补录。旧消息的数据库 `id` 可能更大，但读取历史时仍排在 17:00 的消息之前。补录保留平台原始 `message_id`，不会给旧消息随机生成新的平台消息 ID。

### 按时间读取与分页

`stream_api.load_stream_context()` 和 `stream_api.get_stream_messages()` 统一按原发送时间查询，不接收排序选项。查询先按 `time DESC, id DESC` 选择最近的记录，再把选中的记录按正序返回。时间完全相同的消息按数据库主键排序；这能保证结果稳定，但不能还原平台同一秒内未提供的精确发送顺序。

```python
from src.app.plugin_system.api import stream_api

latest = await stream_api.get_stream_messages(stream_id, limit=50)
older = await stream_api.get_stream_messages(stream_id, limit=50, offset=50)
context = await stream_api.load_stream_context(stream_id, max_messages=100)
```

第一行选择最近 50 条，第二行跳过较新的 50 条再选择一页，每页内部都是从早到晚。新增或补录消息可能改变分页位置，所以 `offset` 不能当作固定的增量游标。

`load_stream_context()` 排除上下文清空时间及之前的记录，省略 `max_messages` 时加载清空边界之后的全部记录。`get_stream_messages()` 查询持久化历史，不受上下文清空边界影响。

### 查询私聊对端

`stream_api.get_stream_info()` 返回流信息，其中 `person_id` 是人物表的内部关联 ID，不是平台用户 ID。需要私聊对端的原平台 ID 时，组合 `person_api.get_person_by_id()` 查询人物，再读取 `user_id`：

```python
from src.app.plugin_system.api import person_api, stream_api

stream_info = await stream_api.get_stream_info(stream_id)
user_id = None
if stream_info and stream_info["chat_type"] == "private":
    person_id = stream_info.get("person_id")
    if isinstance(person_id, str) and person_id:
        person = await person_api.get_person_by_id(person_id)
        if person is not None and person.platform == stream_info["platform"]:
            user_id = person.user_id
```

人物不存在时返回 `None`，查询不会创建人物、刷新交互时间或增加交互计数。群聊直接使用流信息中的 `group_id`，不需要查询人物。上述调用分别要求 `stream_api` 2.0.0 和 `person_api` 1.1.0。

### 静默补录与媒体转换

`stream_api.add_message_to_history(..., silent=True)` 可静默补录历史，不增加实时未读、不替换当前消息，也不改变流的活跃时间。补录记录按原时间进入历史，上下文清空边界仍然生效。

`message_api.envelope_to_message(envelope, recognize_media=False)` 保留段解析、媒体哈希和回复关系处理，但跳过媒体管理器的识别、存储和事件阶段，因此不会触发 VLM、ASR 或媒体存储副作用。默认 `True` 保持原有识别行为。

`recognize_media=True` 时仍由媒体管理器按流和媒体类型决定是否跳过识别；这种跳过只影响识别，媒体存储仍按原流程执行。它与 `recognize_media=False` 跳过整个媒体管理阶段不同。

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
