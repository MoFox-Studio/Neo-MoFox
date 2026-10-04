# Grok Avatar

将 QQ 用户头像转换为 Grok bot 风格的极简 2D 机器人头像，并直接发送到当前聊天。
插件提供两个输出模式：普通 Grok bot 头像，或叠加 Google 四色圆环的头像。

插件版本：`1.0.1`

## 功能

- 由 LLM 通过 `grok_avatar` Tool 直接调用，不需要聊天命令。
- 支持以目标 QQ 头像、当前聊天记录图片或指定图片作为本次生成的原始图片输入。
- 使用固定的 Grok bot icon 韩文视觉规范生成头像。
- 支持 `plain` 和 `google_ring` 两种模式。
- Google 四色圆环算法内嵌在插件中，运行时不访问在线网页或依赖浏览器。
- 每次调用都是一次新的独立图片请求，不会把上一张生成图当作下一次输入。
- 生成结果自动发送到触发 Tool 的当前聊天流，并回复触发消息。

## 工作流程

```text
图片来源（QQ头像 / 聊天记录图片 / 指定图片）
    -> onebot_expand 获取头像或历史图片信息
    -> 下载本次原始图片
    -> 独立 POST /v1/images/edits multipart 请求
             image = 本次原始头像
             prompt = 固定 Grok bot icon 规格
    -> plain：直接发送生成结果
         google_ring：本地叠加 Google 四色圆环后发送
```

图像请求不会携带 `messages`、`previous_response_id`、上一轮生成图片或聊天文本。
因此每次调用都会以选定的原始图片创建独立的图片生成请求。

## 安装要求

- Neo-MoFox 核心版本：`1.0.0` 或更高
- `onebot_expand` 插件已安装并启用
- 图像模型服务支持 OpenAI 兼容的 `POST /v1/images/edits` 接口
- Python 依赖：`httpx`、`pillow`

插件清单已经声明 `onebot_expand` 依赖。若协议端无法通过标准用户信息接口返回头像，
插件会回退到 QQ 公开头像地址 `q1.qlogo.cn`。

## 配置

插件配置文件：

```text
config/plugins/grok_avatar/config.toml
```

最小配置示例：

```toml
[plugin]
enabled = true
tool_enabled = true

[api]
base_url = "https://your-image-api.example.com/v1"
api_key = "sk-your-key"
model = "your-image-model"
timeout = 180.0

[generation]
size = 1024
output_size = 640
jpeg_quality = 90

[frame]
border_ratio = 0.04
gap_ratio = 0.02

```

### 配置字段

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `plugin.enabled` | `true` | 是否启用插件 |
| `plugin.tool_enabled` | `true` | 是否注册 LLM Tool |
| `api.base_url` | 空 | OpenAI 兼容 API 根地址或完整地址 |
| `api.api_key` | 空 | Bearer API Key，可留空取决于服务端配置 |
| `api.model` | 空 | 图像模型名称，必须支持图片编辑输入 |
| `api.timeout` | `180.0` | 图像请求超时时间，单位秒 |
| `generation.size` | `1024` | 请求模型生成的正方形尺寸 |
| `generation.output_size` | `640` | `google_ring` 输出图像边长 |
| `generation.jpeg_quality` | `90` | 圆环模式输出 JPEG 质量 |
| `frame.border_ratio` | `0.04` | Google 彩色圆环宽度比例 |
| `frame.gap_ratio` | `0.02` | 圆环与头像之间白色间隔比例 |

`generation.prompt` 默认内置用户指定的韩文 Grok bot icon 规格。插件会将它原样发送给图像模型，
不会自动追加、翻译或拼接其他提示词。除非确实需要修改模型行为，否则不要改动该字段。

## LLM Tool

Tool 名称：`grok_avatar`

