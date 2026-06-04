# 18. Adapter：插件怎样真正接上外部平台

> **导读** 本章介绍 Adapter——插件与外部聊天平台之间的桥接层。Adapter 的核心职责是双向翻译：把平台原始消息转成统一的 `MessageEnvelope`，把核心的出站消息翻译回平台协议。本章将解释 Adapter 与 mofox-wire 的层次关系、`from_platform_message()` 的工作原理、生命周期钩子的实用意义，以及第一次写 Adapter 应该怎样控制复杂度。最后提供 `BaseAdapter` 基类速查。

前面几章，我们一直在插件系统的“核心里面”走：

- Tool 提供能力
- Agent 做局部编排
- Action 执行动作
- EventHandler 在系统事件点介入
- Chatter 组织整轮对话
- Router 提供 HTTP 入口

到了 Adapter，方向要再拐一次。

因为 Adapter 解决的问题不是“这轮对话怎么组织”，也不是“插件怎样暴露一个接口”，而是更底层、更靠外的一件事：

> **Bot 到底怎样和外部聊天平台接上线。**

你可以把它理解成平台桥梁。

外部平台发来原始事件，Adapter 把它翻译成核心能理解的统一消息；
核心想把消息发回平台，Adapter 再把统一消息翻译回平台自己的格式。

所以这一章最重要的目标，不是把你一下带进 WebSocket、协议细节和平台 SDK 的深水区，而是先让你真正建立一个稳定心智模型：

> **Adapter 的职责，是做“平台协议”和“核心消息模型”之间的双向翻译。**

这一章按你定的方向来：

- 先讲一个最小自定义 Adapter 应该理解什么
- 再单独讲清楚它和 mofox-wire 的关系
- 最后再提醒你，真实平台接入为什么会比最小示例复杂很多

## 18.1 先把一句话记住：Adapter 不是聊天逻辑，它是平台桥

很多人第一次接触 Adapter，会下意识把它理解成：

- “一个专门负责收发消息的插件组件”

这句话不算错，但还不够准确。

更准确一点的说法应该是：

> **Adapter 不是在写聊天逻辑，而是在做协议翻译和平台接入。**

这意味着它最关心的不是：

- 模型这轮要不要回复
- Tool 要不要调用
- 当前会话上下文该怎么拼

它更关心的是：

- 平台原始事件长什么样
- 这条事件怎样转换成统一的消息信封
- 核心发回来的统一消息怎样再变成平台可发送的数据
- 平台连接是否健康
- 平台连接断了以后要不要重连

所以从分工上看，Adapter 和 Chatter / Action / Router 都不是一层东西。

## 18.2 Adapter 和别的组件，边界到底怎么分

这一章非常值得先把边界立住。

### Adapter 和 Chatter 的区别

Chatter 负责的是：

- 这一轮对话怎么组织
- 要不要响应
- 用哪些 usable

Adapter 负责的是：

- 外部平台的原始数据怎么进来
- 核心消息怎么发出去

所以 Chatter 是对话总控，Adapter 是平台接入层。

### Adapter 和 Action 的区别

Action 负责的是：

- 在当前聊天流里执行一个动作
- 产生命令、副作用、发送行为

Adapter 负责的是：

- 真正把“要发出去的统一消息”变成平台能认的格式

也就是说，Action 更像“我要做什么”，Adapter 更像“怎么把它送到平台上”。

### Adapter 和 Router 的区别

Router 面向 HTTP 请求。

Adapter 面向外部聊天平台的消息流。

如果 Router 是“插件的 HTTP 入口”，那 Adapter 更像“Bot 的平台入口”。

## 18.3 这层为什么会同时牵到 Neo-MoFox 和 mofox-wire

这一点如果不先讲清楚，后面很容易越写越糊。

当前实现里，Neo-MoFox 的 `BaseAdapter` 不是凭空定义的一套协议。

它是建立在 mofox-wire 的 `AdapterBase` 之上的。

你可以把两层关系先粗略理解成这样：

- mofox-wire 提供统一消息信封、CoreSink、基础适配器传输抽象
- Neo-MoFox 的 `BaseAdapter` 在上面补了插件生命周期、健康检查、重连、插件实例注入这些运行时能力

