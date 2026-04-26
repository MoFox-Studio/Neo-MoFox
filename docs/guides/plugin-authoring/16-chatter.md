# 16. Chatter：插件里的总控组件，到底该负责什么

> **导读** 本章介绍 Chatter——插件里的对话总控组件。Chatter 不亲自实现能力，而是负责决策：这一轮要不要响应、如何组装上下文、把哪些能力交给模型、模型回来后下一步怎么走。本章将展示如何从最小闭环开始写一个 Chatter，并讲解它与 Tool / Agent / Action 的调度关系。最后提供 `BaseChatter` 基类速查。

前面几章已经把插件系统里几种重要的“能力型组件”都走过一遍了：

- Tool：拿信息、做单步处理
- Agent：在一小组能力里做局部编排
- Action：把动作真正执行出去
- EventHandler：在系统事件发生时旁听和介入

接下来终于轮到最上面那一层了：

> **Chatter。**

如果前面几类组件更像工具箱里的工具，那 Chatter 更像那个真正把整轮对话流程组织起来的人。

它不是单步能力，不是旁路钩子，也不是局部子代理。

它回答的问题更接近：

> **面对这个会话，这一轮到底要不要响应、怎么组装上下文、该把哪些能力交给模型、模型回来以后下一步又该怎么走。**

所以这一章最重要的目标，不是教你把 Chatter 写得很复杂，而是先让你真正建立一个边界非常清楚的认识：

> **Chatter 是对话总控组件。它负责调度，而不是替所有别的组件把事都做了。**

这一章不再沿用 `echo_demo`，而换一个更贴近对话主流程的最小例子：

> **一个最小“接待型” Chatter。**

因为到了 Chatter 这一层，示例最好更像“完整对话入口”，而不是一个单独的文本处理器。

## 16.1 先把一句话记住：Chatter 不是更大的 Tool，也不是更大的 Agent

这是这一章最重要的一句话。

很多人第一次看到 Chatter，很容易把它理解成：

- 一个能调更多组件的超级 Agent
- 或者一个“什么都能做”的总类

这两种理解都不太准确。

更准确的说法应该是：

> **Chatter 不负责替每个组件完成工作，它负责决定这轮对话怎么组织。**

也就是说，它的核心价值不是某个单独能力，而是流程控制。

它更关心的是：

- 这轮该不该响应
- 上下文怎么组装
- 哪些 Tool / Agent / Action 应该暴露给模型
- 模型返回了什么
- 接下来该等待、继续、失败，还是暂时停止

这就是为什么它处在整个对话系统最上层。

## 16.2 Tool、Agent、Action 和 Chatter，到底怎么分工

这一章你希望明确讲边界，这一步非常有必要。

可以先把几类组件粗暴压成下面四句话：

### Tool

负责一小步、通常可返回信息的能力。

### Agent

负责一小块局部任务的多步编排。

### Action

负责真正对当前会话或外部系统产生副作用。

### Chatter

负责把一整轮对话流程组织起来。

所以如果你要问“谁是这轮对话的导演”，答案通常是 Chatter。

它不一定亲自演每一段，但它决定：

- 谁上场
- 什么时候上场
- 这一轮什么时候收口

## 16.3 `default_chatter` 是什么关系：它是标准实现，不是你必须照抄的模板

这里也需要先把预期放正。

仓库里的 `default_chatter` 确实是一个很完整的实现，而且已经承担了：

- 上下文组织
- usable 注入
- tool calling
- follow-up
- 子代理判定
- 多阶段控制流

但这并不意味着：

> **你第一次写自己的 Chatter，就该把它写成 `default_chatter` 那个体量。**

更合理的理解是：

- `default_chatter` 是一个完整参考实现
- `BaseChatter` 才是插件作者起步时真正该抓住的抽象边界

所以这一章里，`default_chatter` 只当参照物存在，不会再深入拆它的内部执行流。

## 16.4 `BaseChatter` 给你的，不只是一个抽象类，而是一套总控样板

这点很重要。

当前 `BaseChatter` 不是那种只给你留一个空 `execute()` 的极简基类，它其实已经内置了不少“总控组件常用样板”：

