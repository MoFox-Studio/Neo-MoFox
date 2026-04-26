# 14. 让插件真正动起来：什么时候该写 Action

> **导读** 本章介绍 Action 组件的定位与用法。Action 与 Tool 的核心区别在于：Tool 把信息带回来，Action 把动作做出去。理解这条分工线，是避免组件职责混淆的关键。本章将展示如何定义 Action、如何绑定 `chat_stream`、如何利用 `go_activate()` 实现条件激活，并结合 `echo_demo` 完成一个完整的动作组件示例。最后提供 `BaseAction` 基类速查。

前面我们已经分别讲过：

- Tool：适合做可查询、可返回信息的单步能力
- Agent：适合把一组 Tool 收束成一个局部编排工作流

那接下来就轮到另一个很关键、但也最容易被误解的组件了：

> **Action。**

很多人第一次看到 Action，会下意识把它理解成“另一种 Tool”。

这个理解不能说完全错，但会越用越别扭。因为在当前实现里，Action 和 Tool 的职责不是平行替换关系，而是明显分工不同：

- Tool 更像“把信息拿回来”
- Action 更像“把动作做出去”

这一章继续沿用 `echo_demo`，但目标会和前几章不一样。

这次我们不再重点讨论“模型知道了什么”，而是讨论：

> **当模型已经做出判断之后，插件怎样替它真正产生一个副作用。**

比如：

- 发送一段文本
- 发送一张图片
- 发一条语音
- 执行一个外部动作

这些都更接近 Action 的位置。

## 14.1 先把一句话记住：Action 是“主动响应”，不是“信息查询”

当前 `BaseAction` 的注释其实已经把定位说得很直接了：

> **动作是“主动的响应”，LLM 并不会从中获得信息。**

这句话特别重要。

因为它直接决定了你该不该把某个能力写成 Action。

### 如果一个能力的核心价值是“返回信息给模型继续推理”

那它大概率更像 Tool。

比如：

- 查询数据库记录
- 读取某段配置
- 检索历史记忆
- 做一次文本转换并把结果返回

这些场景，模型真正需要的是结果本身。

### 如果一个能力的核心价值是“对外界产生动作”

那它更像 Action。

比如：

- 把文字发到当前会话
- 发送语音
- 发送表情包
- 触发外部系统执行某件事

这些场景里，模型关心的重点不再是“拿回一份材料”，而是“这件事有没有被执行”。

所以可以先把 Tool 和 Action 粗暴压成两句话：

> **Tool 偏信息流，Action 偏副作用。**

> **Tool 往往服务于继续推理，Action 往往服务于真正落地响应。**

## 14.2 为什么 Action 不能简单理解成“会返回字符串的 Tool”

这里非常值得停一下。

因为从函数签名上看，Action 和 Tool 都经常长这样：

```python
async def execute(...) -> tuple[bool, str]
```

如果只看这一层，确实很容易觉得它们差不多。

但语义完全不一样。

### Tool 返回的结果，通常是给模型继续消费的

Tool 的返回值通常会被包成 `ToolResult` 写回上下文，然后进入 follow-up。

也就是说，Tool 的结果往往是：

- 新的信息
- 中间材料
- 后续推理依据

### Action 返回的结果，更多是执行回执

Action 虽然也返回 `(success, result)`，但这个 `result` 更像：

- 成功了没有
- 做了什么
- 失败原因是什么

它不是为了给模型喂更多知识，而是为了让系统知道动作有没有正常完成。

这就是为什么 Action 更像“执行层”，而不是“知识层”。

## 14.3 当前运行链里，Action 也是独立走 manager 的

这不是概念区分，而是运行时真的分开了。

在 `BaseChatter.exec_llm_usable()` 里，系统会把 Tool / Action / Agent
统一交给公共执行器。公共执行器会根据组件类型实例化对象，并把
`execute()` 包装成可调度的执行对象：

- 普通 coroutine 会直接并发执行并在返回后完成
- 异步生成器可以先准备资源，`yield None` 暂停，等待统一调度
- 最后一次非空 `yield` 会被视为执行结果

这意味着 Action 在运行链上的定位是明确的：它仍然不是“Tool 的一个别名”，
但它和 Tool / Agent 共享同一个调度入口。这个入口在创建 Action 时会做几件事：

1. 先根据消息定位或激活对应的 `ChatStream`
2. 用当前流和插件实例创建 Action 实例
3. 调用并包装 `execute()`，让顺序敏感的最终发送动作可以交给统一调度器安排

也就是说，Action 的天然工作上下文不是“纯函数式输入输出”，而是：

> **带着当前会话流，去做一个真正和当前聊天环境相关的动作。**

这一点和 Tool 很不一样。

## 14.4 为什么说 Action 往往更贴近“当前会话”

从基类构造函数就能看出来，Action 初始化时拿到的是：

- `chat_stream`
- `plugin`

这意味着 Action 天然知道：

