# NFC（Neo-Default-Chatter）设计文档

> 全名：**Neo-Default-Chatter**，插件标识 `neo_default_chatter`，简称 NFC。
> 类型：Neo-MoFox 插件，遵循 `Neo-MoFox/AI插件编写规范.md`。
> 状态：设计阶段（未实现）。

## 1. 设计目标

NFC 是一个「会话逻辑中台」型插件。它把对话主流程抽离成一份可被复用的**主会话逻辑**，并在外层包出两个上层接口：

- **Chatter**：Neo-MoFox 标准的对话主控制器，负责对接框架的聊天流调度。
- **Service**：面向**其他插件**的会话工厂，让第三方插件无需重写对话流程，就能基于 NFC 的主会话逻辑快速构建自己的聊天器。

核心诉求：

1. 主会话逻辑独立、可被 Service 复用，不被某个具体 Chatter 绑死。
2. 消息进入主流程后，先**通过事件做预处理**：发布事件 → 拿到事件处理器返回的参数 → 据此决定「是否处理这条消息」以及「不处理的理由」。
3. 通过预处理后再交给大模型，并统一处理响应、工具调用、动作执行。
4. 默认提供三个动作：`send_text`、`pass_and_wait`、`stop_conversation`。
5. 原生多模态：图片 base64 直接进 LLM 请求体，靠**唯一占位符**让模型区分每张图，绕开框架媒体 API 的文字描述环节。

## 2. 在 Neo-MoFox 插件系统中的定位

| 维度 | 取值 |
| --- | --- |
| 插件类 | `NeoChatterPlugin(BasePlugin)`，`@register_plugin` |
| `plugin_name` | `neo_default_chatter` |
| `manifest.name` | `neo_default_chatter`（必须与 `plugin_name` 一致） |
| 入口 | `plugin.py` |
| 暴露组件 | 1 个 Chatter、1 个 Service、3 个 Action、1 个 Config |
| 对外能力 | 通过 Service `neo_default_chatter:service:chat_core` 暴露会话工厂 |
| 公开 API 依赖 | `event_api`、`llm_api`、`send_api`、`prompt_api`、`stream_api`、`log_api`、`adapter_api`、`message_api`（按需） |

依赖关系遵循规范 §8.2：组件级依赖写完整签名，例如：

```python
dependencies = ["neo_default_chatter:service:chat_core"]
```

