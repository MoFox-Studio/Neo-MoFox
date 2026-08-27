# 15. 让插件学会旁听与介入：事件系统怎么用

> **导读** 本章介绍事件系统与 `BaseEventHandler` 的用法。事件系统是插件与系统协作的另一条线：不是模型主动调用，而是系统某件事发生时通知插件。本章将解释事件处理器的订阅机制、链式传递语义、`EventDecision` 三种返回值的含义，以及如何用事件系统在插件之间实现低耦合协作。最后提供 `BaseEventHandler` 基类速查。

前面几章我们一直在围绕“模型如何调用组件”展开：

- Tool 负责提供信息能力
- Agent 负责局部编排
- Action 负责真正把动作做出去

但插件系统里还有一条完全不同的线：

> **不是模型来主动调用你，而是系统里某件事发生之后，通知你。**

这条线，就是事件系统。

如果说前面的组件更像“能力入口”，那事件系统更像“系统广播”。

它解决的不是：

- 用户输入一个命令以后，你怎么响应
- 模型想调用一个 Tool 时，你怎么执行

而是：

- 某条消息刚被接收到了，谁要处理一下？
- 某个 prompt 正准备构建，谁想往里面补点东西？
- 某个插件刚加载完，谁要跟着做初始化？

所以这一章的核心目标，是让你建立一个非常关键的心智模型：

> **事件系统不是让插件“主动出手”的主入口，而是让插件在系统某个时刻被动收到通知、按需介入。**

这一章继续沿用 `echo_demo`，但我们会第一次离开“模型调用组件”这条主线，转而看另一条非常重要的系统协作链。

## 15.1 先把事件系统和前面几类组件彻底分开

这一章如果不先分层，后面会很容易混淆。

### Tool / Agent / Action 是“模型可见能力”

它们的共同点是：

- 最终都可能出现在模型可调用能力列表里
- 或者至少处在一条由模型驱动的调用链上

也就是说，它们大多回答的是：

> **模型现在想做一件事，系统怎么帮它做。**

### EventHandler 是“系统事件订阅者”

而事件处理器不是这条线。

它回答的是另一件事：

> **系统里某个事件刚发生，插件要不要参与一下。**

所以 EventHandler 的视角不是“我现在要给模型提供什么能力”，而是：

- 我订阅了哪些事件
- 某个事件发生时，我要不要改一下共享参数
- 我是继续放行，还是直接拦住后续处理器

这是一套完全不同的工作方式。

## 15.2 这套事件系统，插件作者真正会接触到哪两层

当前仓库里的事件系统分层很清楚，但对插件作者来说，不需要一开始就把所有层都背下来。

你真正会经常碰到的，主要是两层：

### 第一层：`BaseEventHandler`

这是插件侧写事件处理器的基类。

你要做的事情基本都在这里：

- 定义处理器名字和描述
- 声明初始订阅的事件
- 实现 `execute(event_name, params)`

### 第二层：`event_api.publish_event()`

这是插件作者用来发布事件的公共入口。

也就是说，插件侧最常见的两个动作就是：

- 订阅事件
- 发布事件

至于更底下的 `kernel/event` 和 `EventManager`，你当然可以理解，但这一章重点不会放在让你背内部实现，而是让你知道插件作者站在哪一层使用它。

## 15.3 事件系统的核心，不是“广播消息”，而是“链式传递共享参数”

这是这一章最关键的一点。

很多人第一次听“事件总线”，脑子里会自动浮现一种非常宽松的广播模型：

- 事件发出去
- 谁订阅了谁就收一下
- 大家互不影响

但当前这套实现比这个更严格，也更有力量。

它不是简单广播，而是：

> **多个处理器按优先级依次执行，并共享同一份参数链。**

也就是说，一次事件发布之后，不是“大家各看各的”，而更像：

```text
发布事件
-> 第一个处理器拿到 params
-> 第二个处理器拿到上一个处理器处理后的 params
-> 第三个处理器继续接着拿
```

这就是为什么这套事件系统不只是通知机制，它还是一种链式协作机制。

## 15.4 `execute()` 的签名为什么这么严格

`BaseEventHandler.execute()` 的签名是固定的：

```python
async def execute(
    self,
    event_name: str,
    params: dict[str, Any],
) -> tuple[EventDecision, dict[str, Any]]
```

这不是风格问题，而是协议。

你要注意这里有三层约束：