- 现在在哪个会话里
- 当前平台是什么
- 当前流上下文是什么

所以它非常适合做这种事：

- 把消息发回当前流
- 根据最近聊天内容做一个动作判断
- 调用某个 service 对当前会话执行外部操作

换句话说：

> **Tool 更像独立能力函数，Action 更像绑定当前会话语境的执行器。**

## 14.5 `go_activate()` 让 Action 很适合做“有条件出现的动作能力”

Action 还有一个很实用的点，是很多插件作者一开始会低估的：

> **它可以在进入模型可用能力列表之前，先做激活判定。**

基类提供了 `go_activate()`，而 `ActionManager.modify_actions()` 会在运行前根据上下文做过滤。

这意味着你不一定要让每个 Action 永远暴露给模型。

你完全可以让某个 Action：

- 只在特定关键词出现时激活
- 只在某类平台里激活
- 只在某类消息类型出现时激活

这对控制动作类能力尤其重要。因为副作用能力一旦暴露过多，模型就可能变得“手很勤快”，但未必总是合适。

## 14.6 继续沿用 `echo_demo`：这次我们做一个真正会发消息的 Action

前面几章里，`echo_demo` 更多是在做“处理文本”。

现在轮到 Action 了，就该让它真正产生一个外部效果。

所以这一章的最小例子，不再是“格式化文本”，而是：

> **把整理好的文本发回当前聊天流。**

这很适合作为第一个 Action，因为它足够直观：

- 你一眼就能看出它不是在查询信息
- 它确实在当前会话里产生了副作用
- 它也很容易和 Tool 的职责区分开

## 14.7 一个最小的 `EchoSendAction` 可以怎么写

先看一个足够小、但已经符合当前公共 API 风格的例子：

```python
from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseAction


class EchoSendAction(BaseAction):
    """把文本发送回当前会话。"""

    action_name = "echo_send"
    action_description = "将给定文本直接发送到当前聊天流。适合在已经确定回复内容时使用。"
    primary_action = True

    async def execute(
        self,
        content: Annotated[str, "要发送给用户的文本内容"],
    ) -> tuple[bool, str]:
        """发送文本消息。"""
        text = content.strip()
        if not text:
            return False, "content 不能为空"

        success = await send_text(
            content=text,
            stream_id=self.chat_stream.stream_id,
            platform=self.chat_stream.platform,
        )

        if success:
            return True, "文本已发送"
        return False, "文本发送失败"
```

这段代码的关键点其实很少，但每个点都很像 Action。

## 14.8 这段 Action 代码里，最重要的不是返回值，而是副作用发生了

如果你盯着这个例子看，最容易忽略的一点反而是最核心的一点：

```python
success = await send_text(...)
```

这一步才是 Action 的主体。

返回的：

```python
return True, "文本已发送"
```

只是执行回执。

真正的价值在于：

- 一条消息已经被送到当前流
- 平台侧已经发生了可见效果

这和 Tool 那种“把结果交还给模型再继续推理”的感觉是很不一样的。

## 14.9 为什么示例里直接使用 `self.chat_stream`

这也是 Action 和 Tool 的差别之一。

在这个例子里，我们不需要自己再去想：

- 当前流 ID 是什么
- 平台是什么
- 这条回复到底该发到哪里

因为 Action 初始化时已经拿到了 `chat_stream`。

所以这里自然会写成：

```python
stream_id=self.chat_stream.stream_id,
platform=self.chat_stream.platform,
```

这正是 Action 很适合做“会话相关副作用”的原因。

## 14.10 要不要让 Action 自己做复杂文本处理

通常不建议。

这也是 Action 容易写歪的地方。

一个很常见的坏味道是：

- Action 里先做一大段文本分析
- 再做一堆规则判断
- 最后把消息发出去

这样写久了，你就会得到一个既像 Tool、又像 Service、又像 Action 的混合体。

更稳的做法通常是：

- 文本整理交给 Tool 或 Service
- Action 负责最后那一下“发出去”

也就是说：

> **Action 最好把自己收在执行层，而不是承担太多推理或加工职责。**

## 14.11 一个更稳的分工方式：Tool 产出文本，Action 负责发送

结合前几章的内容，最自然的组件搭配是：

1. `EchoFormatterTool` 负责加工文本
2. `EchoPolishAgent` 负责在多个文本工具之间做局部编排
3. `EchoSendAction` 负责把最终文本真正发送出去

这个分工非常顺：

- Tool 解决“怎么处理文本”
- Action 解决“把结果怎么发出去”

这样写出来的插件会明显更清楚，也更容易维护。

## 14.12 `primary_action` 该怎么理解

基类里还有一个容易被忽略的字段：

```python
primary_action = False
```

从插件作者视角，你可以先把它理解成一种语义标记：

> **这个 Action 是否更接近当前插件最主要的动作输出。**

它不是决定一切行为的魔法开关，但它能帮助你在设计组件时更清楚地表达：