## 3. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                       NeoChatterPlugin                        │
│                                                               │
│  ┌──────────────┐    ┌──────────────────────────────────────┐ │
│  │  NeoChatter  │    │           NeoChatterService          │ │
│  │  (Chatter)   │    │  会话工厂：create_session(...)        │ │
│  │  上层接口     │───▶│  其他插件通过它拿到主会话逻辑           │ │
│  └──────┬───────┘    └───────────────┬──────────────────────┘ │
│         │                            │                         │
│         │    二者都构造同一个           │                         │
│         │    「ConversationSession」   │                         │
│         ▼                            ▼                         │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │              ConversationSession（主会话逻辑）            │ │
│  │                                                          │ │
│  │  1. fetch_unreads → flush_unreads                        │ │
│  │  2. 预处理：publish_event("neo_default_chatter:preprocess", ...)  │ │
│  │     └─ 处理器返回 {proceed, reason, mutations}            │ │
│  │  3. proceed=False → Wait / Stop / Failure（带 reason）    │ │
│  │  4. proceed=True  → 构建 prompt（含原生多模态占位符）       │ │
│  │  5. create_request + inject_usables                      │ │
│  │  6. LLM 调用 + run_tool_call（含控制流工具拦截）          │ │
│  │  7. 解析 send_text / pass_and_wait / stop_conversation    │ │
│  │  8. yield Wait / Success / Failure / Stop                │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  默认动作（注册到全局，chatter_allow=["neo_default_chatter"]）：        │
│    • SendTextAction        send_text                         │
│    • PassAndWaitAction     pass_and_wait                      │
│    • StopConversationAction stop_conversation                 │
└──────────────────────────────────────────────────────────────┘
```

关键边界：

- **Chatter 与 Service 不各自实现一遍对话流程**。它们都构造同一个 `ConversationSession`，运行时行为完全一致；差别只在「谁来驱动这个 session」——`NeoChatter` 由框架聊天流调度驱动，第三方插件则在自己的 Chatter `execute()` 里手动驱动。
- **主会话逻辑自包含**：prompt 构建、usable 注入、工具调度、控制流拦截等能力全部由 `ConversationSession` 内部固定实现，不暴露替换点，也不通过 Protocol 反向回调宿主。
- **第三方插件复用方式**：调用 `service_api.get_service("neo_default_chatter:service:chat_core")` 拿到 Service 实例（注意：规范 §6.1.2 指出 Service 每次都新建实例，不要依赖实例级缓存），再 `create_session(stream_id=...)` 得到一个会话对象，最后在自己的 Chatter `execute()` 里 `async for` 转发其结果即可。需要自定义「是否响应」「响应前注入什么」时，通过订阅 `neo_default_chatter:preprocess` 事件实现，而不是替换运行时。

## 4. 组件清单

| 组件 | 类型 | `name` | 职责 |
| --- | --- | --- | --- |
| `NeoChatterPlugin` | Plugin | `neo_default_chatter` | 容器、生命周期、注册提示词模板 |
| `NeoChatterConfig` | Config | `config` | 单一配置节，字段见 §6 |
| `NeoChatter` | Chatter | `neo_default_chatter` | 默认 Chatter，对接框架聊天流 |
| `NeoChatterService` | Service | `chat_core` | 会话工厂，对外暴露主会话逻辑 |
| `SendTextAction` | Action | `send_text` | 发布文字（默认动作） |
| `PassAndWaitAction` | Action | `pass_and_wait` | 登记等待点（默认动作） |
| `StopConversationAction` | Action | `stop_conversation` | 结束对话并设冷却（默认动作） |

签名（规范 §1）：

```
neo_default_chatter:chatter:neo_default_chatter
neo_default_chatter:service:chat_core
neo_default_chatter:action:send_text
neo_default_chatter:action:pass_and_wait
neo_default_chatter:action:stop_conversation
neo_default_chatter:config:config
```

动作统一加：

```python
chatter_allow: list[str] = ["neo_default_chatter"]
associated_types = ["text"]
```

防止被其他 chatter 误用（规范 §4.3、`modify_llm_usables` 的过滤逻辑）。

## 5. 会话流程

### 5.1 主会话逻辑执行步骤

`ConversationSession.execute()` 是一个异步生成器，框架语义与 `BaseChatter.execute()` 一致（`yield Wait/Success/Failure/Stop`，`asend` 接收 `WaitResumeEvent`）。

1. **取未读消息**
   `fetch_unreads()` 拿到当前流的未读消息快照（不修改上下文）。
   无未读 → `yield Wait()`（等待新消息）。

2. **预处理（事件驱动）**
   发布自定义事件 `neo_default_chatter:preprocess`，参数：

   ```python
   {
       "stream_id": str,
       "chat_type": "private" | "group",
       "unreads": list[Message],          # 快照，禁止原地改
       "history_text": str,               # 已格式化的历史摘要
       "config": NeoChatterConfig,        # 只读视图
   }
   ```

   事件处理器返回 `(EventDecision, dict)`，NFC 只关心 dict 中的约定字段：

   | 字段 | 类型 | 含义 |
   | --- | --- | --- |
   | `proceed` | `bool` | 是否继续处理这条消息 |
   | `reason` | `str` | 不处理时的理由（写日志、回写 Failure.message） |
   | `mutations` | `dict[str, Any]` | 可选，对 prompt 的额外注入片段（写入 `extra`） |
   | `force_stop_minutes` | `float \| None` | 可选，要求直接进入 Stop 冷却 |

   决策语义：

   - 任意处理器返回 `EventDecision.STOP` 且 `proceed=False` → 立即终止后续处理器（规范 §4.8），NFC 据 `force_stop_minutes` 或配置默认值 `yield Stop(...)`，否则 `yield Wait()` 并带 `reason` 记日志。
   - 处理器返回 `PASS` 或 `proceed=True` → 继续往下。
   - 处理器抛异常 → 规范 §6.1.3：事件管理器会把异常转成 `PASS`，NFC **不依赖**异常来拦截，只看显式返回。

   > 事件命名遵循「插件名:事件名」的私有事件约定，不与系统 `EventType` 枚举混淆。处理器通过 `EventHandler` 组件 + `init_subscribe` 订阅，权重高者优先。

3. **flush 未读**
   预处理通过后，`flush_unreads(unread_messages)` 把这批未读搬进 history，避免读取期间新增消息被一并清掉（基类已实现）。

4. **构建 prompt**
   - system prompt：人设 + 场景引导（`private` / `group`）+ 可选负面行为约束（`reinforce_negative_behaviors`）。
   - user prompt：`history` + `unreads` + `extra`（含 `mutations` 注入）。
   - 多模态：见 §7。

5. **创建 LLM 请求并注入工具**
   `create_request(task="actor", with_reminder="actor")` + `inject_usables(request)`。
   控制流动作 `pass_and_wait` / `stop_conversation` 由主流程在 `run_tool_call` 之前**先拦截**（基类 `run_tool_call` 注释明确指出这两类应由调用方先行处理），其余工具走标准调度。

6. **解析响应**
   - 命中 `send_text` → 已由工具执行发送。
   - 命中 `pass_and_wait` → `yield Wait(time=seconds)`；`seconds=None` 时 `yield Wait()` 等待新消息。
   - 命中 `stop_conversation` → `yield Stop(time=minutes*60, direct_message_wake_enabled=..., direct_message_wake_probability=...)`。
   - 无控制流调用 → 默认 `yield Success(...)` 或 `yield Wait()` 视配置而定。

7. **恢复**
   `WaitResumeEvent.source`：
   - `"message"` → 重新进入步骤 1。
   - `"timer"` → `pass_and_wait(seconds=...)` 到期，继续后续逻辑。
   - 其他 source（如 `"sub_agent"`、`"internal_context"`、外部插件 `trigger_external_resume`）→ session 默认按 `"message"` 处理，重新进入步骤 1。

### 5.2 预处理事件协议（契约）

第三方插件要拦截 NFC 的消息，只需写一个 `EventHandler` 订阅 `neo_default_chatter:preprocess`：

```python
class MyPreprocessHandler(BaseEventHandler):
    name = "my_preprocess"
    description = "示例：拦截陌生人私聊"
    subscribe = ["neo_default_chatter:preprocess"]   # init_subscribe 用

    async def execute(self, event_name: str, params: dict) -> tuple[EventDecision, dict]:
        if params["chat_type"] == "private" and is_stranger(params["unreads"][0]):
            return EventDecision.STOP, {
                "proceed": False,
                "reason": "陌生人不响应",
            }
        return EventDecision.PASS, {"proceed": True}