- `create_request()`：快速创建带上下文管理器的 LLM 请求
- `get_llm_usables()`：收集当前可用的 Action / Agent / Tool
- `modify_llm_usables()`：按会话条件和激活状态过滤 usable
- `inject_usables()`：把过滤后的 usable 注入 request
- `run_tool_call()`：执行一次响应中的一批普通 tool calls，并按原始顺序回写 `TOOL_RESULT`
- `fetch_unreads()`：读取当前流中的未读消息
- `flush_unreads()`：把已处理未读移入 history
- `format_message_line()`：把消息格式化成统一的 prompt 文本

这意味着一个很重要的事实：

> **写 Chatter 时，你通常不是从零造一个对话引擎，而是在接一套已经准备好的总控样板。**

这会大幅降低你写最小 Chatter 的门槛。

## 16.5 Chatter 为什么天然和“聊天流”绑定

从构造函数就能看出来，`BaseChatter` 初始化时拿到的是：

- `stream_id`
- `plugin`

这里和 Action 有点像，但层次完全不同。

Action 拿到 `chat_stream` 是为了做当前会话上的一个动作；

Chatter 拿到 `stream_id`，则是为了围绕这条会话流本身去组织对话生命周期。

它天然要关心：

- 这条流上有哪些未读消息
- 历史上下文长什么样
- 现在绑定的是哪个 chatter 实例
- 这一轮执行完以后，流状态应该怎么推进

所以 Chatter 从一开始就不是“独立函数式组件”，而是一个流级运行时角色。

## 16.6 `chat_api` 这层暴露出来的，其实就是 Chatter 的运行位置信息

你如果去看公共 API，会发现 `chat_api` 暴露的不是“让你执行一个 chat 函数”，而更多是这些能力：

- 获取所有 chatter 类
- 查看当前活跃 chatter
- 按 stream 获取 chatter
- 为 stream 自动选择并绑定合适的 chatter

这背后的信号非常明确：

> **Chatter 在架构里的身份，不是一个随手调用的能力函数，而是一个跟 stream 绑定的运行中组件。**

也就是说，Chatter 更接近“会话的执行者”，而不是“某次调用的服务对象”。

## 16.7 自动绑定这件事很重要：并不是每条流都得手动指定 Chatter

这一点很容易被忽略，但对插件作者理解整体架构很重要。

`ChatterManager.get_or_create_chatter_for_stream()` 会根据：

- `chat_type`
- `platform`

来为当前流选择最合适的 chatter 类，然后实例化并注册为活跃 chatter。

也就是说，插件系统并不是在说：

> “你必须手动告诉每条流用哪个 chatter。”

而是在说：

> “只要你的 chatter 声明清楚适用范围，系统就可以自动把它绑定到合适的流上。”

这让 Chatter 真正变成了“对话入口层组件”，而不是你每次都要手工拼装的对象。

## 16.8 一个最小自定义 Chatter，应该先做什么，不该做什么

第一次写 Chatter，目标不要定得太高，更稳的做法是：

> **先写一个能完成最小对话闭环的 Chatter。**

也就是说，它至少做到：

1. 读取未读消息
2. 组装一段最基本的上下文
3. 发起一次 LLM 请求
4. 根据结果决定是等待、失败还是暂时停止

而不是一上来就把下面这些全塞进去：

- 子代理判定
- 多轮 tool calling
- 复杂 prompt 注入
- 状态机分相
- 自定义去重

这些都可以以后再加。

## 16.9 一个更贴近真实主流程的最小例子：`ReceptionChatter`

这一章我们换一个更像真实对话入口的例子：

> **一个最小接待型 Chatter。**

它的目标很简单：

- 收到未读消息后
- 把最近内容整理成一段 USER 文本
- 交给模型生成一条简短回复
- 然后结束本轮，等待下一批消息

这不是完整产品级实现，但它非常适合帮助你理解 Chatter 的职责边界。

```python
from __future__ import annotations

from collections.abc import AsyncGenerator

from src.app.plugin_system.base import BaseChatter, Failure, Stop, Wait
from src.app.plugin_system.types import LLMPayload, ROLE, Text


class ReceptionChatter(BaseChatter):
    """一个最小的接待型 Chatter。"""

    chatter_name = "reception_chatter"
    chatter_description = "负责对当前会话的未读消息做最小响应"

    async def execute(self) -> AsyncGenerator[Wait | Failure | Stop, None]:
        """执行一轮最小对话流程。"""
        unread_text, unread_messages = await self.fetch_unreads()
        if not unread_messages:
            yield Wait()
            return

        request = self.create_request(task="actor", with_reminder="actor")
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text("你是一个简洁、礼貌的接待助手，请根据用户最新消息给出简短回复。"),
            )
        )
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(f"以下是本轮新消息：\n{unread_text}"),
            )
        )

        try:
            response = await request.send(stream=False)
            await response
        except Exception as error:
            yield Failure("LLM 请求失败", error)
            return

        await self.flush_unreads(unread_messages)
        yield Stop(0)
```