1. 输入一定是 `event_name + params`
2. 输出一定是 `(EventDecision, params)`
3. 返回的 `params` 必须和输入时拥有同一组 key

第三条特别重要。

当前底层 EventBus 明确要求：

> **处理器返回的 `next_params`，key 集合必须和原始 `params` 完全一致。**

如果不一致，这次改动就会被丢弃。

也就是说，你可以：

- 修改已有字段的值
- 在事件最初发布时就预留好字段，然后在链上不断填充

但你不能半路随意给 `params` 增删结构。

这条约束的目的很明确：

> **保证一条事件链上的共享参数结构稳定，不让后续处理器拿到一份形状飘来飘去的对象。**

## 15.5 `EventDecision` 不是附属细节，而是事件链控制权

这一章你要求把 `PASS/STOP` 讲透，这是对的，因为它们正是事件系统区别于普通回调的地方。

当前决策值主要有三种：

- `SUCCESS`
- `PASS`
- `STOP`

### `SUCCESS`

表示：

- 我处理完了
- 我的参数修改可以继续往后传
- 后续处理器继续执行

这通常是最常见的返回值。

### `PASS`

表示：

- 我决定跳过
- 就算我改了 `params`，这些改动也不应该传播
- 直接交给后续处理器继续走

这点非常关键。

所以 `PASS` 不是“执行成功的一种说法”，而是：

> **我不想对这条事件链产生实际影响。**

### `STOP`

表示：

- 我已经处理完，并且要终止后续处理器
- 当前这份参数状态就是链路终点

也就是说，`STOP` 给了事件处理器一种真正的“拦截”能力。

这就是为什么事件系统不仅能监听，还能介入控制流程。

## 15.6 优先级不是装饰，它决定谁先拿到共享参数

事件处理器有一个很重要的类属性：

```python
weight = 0
```

权重越高，执行得越早。

因为这套系统是共享参数链式传递，所以“谁先执行”不只是日志顺序问题，而是会直接影响后续看到的状态。

比如：

- 高权重处理器先做规范化
- 后面的处理器再消费规范化后的内容

或者：

- 高权重处理器先发现风险
- 直接 `STOP`
- 后面的处理器根本不再运行

所以一旦涉及事件系统，你就要开始有“链头”和“链尾”的意识。

## 15.7 系统事件和自定义事件，两者都很重要

这一章你要求两者都讲，这也是对的，因为它们分别解决不同问题。

### 系统事件

系统事件通常来自框架内部，比如：

- `EventType.ON_MESSAGE_RECEIVED`
- `EventType.ON_MESSAGE_SENT`
- `EventType.ON_ALL_PLUGIN_LOADED`
- `EventType.ON_RECEIVED_OTHER_MESSAGE`
- `EventType.BEFORE_CHATTER_DISPATCH`

这类事件的价值在于：

> **插件可以在框架已经定义好的关键时刻插进去。**

比如 notice 收集、消息拦截、插件加载后补初始化，都是这一类。

其中 `BEFORE_CHATTER_DISPATCH` 值得单独一提：它在每个会话 Tick 的最前端发布，把 `continue` 置为 `False` 可以让本轮分发整体跳过——等待状态、恢复事件、消息缓冲与生成器全部原样保留，不会消耗任何流状态。适合做专注度门控、优先级压制这类“本轮先别说话”的判断；如果阻断逻辑需要消费消息或状态，应该用 `ON_CHATTER_STEP` 而不是它。

### 自定义事件

自定义事件则更像插件之间的协作约定。

例如：

```python
await event_api.publish_event(
    "echo_demo:text_polished",
    {
        "stream_id": stream_id,
        "text": polished_text,
        "source": "echo_polish_agent",
    },
)
```

它的意义是：

- 这个事件不是框架预设的
- 而是插件自己定义的一条协作信号

所以你可以把它理解成：

> **系统事件是框架给你的钩子，自定义事件是你自己搭的协作接口。**

## 15.8 为什么 `EventType` 必须保持为 `str + Enum`

这是个实现细节，但很值得记住。

当前仓库里 `EventType` 不是普通 `Enum`，而是：

```python
class EventType(str, Enum):
    ...
```

原因很直接：底层 `EventBus` 只接受字符串事件名。

如果这一层不是 `str + Enum`，就很容易在订阅和发布时出现：

- 你以为自己传的是同一个事件
- 实际字符串值根本对不上

这件事仓库里已经踩过坑了，所以这一章你可以把它记成一个非常务实的规则：

> **系统事件名最终都得落成稳定字符串。**