- 这是插件核心动作
- 还是一个辅助动作

对入门阶段来说，先把它当成“主动作标记”就够了。

## 14.13 如果以后要做更丰富的 Action，思路也还是一样

一旦你理解了 `EchoSendAction`，其他类型的 Action 其实只是副作用介质不一样。

比如：

- 发送图片
- 发送语音
- 发送表情包
- 触发外部服务

它们的共性都不是“返回了什么信息”，而是：

> **它们真的对当前会话或外部系统做了某个动作。**

所以以后你看到别的 Action，大可以先问自己一句：

> 这个组件的核心价值，是不是“把某件事做出去”？

如果答案是“是”，那它大概率就是个合格的 Action 候选。

## 14.14 什么时候不该写成 Action

这个问题同样重要。

如果一个能力主要是在做：

- 查询
- 检索
- 判断
- 转换
- 汇总

那它大概率不该先写成 Action。

否则你很容易得到一种奇怪组件：

- 名字叫 Action
- 实际却不做副作用
- 只是返回一大段信息

这种组件最开始也许能跑，但文档、心智模型和后续编排都会越来越乱。

所以在真的下手前，最好先问一句：

> **它是在“拿信息”，还是在“做动作”？**

这句判断，通常已经能帮你避开大多数分层错误。

## 14.15 BaseAction 基类速查

`BaseAction` 定义于 `src/core/components/base/action.py`，继承自 `ABC` 和 `LLMUsable`。

### 类属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `action_name` | `str` | Action 名称，schema 中注册为 `action-{action_name}` |
| `action_description` | `str` | Action 描述，告知模型此 Action 的用途 |
| `primary_action` | `bool` | 是否为插件主动作，默认 `False` |
| `chatter_allow` | `list[str]` | 允许使用此 Action 的 Chatter 名称列表，为空则不限制 |
| `chat_type` | `ChatType` | 支持的聊天类型（`ALL` / `PRIVATE` / `GROUP`） |
| `associated_platforms` | `list[str]` | 关联的平台列表，为空则不限制 |
| `dependencies` | `list[str]` | 组件级依赖（其他组件的签名列表） |

### 实例属性

| 属性 | 说明 |
|------|------|
| `self.chat_stream` | 当前聊天流实例（天然绑定，可直接操作当前会话） |
| `self.plugin` | 所属插件实例 |

### 方法

| 方法 | 说明 |
|------|------|
| `execute(*args, **kwargs) -> tuple[bool, str]` | **抽象方法**，Action 核心逻辑，返回 `(成功标志, 结果详情)` |
| `go_activate() -> bool` | 激活判定，默认返回 `True`，可重写以实现条件激活 |
| `_random_activation(probability: float) -> bool` | 工具方法，按概率随机激活 |
| `_keyword_match(keywords, ...) -> bool` | 工具方法，基于关键词匹配激活 |
| `to_schema() -> dict` | 生成 LLM Tool Schema，schema 名称为 `action-{action_name}` |
| `get_signature() -> str \| None` | 返回组件签名，格式为 `{plugin}:action:{action_name}` |

### execute 参数规范

与 `BaseTool.execute` 相同，使用 `Annotated[type, "description"]` 标注参数：

```python
async def execute(
    self,
    text: Annotated[str, "要发送的文本内容"],
) -> tuple[bool, str]:
    await self.chat_stream.send_text(text)
    return True, "发送成功"
```

`execute` 的参数签名即对应 LLM Tool Schema 中的 `parameters`，`Annotated` 注解会被自动解析为参数描述。

### go_activate 示例

```python
async def go_activate(self) -> bool:
    # 50% 概率激活
    return await self._random_activation(0.5)
```

`go_activate` 在每次 Action 被加入模型可见列表前调用，返回 `False` 则该 Action 不会出现在当前轮次的 schema 中。

## 14.16 对插件作者来说，这一章最值得带走什么

把这一章压成几个最实用的结论，大概就是：

1. Action 不是另一种 Tool，它的核心定位是副作用和主动响应。
2. Tool 更适合把信息带回来，Action 更适合把动作做出去。
3. Action 天然绑定 `chat_stream`，所以非常适合做和当前会话直接相关的输出动作。
4. `go_activate()` 让 Action 很适合做“有条件暴露”的动作能力。
5. 最稳的分工通常是：Tool 处理信息，Action 负责落地执行。

## 14.17 把这一章压缩成一句话

如果要把这一章压缩成一句最值得带走的话，那就是：

> **在 Neo-MoFox 里，Action 负责把模型已经做出的决定真正执行到当前会话或外部系统里，它的价值不在于返回更多信息，而在于产生明确的副作用。**

沿着这条线，下一步自然会涉及：

> **当 Tool、Agent、Action 都已经出现以后，插件作者该怎样给它们划清职责，避免一个组件把所有事都做了？**

那就是完整组件分层的问题了。