这段代码很克制，但它已经足够像一个 Chatter 了。

## 16.10 这个最小例子里，真正体现 Chatter 身份的是哪几步

如果把刚才的代码拆开看，最能体现 Chatter 身份的不是 `send()`，而是这几步：

### 第一步：从流里拿未读

```python
unread_text, unread_messages = await self.fetch_unreads()
```

这说明它不是在处理一段孤立输入，而是在处理当前会话流的状态。

### 第二步：决定这轮要不要响应

```python
if not unread_messages:
    yield Wait()
    return
```

这一步非常像总控逻辑，而不是能力逻辑。

### 第三步：自己组织 request

```python
request = self.create_request(...)
```

这意味着它在决定这一轮对话用什么模型任务、什么上下文管理、什么提示词结构。

### 第四步：处理完以后推进流状态

```python
await self.flush_unreads(unread_messages)
yield Stop(0)
```

这一步尤其关键，因为它说明 Chatter 不只是“问模型一个问题”，它还负责决定这轮会话结束以后流状态怎么往前走。

## 16.11 为什么这个最小例子里还没有注入 Tool / Agent / Action

这是刻意的。

你如果第一次写 Chatter，我更建议先搞清楚：

- 它怎么读流
- 怎么组装最小上下文
- 怎么发一次模型请求
- 怎么结束本轮

只有这条主线稳定了，再把 Tool / Agent / Action 注进来，才不会把整个 Chatter 写成一团。

也就是说：

> **Chatter 的第一职责是建立对话主循环，不是急着把所有能力都塞进去。**

## 16.12 但一旦要接入能力组件，Chatter 就是它们的总调度入口

虽然刚才的例子没接工具，但你得知道：一旦进入真实场景，Chatter 确实就是 Tool / Agent / Action 的总调度入口。

这在 `BaseChatter` 里已经有完整样板了：

- `get_llm_usables()` 收集全局可用组件
- `modify_llm_usables()` 根据当前会话筛 usable
- `inject_usables()` 把筛出来的能力注入 request
- `run_tool_call()` 负责执行一批普通 tool calls，并把结果按模型输出顺序回写

所以更准确的说法是：

> **Chatter 不亲自实现这些能力，但它决定这一轮对话能调用哪些能力，以及这些能力什么时候被接进来。**

这就是“总控”两个字真正的含义。

## 16.13 什么时候该写自定义 Chatter，而不是继续堆 Agent 或 Action

这个问题很实际。

如果你只是想：

- 加一个新动作
- 加一个新工具
- 加一个局部子工作流

那通常还不需要新写 Chatter。

但如果你想改变的是：

- 这轮对话什么时候开始响应
- 上下文怎么组装
- 哪些能力该暴露给模型
- 一轮响应之后流状态怎么推进
- 整个会话主循环该怎么跑

那你讨论的就已经不是 Tool/Agent/Action 级别的问题，而是 Chatter 级别的问题了。

所以可以把判断标准压成一句话：

> **你如果想改的是“能力”，通常写 Tool / Agent / Action；你如果想改的是“对话主流程”，那就是 Chatter。**

## 16.14 最容易把 Chatter 写歪的方式：什么都往里面塞

这是一个很现实的提醒。

因为 Chatter 站在最上层，所以它特别容易被写成一个巨大的“万能类”。

常见坏味道包括：

- 复杂 prompt 都硬编码在 Chatter 里
- Tool 的具体业务逻辑也写在 Chatter 里
- Action 的执行细节也写在 Chatter 里
- 各种条件判断、状态迁移、外部调用全挤在一起

这样写出来的结果通常是：

- 一开始很快
- 后面越来越难改
- 任何能力边界都开始模糊

更稳的方式反而是：

- Chatter 负责流程骨架
- Tool 负责拿信息
- Agent 负责局部编排
- Action 负责落地执行
- EventHandler 负责旁路介入

也就是说：

> **Chatter 应该做导演，不该抢走所有演员的工作。**