所以插件作者写 Adapter 时，实际上是在同时面对两层约束：

1. **你要满足 Neo-MoFox 这一层的插件组件规范**
2. **你也要产出 mofox-wire 这一层能理解的消息结构**

这一点非常重要。

因为这意味着你写 Adapter 时，不能只想着“把平台消息变成某个内部对象”；
你真正要交出来的，是一个统一的 `MessageEnvelope`。

## 18.4 什么是 `MessageEnvelope`，为什么它是 Adapter 的核心

如果只记 Adapter 里的一个关键词，那几乎就是它：

> **`MessageEnvelope` 是 Adapter 和核心之间交换消息的统一信封。**

你不用一开始就记住它所有字段，但至少要先记住三个最重要的部分：

### `direction`

表示这条信封的方向。

在 Adapter 这一层，最常见的是：

- `incoming`：平台发到核心
- `outgoing`：核心准备发回平台

### `message_info`

这是消息的元信息。

通常至少会带：

- `platform`
- `message_id`
- 时间信息
- 用户信息 / 群信息

### `message_segment`

这是消息内容本体。

它不是简单的“一段字符串”，而是一段或一组分段结构，比如：

- text
- image
- emoji
- reply
- at

也就是说，Adapter 接的不是“纯文本聊天程序”的接口，而是一个更通用的消息段模型。

这就是为什么前面我一直强调：

> **Adapter 不是简单收发字符串，而是在收发统一的结构化消息。**

## 18.5 先看运行时链路：消息到底怎么经过 Adapter

这一段建议你别死记细节，先记流向。

### 入站：平台 -> Adapter -> 核心

大致流程可以先理解成：

1. 平台发来原始事件
2. Adapter 的 `from_platform_message()` 把原始事件转换成 `incoming MessageEnvelope`
3. Adapter 通过 `core_sink.send(...)` 把这条信封送进核心
4. `MessageReceiver` 收到信封，再继续把它转成核心业务里的 `Message`

也就是说，Adapter 负责的是“把平台原始数据翻译到核心入口前一站”。

### 出站：核心 -> Adapter -> 平台

另一边，大致可以先理解成：

1. 核心想发送一条消息
2. 业务消息先被转换成 `outgoing MessageEnvelope`
3. 系统找到对应平台的 Adapter
4. Adapter 的 `_send_platform_message()` 把信封翻译成平台格式并真正发出去

这条路径里，插件作者最需要关注的仍然是最后一步：

> **你的 Adapter 要知道怎样把统一信封重新翻译回平台协议。**

### 一个容易混淆的点：CoreSink 不等于“所有出站消息都靠它走”

这里有一个容易混淆的点值得单独说明。

在 mofox-wire 抽象里，`CoreSink` 是一个双向协议，既能把消息送进核心，也能接收核心推回来的 outgoing 信封。

但在 Neo-MoFox 当前实现里，新手最应该先抓住的出站主路径，其实还是：

- 核心消息
- 转成 `MessageEnvelope`
- 找到目标 Adapter
- 调用 Adapter 的 `_send_platform_message()`

也就是说，先把 `_send_platform_message()` 理解扎实，比先钻 `push_outgoing()` 更重要。

后者更多是自动传输和更底层桥接时会看到的能力。

## 18.6 当前实现里，插件作者真正要写的是什么

如果把运行时细节先压住，对插件作者来说，真正要写的其实没有那么多。

通常最核心的是这几件事：

1. 声明 Adapter 元数据
2. 实现 `from_platform_message()`
3. 实现 `_send_platform_message()`
4. 实现 `get_bot_info()`
5. 视情况重写 `on_adapter_loaded()`、`on_adapter_unloaded()`、`health_check()`、`reconnect()`

也就是说，最小可理解版本的 Adapter，本质上就是一对转换函数，再加一点生命周期逻辑。

## 18.7 一个最小自定义 Adapter，先长成什么样

这一章我们不直接上 OneBot 那种真实平台接入版本。