## 15.9 继续沿用 `echo_demo`：先写一个最小系统事件处理器

既然前面几章一直沿用 `echo_demo`，这一章最自然的方式，就是也给它加一个最小 EventHandler。

这次我们不从复杂的 prompt 注入开始，而先从一个更容易理解的系统事件切入：

> **当系统收到一条消息时，记录一些插件自身的轻量信息。**

这个例子不是为了做重功能，而是为了让你先看懂事件处理器长什么样。

```python
from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import EventType
from src.kernel.event import EventDecision

logger = get_logger("echo_demo_event")


class EchoMessageLogger(BaseEventHandler):
    """在消息接收事件发生时记录一条轻量日志。"""

    handler_name = "echo_message_logger"
    handler_description = "监听消息接收事件，输出 echo_demo 的调试日志"
    weight = 0
    init_subscribe = [EventType.ON_MESSAGE_RECEIVED]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理系统消息接收事件。"""
        message = params.get("message")
        if message is None:
            return EventDecision.PASS, params

        logger.info(
            f"echo_demo 收到消息事件: stream_id={getattr(message, 'stream_id', '')}"
        )
        return EventDecision.SUCCESS, params
```

这个例子里最关键的不是功能有多强，而是它很标准：

- 订阅系统事件
- 收到 `params`
- 判断自己是否真的要处理
- 返回 `SUCCESS` 或 `PASS`

这已经是一个合格的最小事件处理器了。

## 15.10 如果想做“介入”，而不是单纯旁听，就要认真对待 `params`

刚才那个例子更像“监听器”。

但事件系统真正厉害的地方，在于它不只能旁听，还能介入。

比如当前仓库里就有两类很典型的真实例子：

- `notice_injector` 监听系统的其他消息事件，把 notice 收集起来
- `booku_memory` 监听 `on_prompt_build`，在 prompt 构建前往 `values.extra` 里注入记忆闪回

这两类例子都说明了一件事：

> **事件处理器不只是“知道发生了什么”，还可以沿着参数链对后续行为产生影响。**

这里的关键不是胡乱改字典，而是：

- 先理解这条事件原本约定了哪些 key
- 再在这些既有字段里做安全修改

这就是为什么这套系统虽然灵活，但又必须强调参数签名稳定。

## 15.11 `PASS` 最容易被误用，尤其是在“我虽然看了，但不想生效”这种场景里

很多人第一次写事件处理器，最容易犯的错是：

- 改了一些 `params`
- 结果最后返回了 `PASS`
- 然后疑惑为什么后面没看到自己的改动

原因其实很简单。

`PASS` 的语义就是：

> **我这次不对共享参数链产生实际影响。**

所以如果你希望自己的改动真的被后续处理器或事件发布方看到，就应该返回 `SUCCESS`，而不是 `PASS`。

这条规则很简单，但非常重要。

## 15.12 自定义事件更像插件之间的“低耦合协作口”

如果说系统事件是在框架关键时刻插钩子，那自定义事件更适合做插件之间的松耦合协作。

比如在 `echo_demo` 里，你可以这样设计：

- `EchoPolishAgent` 完成文本整理后，不直接绑定某个固定后处理器
- 而是发布一个 `echo_demo:text_polished` 事件
- 谁关心这件事，谁就去订阅它

例如：

```python
from src.app.plugin_system.api import event_api

await event_api.publish_event(
    "echo_demo:text_polished",
    {
        "stream_id": stream_id,
        "text": polished_text,
        "mode": "polite",
    },
)
```

然后另一个处理器去监听：

```python
class EchoPolishObserver(BaseEventHandler):
    handler_name = "echo_polish_observer"
    init_subscribe = ["echo_demo:text_polished"]

    async def execute(self, event_name: str, params: dict[str, Any]):
        text = params.get("text", "")
        if not text:
            return EventDecision.PASS, params

        # 这里可以做日志、统计、缓存、二次派发等
        return EventDecision.SUCCESS, params
```

这样做的好处是：

- 发布方不用知道谁会来接
- 订阅方也不用和发布方硬编码绑定

这就是事件系统在“插件协作”层面最好用的地方。

## 15.13 什么时候该用事件系统，什么时候不该用

这一章也很需要讲清这个边界，不然事件系统特别容易被滥用。

### 更适合用事件系统的时候

- 某个系统时刻发生了，需要旁听或轻量介入
- 你想给插件之间留一个低耦合协作点
- 你不想让主调用方直接依赖某个具体实现