## 16.15 BaseChatter 基类速查

`BaseChatter` 定义于 `src/core/components/base/chatter.py`，继承自 `ABC`。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `chatter_name` | `str` | Chatter 名称 |
| `chatter_description` | `str` | Chatter 描述 |
| `associated_platforms` | `list[str]` | 关联的平台列表，为空则不限制 |
| `chat_type` | `ChatType` | 支持的聊天类型（`ALL` / `PRIVATE` / `GROUP`） |
| `dependencies` | `list[str]` | 组件级依赖（其他组件签名列表） |

### 实例属性

| 属性 | 说明 |
|------|------|
| `self.stream_id` | 当前聊天流 ID |
| `self.plugin` | 所属插件实例 |

### 方法

| 方法 | 说明 |
|------|------|
| `execute() -> AsyncGenerator[ChatterResult, None]` | **抽象方法**，主对话循环，使用 `yield` 返回结果 |
| `get_llm_usables() -> list` | 从全局注册表获取本 Chatter 可用的所有 LLMUsable 组件 |
| `modify_llm_usables(usables) -> list` | 对可用组件列表做激活判定和平台过滤 |
| `inject_usables(request) -> ToolRegistry` | 一步完成「获取 → 过滤 → 注册 → 注入 TOOL payload」全链路 |
| `run_tool_call(calls, response, usable_map, trigger_msg)` | 执行一次响应中的一批普通 tool calls，并将 `TOOL_RESULT` 按原始顺序追加到 response |
| `fetch_unreads(time_format) -> tuple[str, list[Message]]` | 读取未读消息，返回格式化文本和消息列表（不修改上下文） |
| `flush_unreads(unread_messages) -> int` | 将指定的未读消息批次移入历史记录，返回实际 flush 数量 |
| `create_request(model_set, ...) -> LLMRequest` | 快速创建 LLMRequest 对象 |
| `get_signature() -> str \| None` | 返回组件签名，格式为 `{plugin}:chatter:{chatter_name}` |

### ChatterResult 类型

`execute()` 通过 `yield` 返回以下结果类型之一：

| 类型 | 构造 | 含义 |
|------|------|------|
| `Wait` | `Wait(reason: str)` | 等待中（如等待用户输入或 LLM 响应） |
| `Success` | `Success(message: str, data: Any = None)` | 本轮对话成功完成 |
| `Failure` | `Failure(error: str, exception: Exception \| None = None)` | 本轮对话失败 |
| `Stop` | `Stop(time: float \| int)` | 对话暂停，`time` 秒后重新开始 |

### execute 模式示意

```python
async def execute(self) -> AsyncGenerator[ChatterResult, None]:
    # 读取未读消息
    unread_text, unread_messages = await self.fetch_unreads()
    if not unread_messages:
        yield Stop(5)
        return

    # 构建请求
    request = self.create_request(model_set)
    request.add_payload(LLMPayload(ROLE.USER, Text(unread_text)))

    # 发送并处理
    response = await request.send(stream=False)
    final_text = await response

    await self.flush_unreads(unread_messages)
    yield Success(final_text)
```

> **注意**：`fetch_unreads()` 只读取，`flush_unreads()` 才真正将消息移入历史。两步分离可以保证"读取时刻之后新增的未读消息"不会被误清空。

## 16.16 对插件作者来说，这一章最值得带走什么

把这一章压成几个最实用的结论，大概就是：

1. Chatter 不是单步能力组件，而是对话总控组件。
2. 它天然和 stream 绑定，关心的是整轮对话怎么组织和推进。
3. `BaseChatter` 已经提供了一套总控样板，写最小 Chatter 不必从零造引擎。
4. Tool / Agent / Action 解决的是能力问题，Chatter 解决的是主流程问题。
5. 自定义 Chatter 最容易写歪的方式，就是把所有能力细节都塞进来。

## 16.17 把这一章压缩成一句话

如果要把这一章压缩成一句最值得带走的话，那就是：

> **在 Neo-MoFox 里，Chatter 负责的不是某个具体能力，而是一整轮对话如何开始、如何调度能力、以及如何收口。**

沿着这条线，下一步自然会涉及：

> **当 Tool、Agent、Action、EventHandler、Chatter 都已经讲完以后，插件作者该怎么给这些组件划清职责，做出一个不混乱的小型插件架构？**

那就该进入组件分层总章了。