不是因为它不重要，而是因为它一下会把下面这些东西全堆到你脸上：

- WebSocket 连接
- 平台事件类型分流
- 平台 API 调用
- 鉴权
- 响应池
- 命令往返

第一次学 Adapter，我更建议你先看一个“翻译边界很清楚”的最小例子。

下面这个例子假设平台给你的原始事件就是一个 Python 字典，里面至少有：

- `message_id`
- `user_id`
- `text`

我们先不管真实平台怎么连，只看 Adapter 怎么完成“双向翻译”。

```python
from __future__ import annotations

from typing import Any

from mofox_wire import MessageEnvelope

from src.app.plugin_system.base import BaseAdapter


class DemoBridgeAdapter(BaseAdapter):
    """一个只演示双向翻译边界的最小 Adapter。"""

    adapter_name = "demo_bridge"
    adapter_version = "0.1.0"
    adapter_description = "演示平台原始数据与 MessageEnvelope 之间的互转"
    platform = "demo"

    async def from_platform_message(self, raw: dict[str, Any]) -> MessageEnvelope:
        """把平台原始事件转换成统一消息信封。"""
        text = str(raw.get("text", ""))
        user_id = str(raw.get("user_id", "unknown_user"))
        message_id = str(raw.get("message_id", "unknown_message"))

        return {
            "direction": "incoming",
            "message_info": {
                "platform": self.platform,
                "message_id": message_id,
                "user_info": {
                    "platform": self.platform,
                    "user_id": user_id,
                },
            },
            "message_segment": {
                "type": "text",
                "data": text,
            },
            "raw_message": raw,
        }

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """把统一消息信封重新翻译成平台要发送的格式。"""
        segment = envelope.get("message_segment")

        if isinstance(segment, list):
            text = "".join(
                str(item.get("data", ""))
                for item in segment
                if isinstance(item, dict) and item.get("type") == "text"
            )
        elif isinstance(segment, dict) and segment.get("type") == "text":
            text = str(segment.get("data", ""))
        else:
            text = "[暂不支持的消息类型]"

        # 这里先用打印代替真实平台发送。
        print({"platform": self.platform, "text": text})

    async def get_bot_info(self) -> dict[str, Any]:
        """返回这个平台上的 Bot 身份信息。"""
        return {
            "bot_id": "demo-bot",
            "bot_nickname": "Demo Bot",
            "platform": self.platform,
        }
```

这个例子很轻，但它已经把 Adapter 最核心的边界讲清楚了：

- 入站时，把原始事件翻成 `incoming MessageEnvelope`
- 出站时，把统一信封翻回平台格式

如果你第一次写 Adapter，只要先把这个边界写稳，后面再接真实平台时就不会那么乱。

## 18.8 这个最小例子到底说明了什么

它主要说明三件事。

### 第一，Adapter 的关键不是“连上平台”，而是“翻译正确”

很多人第一次写适配器，会本能地把注意力全放在：

- WebSocket 怎么连
- HTTP 怎么收
- SDK 怎么调

这些当然都重要。

但如果你的双向翻译边界本身就没有立住，那么即使连上了平台，后面也还是会越来越乱。

所以第一次写时，先确保：

- 入站转换稳定
- 出站转换稳定
- 元信息别乱丢

这比先追求“把网络连通”更重要。

### 第二，`raw_message` 很有用，不要急着丢

最小例子里把原始平台数据塞回了 `raw_message`。

这是个非常实用的习惯。

因为真实项目里你后面经常会遇到这种情况：

- 核心业务层暂时只用了统一字段
- 但某个事件处理、调试逻辑、平台专属功能还需要原始平台数据

这时候保留 `raw_message`，会比你以后再想办法把平台字段找回来轻松得多。

### 第三，最小版本不必一次支持所有消息段

你当然最终会碰到：

- image
- reply
- at
- file
- voice
- 各平台自定义事件

但第一次写，不需要一口气全支持。

更合理的顺序通常是：

1. 先把 text 跑通
2. 再补 reply / at 这种常见结构
3. 最后再逐步补媒体和平台专属事件

这样你更容易知道问题出在哪一层。