```

NFC 对返回 dict 的字段做容错：缺 `proceed` 视为 `True`，缺 `reason` 视为空串。这样 PASS 型处理器可以只返回 `{}`。

## 6. 配置设计

单一 `BaseConfig` 子类 `NeoChatterConfig`，`name = "config"`。配置文件路径：

```
config/plugins/neo_default_chatter/config.toml
```

字段汇总（字段名、类型、默认值、说明）：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用 NFC |
| `native_multimodal` | bool | `false` | 原生多模态：图片 base64 直接入 LLM 请求体，跳过媒体 API 描述；启用前需确认 actor 模型支持图片输入 |
| `enable_stop_direct_message_wake` | bool | `false` | 是否允许私聊或 @Bot 消息按概率提前解除 stop 冷却 |
| `stop_direct_message_wake_probability` | float | `0.5` | 私聊或 @Bot 消息提前解除 stop 冷却的概率，范围 `0.0–1.0` |
| `reinforce_negative_behaviors` | bool | `true` | 是否在每轮 user 提示词的 extra 板块中再次强调负面行为约束 |
| `theme_guide.private` | str | 见下 | 私聊场景的额外提示词 |
| `theme_guide.group` | str | 见下 | 群聊场景的额外提示词 |
| `default_stop_minutes` | float | `5.0` | `stop_conversation` 未传参时的默认冷却分钟数 |
| `actor_task_name` | str | `"actor"` | 主会话 LLM 任务名，对应 `config/model.toml` 的 task key |

### 6.1 场景引导默认值

`theme_guide.private` 默认值：

```
你当前正处于"私聊"环境中。私聊是私密的、一对一的，也常常伴随更强烈、更直接的情感互动。在这种场景下，真正重要的不是表面上的温柔或热情，而是你是否真正理解对方是谁、你与对方究竟处于怎样的关系之中。请结合记忆、历史对话与当前语境，认真判断彼此的熟悉程度、信任基础、互动边界与情感状态。你需要保持独立思考，不要轻易被对方的话术、情绪或预设节奏牵着走，更不能在毫无判断的情况下陷入对方设定好的关系框架或表达陷阱。但这并不意味着你要始终冷淡、疏离或处处防备。对陌生人过分亲昵、过度投入，或对本就重要、亲近、值得认真对待的人表现得过于戒备、冷漠、敷衍，都是失衡且愚蠢的。关系会随着情感与互动而变化，它是真实存在的，也是需要被理解、经营与珍惜的。请把关系判断放在私聊回应的核心位置，在清醒、稳重与真诚之间取得平衡，严肃对待对方，也严肃对待你们之间正在形成或已经存在的关系。
```

`theme_guide.group` 默认值：

```
你当前正处于"群聊"环境中。群聊里通常同时有很多活跃用户，而你只是其中的一员，不是唯一的中心，也不该默认自己随时都必须发言。请时刻注意多人对话的整体节奏、当前话题的流向，以及别人是否真的在和你互动。每次你想插话、接梗、跟风、冒泡、整活或表达观点之前，都先判断你的介入是否自然，是否会打断气氛，是否可能引起他人的不满、尴尬或反感。当你决定参与互动时，就认真地参与，拿出真实的互动感，而不是爱答不理、敷衍应付，也不要过度热情、强行活跃、唠唠叨叨、喧宾夺主。你应当像一个正常群友那样去说话和相处，既能在合适的时候接住话题、顺势玩梗、自然回应，也懂得在不适合的时候克制表达、不过度刷存在感。请在热情、分寸与互动感之间找到恰到好处的平衡，让你的出现显得自然、舒服、有参与感，而不是突兀、冷场或打扰。
```

### 6.2 配置 TOML 示例

```toml
[plugin]
enabled = true
native_multimodal = true
enable_stop_direct_message_wake = true
stop_direct_message_wake_probability = 0.5
reinforce_negative_behaviors = true
default_stop_minutes = 5.0
actor_task_name = "actor"