参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `mode` | `plain` / `google_ring` | `plain` 直接发送；`google_ring` 叠加四色圆环后发送 |
| `qq_number` | 字符串，可选 | `source=avatar` 时的目标 QQ 号。省略时使用当前聊天流触发消息的发送者 |
| `source` | `avatar` / `chat_history` / `specified` | 图片来源，默认 `avatar` |
| `image` | 字符串，可选 | `source=specified` 时的 URL、data URL、`base64|...`、裸 base64 或本地路径 |
| `history_index` | 非负整数 | `source=chat_history` 时的图片索引，`0` 为最新一张，`1` 为上一张 |

示例调用：

```json
{
    "mode": "google_ring",
    "source": "avatar",
    "qq_number": "3863596004"
}
```

使用聊天记录中的最新图片：

```json
{
    "mode": "plain",
    "source": "chat_history",
    "history_index": 0
}
```

使用指定图片：

```json
{
    "mode": "plain",
    "source": "specified",
    "image": "https://example.com/input.png"
}
```

聊天记录图片会先读取 Neo-MoFox 消息中的媒体字段；历史记录只保留 `image_id` 或
`file` 时，插件通过 `onebot_expand` 的 `message_service.get_msg` 和
`file_service.get_image` 补回图片内容。图片内容会转换成字节后上传给图像模型，
聊天文本、图片描述和生成历史不会发送给图像模型。指定图片大小限制为 20 MB。

省略 `qq_number` 时：

- 私聊：默认使用当前私聊对象；
- 群聊：默认使用触发当前回合消息的发送者；
- 如果 LLM 要转换群内其他成员，应显式传入 QQ 号。

## Google 圆环实现

`google_frame.py` 内嵌了 [Google Avatar Frame Generator](https://github.com/2010384626/Google-Avatar-Frame-Generator)
的本地算法，不会请求该项目的在线页面。

内嵌参数包括：

- Google 红、蓝、绿、黄四色；
- 标定接缝角度：`206°`、`314°`、`48°`、`138°`；
- 圆环宽度比例：`0.04`；
- 白色间隔比例：`0.02`；
- 源图居中裁剪为正方形并进行圆形遮罩。

## 故障排查

### 模型提示不支持图片编辑

确认配置的模型和上游支持：

```text
POST /v1/images/edits
Content-Type: multipart/form-data
```

该插件不会退回到无图片的 `images/generations`，因为那样无法保证模型使用目标头像。

### 头像获取失败

插件首先调用 `onebot_expand:service:account_service` 的标准 `get_stranger_info`。
如果响应没有 `avatar`、`avatar_url`、`avatarUrl` 或 `user_avatar` 字段，
插件会使用：

```text
https://q1.qlogo.cn/g?b=qq&nk=<QQ号>&s=640
```

SnowLuma 不支持的 `get_qq_avatar` 扩展不会被调用。

### 聊天记录图片无法下载

确认 `onebot_expand` 已启用，并且协议端支持 `get_msg` 与 `get_image`。
消息记录中如果只剩媒体哈希，插件会依次尝试 OneBot 原始消息、`get_image` 返回的
base64、URL 或本地文件路径；这些入口都不可用时会返回明确的失败信息。

### 生成多次却像修改上一张图

检查上游是否自行维护了会话状态。插件每次请求均为新的 HTTP multipart 请求，
只上传本次重新下载的原始头像和固定提示词，不发送聊天历史或上一张生成结果。

## 开发与测试

在 Neo-MoFox 根目录运行：

```powershell
& ".\.venv\Scripts\python.exe" -m pytest plugins/grok_avatar/tests -q -p no:randomly --no-cov
```

测试覆盖清单、提示词锚点、Google 圆环像素布局、URL 构造、图片格式识别、
独立 multipart 请求契约以及 Tool schema。

## 许可证与致谢

- 本插件：AGPL-3.0
- Google 圆环算法参考：[2010384626/Google-Avatar-Frame-Generator](https://github.com/2010384626/Google-Avatar-Frame-Generator)