## 18.9 生命周期钩子在 Adapter 里比别的组件更实用

对很多组件来说，生命周期钩子有时像“可有可无的扩展点”。

但在 Adapter 里，它们往往很实用。

### `on_adapter_loaded()`

适合做：

- 启动前校验配置
- 初始化连接对象
- 创建平台客户端

### `on_adapter_unloaded()`

适合做：

- 关闭连接
- 清理响应池
- 释放平台资源

### `health_check()` 和 `reconnect()`

这两个方法更像是“平台接入层”的日常维护口。

因为 Adapter 很可能长期持有：

- WebSocket 连接
- 长轮询状态
- 平台 SDK 会话

所以健康检查和重连，在这一层不是点缀，而是常见需求。

## 18.10 一个需要提前知道的实现接缝：别在 `__init__` 里过度依赖 `core_sink`

这里有一个很实际的实现细节值得提前说明。

从基类签名看，`BaseAdapter` 初始化时会接收 `core_sink`。

但当前运行时的启动过程里，Adapter 实例可能先被创建，之后再由 `SinkManager` 补上真正可用的 `CoreSink`。

这意味着对插件作者来说，更稳妥的写法是：

- 不要在 `__init__` 里假定 `core_sink` 已经完全可用
- 如果你要依赖它，尽量放到启动后逻辑里再使用

这不是说它一定会出问题，而是说：

> **把 `core_sink` 当成“启动阶段保证可用”，会比“构造阶段保证可用”更稳。**

写文档时我特意绕开了“在构造函数里立刻用 sink 做事”的示例，也是因为这个原因。

## 18.11 为什么真实平台 Adapter 会明显更复杂

如果你去看现有的 `onebot_adapter`，会发现它和刚才那个最小例子完全不是一个体量。

这很正常。

因为一旦进入真实平台接入，马上就会多出很多现实问题：

- 平台原始事件不是一类，而是很多类
- 连接可能断开
- 出站通常不是“直接发一段文本”，而是调用平台 API
- 平台还会有 notice、request、meta_event 这类非普通消息
- 有些平台会有命令响应、echo、回调配对之类的额外机制

所以真实 Adapter 往往不只是一个“翻译器”，而更像：

- 一个平台协议入口
- 一个消息事件分流器
- 一个平台 API 客户端

也正因为如此，我才建议你第一次不要直接照着生产级 Adapter 抄。

先把“统一信封和双向翻译”理解扎实，后面再去读真实实现，吸收速度会快很多。

## 18.12 如果以后要接 WebSocket / HTTP 传输，这和 mofox-wire 又是什么关系

这一节只讲到你现在需要知道的程度，不展开太深。

mofox-wire 除了定义 `MessageEnvelope`，还提供了 Adapter 的基础传输抽象，比如：

- `CoreSink`
- `AdapterBase`
- WebSocket 传输配置
- HTTP 传输配置

这意味着你未来如果要接一个真实平台，通常会遇到两种工作：

### 第一种工作：协议翻译

也就是本章反复在讲的：

- 原始平台事件 -> `MessageEnvelope`
- `MessageEnvelope` -> 平台发送格式

### 第二种工作：传输接入

比如：

- 平台是 WebSocket 推送
- 平台是 HTTP webhook
- 平台要求你主动调用 HTTP API 发消息

这部分 mofox-wire 已经准备了一层基础设施，所以 Neo-MoFox 的 `BaseAdapter` 才能在它上面继续补插件运行时能力。

你现在不需要一次把这两层全吃透。

你只要先记住：

> **Neo-MoFox 的 Adapter 是“插件组件层”，mofox-wire 是它下面那层“统一消息与传输抽象层”。**

这个关系一旦清楚，后面你再去读真实 Adapter，就不容易混淆“哪些是平台协议问题，哪些是插件运行时问题”。

## 18.13 什么时候你应该写 Adapter，什么时候不该写

这一章最后再帮你收一下边界。

### 适合写 Adapter 的场景

- 你要把 Neo-MoFox 接到一个新的聊天平台
- 你要处理这个平台独有的消息事件或发送协议
- 你需要维护和平台之间的连接、鉴权、事件流