### 不太适合用事件系统的时候

- 你其实只是想明确调用某个 Service
- 你需要稳定、直接、强约束的一次调用结果
- 你要表达的是严格业务主流程，而不是旁路协作

也就是说：

> **事件系统更适合“发生了某件事，谁想参与就来”，不适合替代所有明确调用。**

## 15.14 对插件作者来说，最值得先学会的不是“拦截”，而是“稳稳地放行”

虽然 `STOP` 很有吸引力，但入门阶段，重点应该放在：

- 正确订阅
- 理解参数结构
- 在不破坏链的前提下做小改动
- 该 `SUCCESS` 时 `SUCCESS`
- 不需要生效时 `PASS`

原因很现实。

一旦进入拦截逻辑，你就在控制别人后面还能不能跑，这对事件语义理解要求更高。

所以初学阶段最稳的做法是：

> **先学会做一个好邻居，再学会做一个拦路者。**

## 15.15 BaseEventHandler 基类速查

`BaseEventHandler` 定义于 `src/core/components/base/event_handler.py`，继承自 `ABC`。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `handler_name` | `str` | 处理器名称 |
| `handler_description` | `str` | 处理器描述 |
| `weight` | `int` | 处理器权重，数值越大执行顺序越靠前，默认 `0` |
| `intercept_message` | `bool` | 是否具有拦截消息能力，默认 `False` |
| `init_subscribe` | `list[EventType \| str]` | 初始化时自动订阅的事件类型列表 |
| `dependencies` | `list[str]` | 组件级依赖（其他组件签名列表） |

### 实例属性

| 属性 | 说明 |
|------|------|
| `self.plugin` | 所属插件实例 |
| `self._subscribed_events` | 当前已订阅的事件集合（内部使用） |

### 方法

| 方法 | 说明 |
|------|------|
| `execute(event_name, params) -> tuple[EventDecision, dict]` | **抽象方法**，事件处理核心逻辑 |
| `subscribe(event)` | 订阅一个事件，支持 `EventType` 枚举或字符串 |
| `unsubscribe(event)` | 取消订阅一个事件 |
| `get_subscribed_events() -> list` | 获取当前已订阅的所有事件 |
| `is_subscribed(event) -> bool` | 检查是否已订阅某个事件 |
| `get_signature() -> str \| None` | 返回组件签名，格式为 `{plugin}:event_handler:{handler_name}` |

### EventDecision 枚举值

| 值 | 含义 |
|----|------|
| `EventDecision.SUCCESS` | 执行完成，**传播当前 `params` 改动**，继续后续处理器 |
| `EventDecision.PASS` | 跳过本处理器，**不传播 `params` 改动**，继续后续处理器 |
| `EventDecision.STOP` | **拦截**，终止后续所有处理器执行 |

### execute 示例

```python
async def execute(
    self, event_name: str, params: dict[str, Any]
) -> tuple[EventDecision, dict[str, Any]]:
    message = params.get("message")
    if message:
        self.plugin.storage.log(str(message))
    return EventDecision.SUCCESS, params
```

> **注意**：`params` 是链路中共享的字典，`SUCCESS` 时对它的修改会传递给后续处理器；`PASS` 则不会。修改 `params` 的 key 结构时要格外谨慎，不要改变字段的集合，否则会破坏后续处理器的参数预期。

## 15.16 对插件作者来说，这一章最值得带走什么

把这一章压成几个最实用的结论，大概就是：

1. EventHandler 不是模型能力组件，而是系统事件订阅者。
2. 当前事件系统是链式参数传递，不是彼此隔离的宽松广播。
3. `SUCCESS` 会传播参数改动，`PASS` 不会，`STOP` 会终止后续处理器。
4. `params` 的 key 集合必须稳定，不能在链路中随意改形状。
5. 系统事件适合接框架钩子，自定义事件适合做插件之间的低耦合协作。

## 15.17 把这一章压缩成一句话

如果要把这一章压缩成一句最值得带走的话，那就是：

> **Neo-MoFox 的事件系统本质上是一条可订阅、可拦截、可链式传递共享参数的协作通道：它不是替代明确调用，而是让插件能在系统关键时刻旁听、介入和协同。**

沿着这条线，下一步自然会涉及：

> **当 Tool、Agent、Action、EventHandler 都已经出现以后，插件作者该怎样为它们划分职责，避免什么都往一个组件里塞？**

那就是组件分层总章了。