[plugin.theme_guide]
private = ""
group = "你当前正处于\"群聊\"环境中。群聊里同时有很多活跃用户，你是其中的一员，不是唯一的中心，所以不需要每条消息都接话。但你也别把自己当旁观者——遇到有意思的话题、好玩的梗、或者你确实有话想说的时候，自然地接上就行，不用每次都纠结该不该开口。判断介入是否合适靠直觉就好：话题正热、你在旁边看着也想笑、有人说了句你接得上来的话，这些时候插一句很正常。真正需要收着的是：别人在聊很私人的事、话题已经冷场、或者你刚发过言还没几条消息。参与的时候就认真参与，拿出真实的互动感，别敷衍；但也别一个人刷屏、别喧宾夺主。像正常群友那样，该接话接话，该安静安静。"
```

> 上例中 `private = ""`、`group` 覆盖为更短文案，演示用户自定义场景；注释里的「默认值」仍以 §6 表为准。

### 6.3 Config 类骨架

```python
from typing import ClassVar
from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class NeoChatterConfig(BaseConfig):
    """Neo-Default-Chatter 配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "Neo-Default-Chatter 配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """主配置节。"""

        @config_section("theme_guide", title="场景引导", tag="text")
        class ThemeGuideSection(SectionBase):
            """按聊天类型区分的额外提示词。"""

            private: str = Field(default=<见 §6.1>, description="私聊场景的额外提示词",
                                 input_type="textarea", rows=3, tag="text")
            group: str = Field(default=<见 §6.1>, description="群聊场景的额外提示词",
                               input_type="textarea", rows=3, tag="text")

        enabled: bool = Field(default=True, description="是否启用 Neo-Default-Chatter", tag="plugin")
        native_multimodal: bool = Field(default=False, description="原生多模态：图片 base64 直接进 LLM 请求体", tag="ai")
        enable_stop_direct_message_wake: bool = Field(default=False, description="是否允许私聊或 @Bot 消息按概率提前解除 stop 冷却", tag="performance")
        stop_direct_message_wake_probability: float = Field(default=0.5, description="私聊或 @Bot 消息提前解除 stop 冷却的概率", tag="performance")
        reinforce_negative_behaviors: bool = Field(default=True, description="是否在每轮 user 提示词的 extra 板块中再次强调负面行为约束", tag="ai")
        default_stop_minutes: float = Field(default=5.0, description="stop_conversation 未传参时的默认冷却分钟数", tag="ai")
        actor_task_name: str = Field(default="actor", description="主会话 LLM 任务名", tag="ai")

        theme_guide: "NeoChatterConfig.PluginSection.ThemeGuideSection" = Field(
            default_factory=lambda: NeoChatterConfig.PluginSection.ThemeGuideSection(),
            description="按聊天类型区分的额外提示词",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
```

## 7. 原生多模态（native_multimodal）

### 7.1 设计目标

启用 `native_multimodal` 后，NFC **不调用** `media_api` 把图片转成文字描述，而是：

1. 从未读消息中提取所有图片段。
2. 框架 converter 已为每条消息生成带 `media_id`（图片 SHA256 哈希）的占位符：
   - `[图片(media_id):description]` — VLM 识别成功，带描述
   - `[图片(media_id)]` — VLM 跳过/早退，无描述
3. 在 user prompt 的文本里，通过 `media_id` 精确关联每条消息的图片，把占位符替换为内部标记
   `[[NDFC_IMAGE:media_id]]`，再按 `media_id` 在消息 media 列表中精确查找 base64 并内联。
4. 在 LLM 请求体里，生成 Text/Image 交替排列的 content 列表——每张图物理插入到其所属
   消息文本之后，模型既能看到文本标记，也能通过邻接关系理解图片归属。

### 7.2 占位符与请求体对应关系

```
文本侧（USER payload 的 Text/Image 交替部分）：
  Text("【10:23】小明 [msg_123]：你看这张图 [图片(abc123)]")
  Image(base64=img1_b64)   # media_id == abc123
  Text("【10:24】小红 [msg_124]：还有这张 [图片(def456)]")
  Image(base64=img2_b64)   # media_id == def456
```

实现上沿用 `default_chatter` 已验证的 media_id 精确关联思路
（`tokenize_message_scoped_image_placeholders` + `inline_message_images_into_text`，
见 `plugins/default_chatter/utils/multimodal.py`），以图片哈希而非全局顺序定位，
避免历史消息与未读消息占位符混合时的错位问题。

### 7.3 关闭时的行为

`native_multimodal = false`（默认）时，图片仍走框架媒体 API 生成描述，user prompt 只包含纯文本。这是为了兼容不支持视觉输入的 actor 模型，避免无谓的 token 浪费。

### 7.4 实现注意

- 占位符以 `media_id`（图片 SHA256 哈希）为唯一标识：`[图片(media_id)]` / `[图片(media_id):description]`。
  `media_id` 是图片内容的哈希，天然全局唯一且跨请求稳定，不会因消息顺序变化而错位。
- 描述在 tokenize 阶段**丢弃**：native_multimodal 下图片以 `Image(base64)` 形式直接传给 LLM，
  文本侧不再需要 VLM 描述，避免冗余 token。
- 找不到对应图片时（如历史消息占位符在本轮未读中无对应媒体）：还原为 `[图片(media_id)]` 纯文本，
  不暴露内部标记给 LLM。
- 表情包图片**不进原生多模态**：与 `default_chatter` 一致，表情包仍走 VLM 识别以利用哈希缓存，
  避免每次都重算。

## 8. stop 冷却与直接唤醒

### 8.1 stop_conversation 工具

```python
class StopConversationAction(BaseAction):
    name = "stop_conversation"
    description = "结束当前对话，过一段时间后再允许开启新对话。..."
    chatter_allow: list[str] = ["neo_default_chatter"]
    associated_types = ["text"]

    async def execute(self, minutes: float | None = None) -> tuple[bool, str]:
        minutes = minutes if minutes is not None else <config.default_stop_minutes>
        return True, f"对话已结束，将在 {minutes} 分钟后允许新对话"
```

NFC 主流程在拦截到该工具调用时，构造 `Stop` 结果：

```python
yield Stop(
    time=minutes * 60,
    direct_message_wake_enabled=config.plugin.enable_stop_direct_message_wake,
    direct_message_wake_probability=config.plugin.stop_direct_message_wake_probability,
)
```

`Stop` 数据类的 `direct_message_wake_enabled` / `direct_message_wake_probability` 字段已由 `BaseChatter.Stop` 提供（见 `src/core/components/base/chatter.py:105`），框架据此在冷却期内对私聊或 @Bot 消息按概率提前解除冷却。

### 8.2 唤醒概率语义

- `enable_stop_direct_message_wake = false`（默认）：冷却期内任何消息都不提前解除，必须等 `time` 到期。
- `enable_stop_direct_message_wake = true`：冷却期内收到**私聊**或 **@Bot** 消息时，以 `stop_direct_message_wake_probability` 的概率提前解除冷却并重启 chatter。
- 概率范围为 `0.0–1.0`：`0.0` 等价于关闭，`1.0` 等价于必定唤醒。

> 普通群聊消息（非 @Bot）**不**触发提前唤醒，避免冷却被刷屏消息绕过。

## 9. 默认动作契约

### 9.1 send_text

```python
class SendTextAction(BaseAction):
    name = "send_text"
    description = "发送一段文本消息给用户。这是你唯一发送文本消息的方式。..."
    chatter_allow: list[str] = ["neo_default_chatter"]
    associated_types = ["text"]

    async def execute(
        self,
        content: str,
        reply_to: str | None = None,
        at: str | None = None,
    ) -> AsyncGenerator[tuple[bool, str] | None, None]:
        ...
```

行为参考 `default_chatter` 的 `SendTextAction`（`plugins/default_chatter/components/actions/send_text.py`）：

- 支持纯文本、引用回复（`reply_to`）、@ 用户（`at`）。
- 打字延迟模拟（仅非首条消息生效）。
- `content` 仅含正文，禁止混入理由/独白/格式说明。
- 发送成功后记录发送时间，供下一轮打字延迟计算。

### 9.2 pass_and_wait

```python
class PassAndWaitAction(BaseAction):
    name = "pass_and_wait"
    description = "为当前对话登记一个等待点。..."
    chatter_allow: list[str] = ["neo_default_chatter"]
    associated_types = ["text"]

    async def execute(self, seconds: float | None = None) -> tuple[bool, str]:
        if seconds is None:
            return True, "已登记等待，将在本轮动作完成后等待新消息"
        return True, f"已登记等待，将在本轮动作完成后等待 {seconds} 秒再继续对话"
```

语义：

- `seconds=None`（默认）→ `yield Wait()`：等待新消息到来再继续。
- `seconds=<数字>` → `yield Wait(time=seconds)`：到时由框架主动恢复（`WaitResumeEvent.source == "timer"`），不依赖新消息。
- 可与 `send_text` 同轮组合：先发文本，再登记等待，表示「发完就等」。

### 9.3 stop_conversation

见 §8.1。

### 9.4 控制流拦截

主流程在调用 `run_tool_call` 前，先扫描 `calls`：

- 命中 `pass_and_wait` → 取 `seconds`，标记本轮结束后 `yield Wait(time=seconds)`，**不**把该 call 写回 `TOOL_RESULT`（避免模型继续 follow-up）。
- 命中 `stop_conversation` → 取 `minutes`，标记 `yield Stop(...)`，同样不写回。
- 其余 call 交给 `run_tool_call` 正常执行。

这与 `BaseChatter.run_tool_call` 注释里「控制流工具应由调用方先行处理」的约定一致。

## 10. 目录结构

遵循「组件单放 `components/`、工具类放 `utils/`」的组织方式（规范 §6 推荐模式的变体）：

```
plugins/neo_default_chatter/
├── manifest.json
├── __init__.py
├── plugin.py                 # NeoChatterPlugin（仅插件类）
├── session.py                # ConversationSession（主会话逻辑，自包含）
├── components/               # 所有注册组件统一放这里
│   ├── __init__.py
│   ├── chatter.py            # NeoChatter（Chatter）
│   ├── config.py             # NeoChatterConfig（Config）
│   ├── service.py            # NeoChatterService（Service）
│   └── actions/              # Action 组件子目录
│       ├── __init__.py
│       ├── send_text.py      # SendTextAction
│       └── control.py        # PassAndWaitAction + StopConversationAction
└── utils/                    # 工具类（非注册组件）
    ├── __init__.py
    ├── preprocess.py         # 预处理事件发布与决策合并
    ├── multimodal.py         # 原生多模态：图片提取 + 占位符内联
    ├── prompt_builder.py     # system/user prompt 构建
    └── prompts.py            # 提示词模板（注册到 prompt_manager）
```

> `session.py` 放根目录：它是主会话逻辑的载体，不是 `BaseComponent` 子类（不进注册表），与 `plugin.py` 同级便于定位。`components/` 只放真正被插件注册的组件类。

导入全部使用相对导入（规范 §2.5），例如：

```python
# plugin.py 内
from .components.chatter import NeoChatter
from .components.config import NeoChatterConfig
from .components.service import NeoChatterService
from .components.actions import (
    PassAndWaitAction,
    SendTextAction,
    StopConversationAction,
)
from .session import ConversationSession

# components/chatter.py 内
from ..session import ConversationSession
from ..components.service import NeoChatterService

# session.py 内（如需 prompt/多模态工具）
from .utils.preprocess import run_preprocess
from .utils.multimodal import inline_message_images_into_text
from .utils.prompt_builder import build_system_prompt, build_user_prompt
```

## 11. manifest.json

```json
{
  "name": "neo_default_chatter",
  "version": "0.1.0",
  "description": "Neo-Default-Chatter：可复用的会话逻辑中台，事件驱动预处理 + 原生多模态",
  "author": "MoFox Team",
  "dependencies": {
    "plugins": [],
    "components": []
  },
  "include": [
    {"component_type": "chatter", "component_name": "neo_default_chatter", "dependencies": [], "enabled": true},
    {"component_type": "service", "component_name": "chat_core", "dependencies": [], "enabled": true},
    {"component_type": "action", "component_name": "send_text", "dependencies": [], "enabled": true},
    {"component_type": "action", "component_name": "pass_and_wait", "dependencies": [], "enabled": true},
    {"component_type": "action", "component_name": "stop_conversation", "dependencies": [], "enabled": true}
  ],
  "entry_point": "plugin.py",
  "api_version": {
    "event_api": "1.0.0",
    "llm_api": "1.0.0",
    "send_api": "1.0.0",
    "prompt_api": "1.0.0",
    "stream_api": "1.0.0",
    "log_api": "1.0.0",
    "adapter_api": "1.0.0",
    "message_api": "1.0.0",
    "service_api": "1.0.0"
  },
  "python_dependencies": [],
  "dependencies_required": true
}
```

注意（规范 §5.1.5）：

- `include` 必须手工维护，与 `get_components()` 返回保持一致。
- `include[].enabled` 不会自动禁用组件，要禁用需同时改 `get_components()`。

## 12. 关键文件骨架

### 12.1 `components/chatter.py`

```python
from __future__ import annotations

from typing import AsyncGenerator

from src.app.plugin_system.base import (
    BaseChatter, Failure, Stop, Success, Wait, WaitResumeEvent,
)
from src.app.plugin_system.types import ChatType

from ..session import ConversationSession
from ..components.service import NeoChatterService


class NeoChatter(BaseChatter):
    """Neo-Default-Chatter 默认聊天器，委托主会话逻辑执行。"""

    name: str = "neo_default_chatter"
    description: str = "可复用的会话逻辑中台，事件驱动预处理 + 原生多模态"
    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL
    dependencies: list[str] = []

    async def execute(
        self,
    ) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]:
        """创建主会话并转发其结果。"""
        service = NeoChatterService(self.plugin)
        session = service.create_session(stream_id=self.stream_id, plugin=self.plugin)
        runner = session.execute()
        resume: WaitResumeEvent | None = None
        while True:
            try:
                result = await runner.asend(resume)
            except StopAsyncIteration:
                return
            resume = yield result
```

### 12.2 `plugin.py`

```python
from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin

from .components.chatter import NeoChatter
from .components.config import NeoChatterConfig
from .components.service import NeoChatterService
from .components.actions import (
    PassAndWaitAction,
    SendTextAction,
    StopConversationAction,
)


@register_plugin
class NeoChatterPlugin(BasePlugin):
    """Neo-Default-Chatter 插件。"""

    plugin_name = "neo_default_chatter"
    configs = [NeoChatterConfig]

    async def on_plugin_loaded(self) -> None:
        """注册提示词模板。"""
        # get_prompt_manager().get_or_create(...) 详见 utils/prompts.py

    def get_components(self) -> list[type]:
        """返回插件组件类。"""
        return [
            NeoChatter,
            NeoChatterService,
            SendTextAction,
            PassAndWaitAction,
            StopConversationAction,
        ]
```

## 13. Service 对外契约

`NeoChatterService` 暴露给第三方插件的入口：

```python
class NeoChatterService(BaseService):
    name = "chat_core"
    description = "Neo-Default-Chatter 会话工厂，复用主会话逻辑构建自定义聊天器"
    version = "0.1.0"

    def create_session(
        self,
        *,
        stream_id: str,
        plugin: BasePlugin | None = None,
    ) -> ConversationSession:
        """创建一个由 NFC 主会话逻辑驱动的会话。

        Args:
            stream_id: 目标聊天流 ID。
            plugin: 可选，传入调用方插件实例，便于 session 内部
                通过 ``self.plugin`` 拿到配置/日志等基础能力；
                为 None 时回退到 NFC 自己的插件实例。

        Returns:
            ConversationSession: 可直接 ``async for`` 的会话对象。
        """
```

> 不提供 `adapters` 参数，也不暴露运行时替换点。第三方插件拿到的 session 行为与 `NeoChatter` 自驱动的 session 完全一致；需要差异化「是否响应」「响应前注入什么」时，通过订阅 `neo_default_chatter:preprocess` 事件实现。

第三方插件复用示例（在自己的 Chatter `execute()` 里转发）：

```python
from src.app.plugin_system.api.service_api import get_service

class MyChatter(BaseChatter):
    name = "my_chatter"
    ...

    async def execute(self):
        service = get_service("neo_default_chatter:service:chat_core")  # 每次新建实例
        session = service.create_session(stream_id=self.stream_id, plugin=self.plugin)
        resume = None
        async for result in session.execute():
            resume = yield result
            # 如需把 WaitResumeEvent 回传给 session：
            # await session.asend(resume)  ——见下文“驱动协议”
```

驱动协议：

- `ConversationSession.execute()` 是 `AsyncGenerator[Wait|Success|Failure|Stop, WaitResumeEvent | None]`，与 `BaseChatter.execute()` 完全一致。
- 第三方 Chatter 用 `async for` 消费结果，并通过 `asend` 把框架送来的 `WaitResumeEvent` 回传给 session（典型写法见 §12 `NeoChatter.execute` 的 `runner.asend(resume)` 模式）。
- session 内部不持有任何「宿主 chatter」引用，所有上下文（未读消息、历史、平台信息）都从 `stream_id` 经 `stream_api` 取得，保证可被任意插件驱动。

> 规范 §6.1.2 提醒：Service 不是单例，`get_service` 每次返回新实例。NFC 不在 Service 实例字段上放共享状态，所有跨轮状态走 `chat_stream.context` 或持久化存储。

## 14. 与 default_chatter 的关系

| 维度 | default_chatter | neo_default_chatter (NFC) |
| --- | --- | --- |
| 定位 | 框架自带默认聊天器，功能自洽 | 会话逻辑中台，强调可复用与可扩展 |
| 预处理 | sub-agent + 兴趣值过滤（内置策略） | **事件驱动**：发布 `neo_default_chatter:preprocess`，策略由外部 EventHandler 提供 |
| 多模态 | `native_multimodal` 已支持，占位符固定 | `native_multimodal` + **可配置占位符模板** |
| 子代理协作 | 内建 sub-agent / 兴趣值 / 语义训练 | 本期不内建，预留 EventHandler 扩展点 |
| 对外复用 | Service `chat_core` 可创建会话，但适配器耦合 DefaultChatter 运行时 | Service 仅暴露 `create_session(stream_id)`，session 行为固定且自包含，第三方插件直接 `async for` 转发即可 |
| 默认动作 | send_text / pass_and_wait / stop_conversation | 相同三者，`chatter_allow=["neo_default_chatter"]` |

NFC 不取代 default_chatter，而是提供一个**更瘦、更可复用**的会话骨架，让需要自定义对话流程的插件不必从零写 Chatter。

## 15. 实现路线

1. **骨架**：`manifest.json`、`plugin.py`、`components/`（chatter/config/service/actions）、`session.py`、`utils/`，能加载、能空跑 `execute()` 返回 `Wait()`。
2. **默认动作**：`components/actions/` 下 `send_text` / `pass_and_wait` / `stop_conversation`，参考 default_chatter 实现，`chatter_allow=["neo_default_chatter"]`。
3. **预处理事件**：`utils/preprocess.py` 发布 `neo_default_chatter:preprocess`，合并处理器返回的 `proceed`/`reason`/`mutations`/`force_stop_minutes`。
4. **原生多模态**：`utils/multimodal.py` 提取图片 + 占位符内联，`native_multimodal=true` 时进 LLM 请求体。
5. **stop 直接唤醒**：`Stop` 携带 `direct_message_wake_enabled` / `direct_message_wake_probability`，由框架消费。
6. **prompt 模板**：`utils/prompts.py` + `utils/prompt_builder.py`，注册到 `prompt_manager`，支持 `theme_guide` 与 `reinforce_negative_behaviors`。
7. **Service 对外契约**：`create_session(stream_id, plugin)` 返回自包含 `ConversationSession`，验证第三方插件经 Service 复用主会话逻辑（无需任何适配器）。
8. **测试**：按规范 §8.7 补单测，覆盖预处理决策合并、占位符唯一性、控制流拦截、stop 唤醒概率边界、第三方驱动转发结果一致性。

## 16. 生成前自检（对照规范 §11）

- [x] 公开入口：`src.app.plugin_system.base/api/types`
- [x] `@register_plugin` + `plugin_name/description/version`
- [x] `get_components()` 返回类
- [x] 配置类放 `configs`
- [x] 组件统一属性 `name`，与 manifest `component_name` 一致
- [x] Tool/Action/Agent/Chatter 签名与返回值匹配基类
- [x] `dependencies` 写完整签名
- [x] Action 与 Tool 不混淆（三个默认动作都是 Action）
- [x] Tool/Service 不假设 `chat_stream`（动作是 Action，有注入）
- [x] 后台任务走 `task_manager`（本期无后台任务）
- [x] 文档字符串、类型注解、测试
- [x] `manifest.name == plugin_name`
- [x] 不依赖 `manifest.include[].enabled` 自动禁用
- [x] Service 不依赖单例语义
- [x] system reminder：`create_request(with_reminder="actor")` 显式启用