### 不适合写 Adapter 的场景

- 你只是想在现有平台上新增一个能力
- 你只是想加一个命令、一个 Tool、一个 Agent
- 你只是想暴露一个 HTTP 接口

也就是说，如果你不是真的在做“平台接入”，那大概率就不该先动 Adapter。

## 18.14 BaseAdapter 基类速查

`BaseAdapter` 定义于 `src/core/components/base/adapter.py`，继承自 `mofox_wire.AdapterBase`。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `adapter_name` | `str` | 适配器名称 |
| `adapter_version` | `str` | 适配器版本，如 `"1.0.0"` |
| `adapter_description` | `str` | 适配器描述 |
| `platform` | `str` | 平台标识，如 `"qq"`、`"telegram"` |
| `dependencies` | `list[str]` | 组件级依赖（其他组件签名列表） |

### 实例属性

| 属性 | 说明 |
|------|------|
| `self.plugin` | 所属插件实例 |
| `self.core_sink` | 核心消息接收器（来自 mofox-wire，用于把消息送进核心） |

### 方法

| 方法 | 说明 |
|------|------|
| `from_platform_message(raw) -> MessageEnvelope` | **抽象方法**，把平台原始消息翻译成统一 `MessageEnvelope` |
| `_send_platform_message(envelope)` | 把出站 `MessageEnvelope` 翻译回平台格式并发送（建议重写） |
| `start()` | 启动 Adapter，开始接收平台消息 |
| `stop()` | 停止 Adapter，断开平台连接 |
| `on_adapter_loaded()` | **生命周期钩子**，Adapter 加载后调用，适合做初始化连接 |
| `on_adapter_unloaded()` | **生命周期钩子**，Adapter 卸载前调用，适合做资源清理 |
| `health_check() -> bool` | 健康检查，返回平台连接是否正常（可重写） |
| `reconnect()` | 重连平台（可重写） |
| `get_bot_info() -> dict` | 获取 Bot 基础信息（可重写） |
| `get_signature() -> str \| None` | 返回组件签名，格式为 `{plugin}:adapter:{adapter_name}` |

### from_platform_message 示例

```python
async def from_platform_message(self, raw: Any) -> MessageEnvelope:
    # raw 是平台推送的原始数据（如字典、proto 对象等）
    return MessageEnvelope(
        platform=self.platform,
        message_id=raw["msg_id"],
        user_id=raw["user_id"],
        group_id=raw.get("group_id"),
        content=raw["content"],
        raw_message=raw,
    )
```

### 生命周期时序

```text
Adapter 类注册 → 系统初始化 core_sink → __init__() 
  → on_adapter_loaded()   ← 适合初始化连接
  → start()               ← 开始接收消息
  ...运行中...
  → stop()                ← 停止接收
  → on_adapter_unloaded() ← 适合清理资源
```

> **注意**：`__init__` 执行时 `core_sink` 可能尚未完全就绪，不建议在 `__init__` 里依赖它做任何业务调用。需要依赖 `core_sink` 的初始化逻辑，应放到 `on_adapter_loaded()` 或 `start()` 里。

## 18.15 这一章先收在这里：第一次写 Adapter，先守住三件事

如果把这一章压成最后三句话，我更希望你记住的是：

1. **Adapter 是平台桥，不是聊天逻辑层。**
2. **Adapter 的核心任务，是围绕 `MessageEnvelope` 做双向翻译。**
3. **第一次写时，先把 text 跑通，再逐步补复杂消息和真实传输。**

只要这三件事守住，后面你去读真实平台适配器时，看到的复杂度就不会是一团雾，而会开始自然分层：

- 哪些是统一消息模型
- 哪些是平台原始协议
- 哪些是运行时生命周期
- 哪些只是工程化细节

下一步如果继续往下写，一个很自然的方向就是：

> **挑一个真实 Adapter，看它怎么把“最小双向翻译”扩展成“可运行的平台接入器”。**

到那时，再回头看这一章，你会发现最重要的其实不是示例有多复杂，而是边界有没有先讲清楚。