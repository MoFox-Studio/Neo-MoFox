# emoji_sender

一个“表情包收藏并发送”的插件：

- 定时从主程序内置的 `data/media_cache/emojis/` 随机抽取表情包
- 调用 VLM 注入主配置人格（`config/core.toml` 的 `personality`）后，让模型决定是否收藏并输出标注（描述 + 情感 tag）
- 若收藏：复制源文件到 `data/emoji_sender/memes/`，并将描述 embedding 写入 `data/emoji_sender/vector_db/`
- 对外暴露：
  - Action：根据“目标描述 + 情感 tag”发送表情包
  - Service：供其他插件以编程方式检索/发送
- 检索阶段支持通过 `config/plugins/emoji_sender/config.toml` 中的 `vector.temperature` 控制采样强度，避免代表性表情反复被固定选中

说明：情感 tag 预设为插件内置常量，不进入配置。用户手动删除 `data/emoji_sender/memes/` 中不想要的表情后，会在下一次入库任务开始时自动清理数据库对应条目。

## 入库与拒绝去重

- 优先按文件名顺序处理手动目录中的表情包，跳过已入库或已明确拒绝收藏的内容；没有待处理的手动图片时，按配置决定是否随机抽取缓存。
- 缓存候选按随机顺序最多检查一遍，跳过已拒绝、已入库或无法读取的图片；每轮最多评估一张待处理图片，没有可用候选时结束本轮。
- VLM 明确返回 `keep=false` 时，仅在当前插件实例的内存中记录图片哈希，各轮定时任务创建的服务共享这份集合；插件重载或 Bot 重启后清空，可重新评估。
- 拒绝状态不持久化，不写入向量库、不占用收藏数量，也不删除原始素材。模型调用失败、无效输出或收藏标注不完整时不标记拒绝，后续仍可重试。

## 聊天中的主动收藏与重新识别

direct 和 picker 两种发送模式均支持以下动作。Bot 可以按自己的喜好、人设和表达需要主动选择收藏，也可以在识别不准确时重新看图。

- `collect_emoji_meme(media_id)`：收藏当前聊天中的图片或表情包，支持 GIF，重复收藏不会重复入库。`media_id` 是聊天记录中图片或表情包的完整媒体哈希，不是消息 ID。
- `refresh_emoji_meme(meme_id, extra_prompt="")`：再次调用 VLM，更新图片描述和识别缓存；已收藏的图片还会更新情感标签和检索向量。`meme_id` 可用当前聊天的完整媒体 ID、收藏 ID 或检索候选中的 12 位 ID。可通过 `extra_prompt` 补充识别提示，例如“请重点核对图中文字”。

两个动作都不会自动发送消息。重新识别不要求先收藏，也不会自动收藏或修改图片。未收藏的图片仅限当前聊天；原图已被清理时，会提示重新发送图片。

## 发送模式（1.1.0 起）

通过 `config/plugins/emoji_sender/config.toml` 的 `plugin.interaction_mode` 切换，修改后需重载插件：

### direct（默认）——一步直达

注册 Action `send_emoji_meme`：AI 给出"目标描述 + 情感 tag"，插件按向量距离与温度采样自动挑选一张直接发送。

- 适合：简单场景，省一轮 LLM 调用
- LLM 组件：`send_emoji_meme`

### picker——查询挑选（AI 亲自挑选）

注册 Tool `search_emoji_memes` + Action `send_emoji_meme_by_id`，两段式流程：

1. AI 调用 `search_emoji_memes(描述, [情感tag], page)` 查看候选列表（每项含 id、标签、描述、距离，距离越小越贴切，同一表情多标签已去重）
2. AI 从中挑选最契合的一张，调用 `send_emoji_meme_by_id(id)` 发送；不满意可换描述重查或翻页

- 适合：希望 AI 对发什么表情有更高控制力、表达更精准的场景
- 每页数量由 `picker.page_size` 控制（默认 6）

### 使用历史去重（两种模式共通）

`[dedup]` 配置节：

- `enabled`：默认开启。每个聊天流记住最近 `window`（默认 6）张已发送的表情包，检索候选时自动过滤，避免短时间重复发同一张
- 候选全被过滤时自动回退全量，不会因此无表情可发
- 历史保存在内存中，重启后清空
