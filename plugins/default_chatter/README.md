# Default Chatter

本文将 `plugins/default_chatter` 简称为 `DFC`。  
它现在不只是一个“默认聊天器”，而是一套可复用的聊天能力核心：

- 对框架而言，它继续提供 `default_chatter` 这个标准 `chatter` 组件。
- 对插件开发者而言，它额外提供 `default_chatter:service:chat_core`，可以直接创建并驱动完整的聊天会话。
- 对能力封装而言，它已经把对话状态机、请求链路、工具执行、sub-agent 协作、多模态注入、`wait/stop/resume` 语义统一收拢进 `Session` 核心。

目标是让外部调用者只关心两件事：

- 我要用哪条流 `stream_id`
- 我要给这个会话配置什么参数或适配器

而不需要再关心内部 LLM 请求是怎么构建的、工具是怎么串起来的、`WaitResumeEvent` 什么时候继续推进。

## 跨插件调用约束

这一点很重要：

- 外部插件不能直接 `import plugins.default_chatter.*`
- 外部插件不能依赖 `default_chatter` 的内部模块路径
- 外部插件只能通过框架提供的 API 获取组件

对于 DFC 来说，跨插件的标准入口是：

- `src.app.plugin_system.api.service_api.get_service(...)`

也就是说，下面这种写法对外部插件来说是错误示例：

```python
from plugins.default_chatter.service import DefaultChatterService
from plugins.default_chatter.type_defs import DefaultChatterSessionOptions
```

正确思路应该是：

```python
from src.app.plugin_system.api.service_api import get_service

service = get_service("default_chatter:service:chat_core")
```

本文后面凡是出现 `plugins.default_chatter.*` 的 import，都应理解为：

- `default_chatter` 插件内部实现示例
- 框架内部代码示例
- 单元测试示例

而不是跨插件调用方式。

## 组件清单

`default_chatter` 插件当前对外注册了这些组件：

- `default_chatter:service:chat_core`
  `DefaultChatterService`，聊天核心工厂。
- `default_chatter:chatter:default_chatter`
  `DefaultChatter`，框架默认聊天器，现已变成 `Service + Session` 的薄适配层。
- `default_chatter:action:send_text`
  发送文本消息。
- `default_chatter:action:pass_and_wait`
  跳过本轮并进入等待，可选定时唤醒。
- `default_chatter:action:stop_conversation`
  结束当前对话轮次，可选冷却时间。

此外，当启用 sub-agent 协作时，DFC 还会向主会话注入管理型 agent usable：

- `create_agent`
- `get_agent`
- `kill_agent`

## DFC 能力概览

DFC 是一个“完整对话回合执行器”，不是一个只负责发请求的轻封装。它内建了以下能力。

### 1. 完整的四相状态机

会话核心 `DefaultChatterSession` 内部维护固定的四相 FSM：

- `WAIT_USER`
  等待用户消息，或等待外部恢复事件。
- `MODEL_TURN`
  由模型做出本轮决策并输出文本 / tool calls。
- `TOOL_EXEC`
  执行模型本轮给出的工具调用，并把结果回写进上下文。
- `FOLLOW_UP`
  在工具结果尾部、等待超时、sub-agent 完成等场景下继续推进后续决策。

这套状态机是 DFC 行为一致性的核心，外部调用者不需要自己拼接这些阶段。

### 2. 完整请求链路封装

DFC 会在 session 内统一完成这些步骤：

- 激活 `ChatStream`
- 创建 LLM request
- 构建 system prompt
- 合并历史消息
- 读取未读消息
- 注入 tools / actions / agents
- 发送模型请求
- 执行 tool calls
- 根据结果继续 follow-up 或进入 wait / stop

调用者只需要拿到 session 并驱动 `execute()`。

### 3. 未读消息合并与 flush

DFC 会统一处理未读消息的生命周期：

- 拉取未读
- 格式化未读内容
- 作为用户输入注入到当前会话上下文
- 在合适时机 flush 已消费的未读消息

这保证了“会话上下文”和“平台消息状态”之间的一致性。

### 4. 工具链路与 Action 语义统一

DFC 会把 LLM 输出的工具调用统一编排为一个回合中的执行链路，并内建这些约定：

- 支持普通 tool / action / agent 的混合调用
- 跟踪本轮已使用工具，避免链路失控
- 处理工具回写后的 follow-up
- 保留 `pass_and_wait` / `stop_conversation` 的控制流语义
- 支持 action-only 回合的 suspend 注入

其中三类核心 action 的职责分别是：

- `send_text`
  唯一标准文本发送路径。
- `pass_and_wait`
  本轮不再继续，挂起并等待新消息或定时器恢复。
- `stop_conversation`
  结束本轮对话，并把控制权交回外部调度。

### 5. Wait / Resume / Stop 语义内建

DFC 的 session 不是普通函数，而是一个异步生成器：

- 产出 `Wait`
- 产出 `Success`
- 产出 `Failure`
- 产出 `Stop`
- 接收 `WaitResumeEvent | None` 来恢复执行

这意味着它天然适合接入 Neo-MoFox 当前的流式调度框架，也适合被其他插件作为聊天执行核心复用。

### 6. Sub-agent 协作

当启用 `enable_sub_agent_collaboration` 后，DFC 会把子代理协作纳入主会话流程中：

- 主会话可创建子代理
- 子代理状态按 `stream_id` 聚合管理
- 子代理完成后可通过 resume 事件回灌主会话
- 主会话可查询 / 销毁已管理的子代理
- 协作模式下可对可用 tools / MCP 能力做受控暴露

这套协作能力已经内聚进 session 核心，而不是散落在外部调用链里。

### 7. Programmatic Controller

DFC 支持“程序化先验控制”，用于在正式发起一次 LLM 判定前做额外 gating。

目前它主要用于：

- sub-agent 场景下的概率直通响应
- 降低不必要的模型决策开销
- 提高在群聊环境中的响应策略可控性

### 8. 多模态消息注入

当启用 `native_multimodal` 后，DFC 会在 unread 合并阶段支持图片内容进入会话输入，并主动为当前流跳过默认 VLM 图片识别流程，避免重复处理。

### 9. Prompt 注入与主题引导

DFC 的 prompt 由多个层次组合而成：

- system prompt
- 历史消息文本
- unread user prompt
- 负面行为强化
- 按私聊 / 群聊区分的 `theme_guide`
- sub-agent 协作补充说明

你可以直接复用这套 prompt 编排，也可以通过 adapter seam 换掉其中的实现。

## 架构模型

现在的 DFC 推荐理解为三层：

### 1. `DefaultChatterService`

负责创建会话，不保存运行态。

### 2. `DefaultChatterSession`

真正的聊天能力核心。  
单个 session 持有一个会话完整生命周期所需的可变状态。

### 3. `DefaultChatter`

框架默认 `chatter` 组件。  
它现在主要负责读取插件配置、组装默认 adapters，然后把执行委托给 session。

推荐依赖方向如下：

```text
外部调用者 / 其他插件
        ↓
DefaultChatterService
        ↓
DefaultChatterSession
        ↓
adapters（request / prompt / unread / tool / sub-agent / logger）
```

## Public API

这里的 “Public API” 指的是 DFC 对外承诺的能力边界，不等于“建议你跨插件直接 import 这些类”。  
跨插件使用时，仍然应该通过框架 API 获取 service 实例，再调用其方法。

### `DefaultChatterService`

签名：

```python
"default_chatter:service:chat_core"
```

主要入口：

```python
create_session(
    *,
    stream_id: str,
    options: DefaultChatterSessionOptions | None = None,
    adapters: DefaultChatterSessionAdapters | None = None,
) -> DefaultChatterSession

create_default_session(
    *,
    stream_id: str,
    plugin: BasePlugin,
    chatter: BaseChatter | None = None,
    options: DefaultChatterSessionOptions | None = None,
) -> DefaultChatterSession
```

使用建议：

- 如果你想直接复用框架默认实现，优先用 `create_default_session(...)`
- 如果你想把 DFC 当成一个更底层的会话引擎，自己提供 request / prompt / unread / tool 等实现，则用 `create_session(...)`

补充说明：

- 对外部插件而言，推荐只把 `create_default_session(...)` 视为稳定接入方式
- `create_session(...)` 依赖 adapter / options 类型，当前更适合 `default_chatter` 插件内部、框架内部或同仓库受控代码使用
- 如果未来需要把自定义 adapter 能力开放为正式跨插件协议，建议先上升到框架级 API 或 protocol，而不是直接依赖本插件内部模块

### `DefaultChatterSession`

主入口：

```python
execute() -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]
```

这是最重要的公共接口。它会：

- 自动激活 `stream_id` 对应的 `ChatStream`
- 自动拉起完整对话状态机
- 在需要等待时 yield `Wait`
- 在恢复后继续执行
- 在结束时 yield `Success`、`Failure` 或 `Stop`

高级用法：

```python
execute_with_stream(
    chat_stream: ChatStream,
    *,
    apply_stop_wake_config: bool,
) -> AsyncGenerator[Wait | Success | Failure | Stop, WaitResumeEvent | None]
```

这个接口更偏内部 / 高级接入场景。一般插件调用只需要 `execute()`。

## 快速开始

### 方式一：通过 Service 获取默认聊天核心

如果你只是想在自己的插件里“跑通一条完整聊天链路”，这是最推荐的方式。

```python
from src.app.plugin_system.api.service_api import get_service

service = get_service("default_chatter:service:chat_core")
if service is None:
    raise RuntimeError("default_chatter service is not available")

session = service.create_default_session(
    stream_id="my-stream-id",
    plugin=service.plugin,
)
```

说明：

- `get_service(...)` 每次都会返回一个新的 service 实例
- `create_default_session(...)` 每次都会返回一个新的 session 实例
- session 是有状态对象，不应该跨多条会话流复用

### 方式二：在已有 `BaseChatter` 运行时上创建 session

这个方式主要面向 `default_chatter` 插件内部或框架内部代码。  
如果你是在“另一个插件”里使用 DFC，请优先使用前一种 `get_service(...)` 的方式，而不是直接 import 本插件内部模块。

如果你已经有一个 `BaseChatter` 子类实例，希望直接复用它作为默认 adapters，可以显式传入 `chatter`：

```python
from plugins.default_chatter.service import DefaultChatterService

service = DefaultChatterService(plugin)
session = service.create_default_session(
    stream_id=chatter.stream_id,
    plugin=plugin,
    chatter=chatter,
)
```

这样 session 会直接复用该 chatter 的：

- request 创建能力
- prompt 构建能力
- unread 读取与 flush 能力
- tool 注入与执行能力
- sub-agent 判定能力

### 方式三：把 DFC 当成底层会话引擎，自定义 adapters

这个方式同样主要面向 `default_chatter` 插件内部、框架内部或受控的同仓库代码。  
它不是推荐的跨插件接入方式，因为跨插件场景下不应直接 import `plugins.default_chatter.type_defs`。

当你不想依赖 `DefaultChatter` 本身，而是只想复用它的状态机和控制流时，可以自己提供 adapters。

```python
from plugins.default_chatter.service import DefaultChatterService
from plugins.default_chatter.type_defs import (
    DefaultChatterSessionAdapters,
    DefaultChatterSessionOptions,
)

service = DefaultChatterService(plugin)

adapters = DefaultChatterSessionAdapters(
    request_adapter=my_request_runtime,
    prompt_adapter=my_prompt_runtime,
    unread_adapter=my_unread_runtime,
    usable_adapter=my_usable_runtime,
    tool_execution_adapter=my_tool_runtime,
    sub_agent_adapter=my_sub_agent_runtime,
    logger_adapter=my_logger,
)

options = DefaultChatterSessionOptions(
    actor_task_name="actor",
    sub_actor_task_name="sub_actor",
    enable_cooldown=True,
    enable_action_suspend=True,
    enable_programmatic_controller=True,
    enable_sub_agent_collaboration=False,
    enable_stop_direct_message_wake=False,
    stop_direct_message_wake_probability=0.0,
    native_multimodal=False,
    theme_guide={
        "private": "私聊引导词",
        "group": "群聊引导词",
    },
    negative_behavior_reinforcement=True,
)

session = service.create_session(
    stream_id="my-stream-id",
    options=options,
    adapters=adapters,
)
```

## 如何驱动一个 Session

`DefaultChatterSession.execute()` 是异步生成器，需要像调度器一样驱动。

最小示例：

```python
from src.core.components.base import Failure, Stop, Success, Wait
from src.core.components.base import WaitResumeEvent


async def run_session(session) -> None:
    runner = session.execute()
    resume_event: WaitResumeEvent | None = None

    while True:
        try:
            result = await runner.asend(resume_event)
        except StopAsyncIteration:
            return

        resume_event = None

        if isinstance(result, Wait):
            resume_event = await wait_for_resume_event()
            continue

        if isinstance(result, Success):
            return

        if isinstance(result, Stop):
            return

        if isinstance(result, Failure):
            raise RuntimeError(result.message)
```

你可以把它理解为：

- session 负责“计算下一步该做什么”
- 外部调度器负责“在 yield 之后何时继续”

### 恢复事件来源

DFC 典型会接收这些恢复来源：

- 新消息触发
- `pass_and_wait` 设置的 timer 触发
- sub-agent 完成事件

对于 session 来说，它只接收统一的 `WaitResumeEvent`，不需要知道外部恢复事件来自哪个具体子系统。

## `DefaultChatterSessionOptions`

`DefaultChatterSessionOptions` 是 session 的显式配置入口。

### `actor_task_name`

主对话 actor 所使用的模型任务名。默认是 `actor`。

### `sub_actor_task_name`

sub-agent 协作时子代理使用的模型任务名。默认也是 `actor`。

### `enable_cooldown`

是否允许 `stop_conversation` 引入冷却语义。

### `enable_action_suspend`

是否在 action-only 回合中注入 suspend 占位，确保对话状态闭合。

### `enable_programmatic_controller`

是否启用程序化前置控制逻辑，例如 sub-agent 场景下的概率直通。

### `enable_sub_agent_collaboration`

是否启用子代理协作能力。

### `enable_stop_direct_message_wake`

当会话返回 `Stop` 时，是否允许私聊直唤重新激活该流。

### `stop_direct_message_wake_probability`

`Stop` 状态下私聊直唤的概率值，范围建议为 `0.0 ~ 1.0`。

### `native_multimodal`

是否让 unread 合并路径直接承载图片等多模态内容。

### `theme_guide`

按聊天场景区分的主题引导文本，通常包含：

- `private`
- `group`

### `negative_behavior_reinforcement`

是否在 prompt 中追加负面行为强化提醒。

## `DefaultChatterSessionAdapters`

`DefaultChatterSessionAdapters` 是 DFC 对外暴露的少量真实 seam。  
它的设计目标是“允许替换能力来源”，而不是“允许外部改写整个会话流程”。

### `request_adapter`

负责创建 LLM request。需要实现：

```python
create_request(
    task: str = "actor",
    request_name: str = "",
    with_reminder: str | None = None,
)
```

### `prompt_adapter`

负责 prompt 构建，需要提供：

- `_build_system_prompt(chat_stream)`
- `_build_enhanced_history_text(chat_stream)`
- `_build_user_prompt(chat_stream, history_text, unread_lines, extra="")`
- `_build_negative_behaviors_extra()`

### `unread_adapter`

负责 unread 生命周期，需要提供：

- `fetch_unreads(...)`
- `format_message_line(...)`
- `_upsert_pending_unread_payload(...)`
- `flush_unreads(...)`

### `usable_adapter`

负责把 tools / actions / agents 注入请求上下文：

- `inject_usables(request)`

### `tool_execution_adapter`

负责真正执行模型给出的工具调用：

- `run_tool_call(calls, response, usable_map, trigger_msg)`

### `sub_agent_adapter`

负责 unread 到来时的 sub-agent 判定：

- `sub_agent(unreads_text, unread_msgs, chat_stream)`

### `logger_adapter`

负责日志输出和决策面板打印。  
需要支持：

- `info`
- `warning`
- `error`
- `debug`
- `print_panel`

## 作为框架默认 Chatter 使用

如果你不需要手动拿 service，那么 DFC 仍然可以像以前一样作为普通 `chatter` 组件运行：

```text
default_chatter:chatter:default_chatter
```

区别在于现在的 `DefaultChatter` 已经是薄适配层：

- 读取插件配置
- 组装 `DefaultChatterSessionOptions`
- 构造默认 adapters
- 委托给 `DefaultChatterSession.execute()`

也就是说：

- 以前你使用的是“一个比较重的 chatter 类”
- 现在你使用的是“一个基于 service/session 核心的 chatter 外壳”

## Action 约定

### `send_text`

DFC 唯一标准文本输出路径。

适合：

- 普通文本回复
- 多段连续发送
- 回复指定消息
- 指定 `at` 对象

不适合：

- 非文本媒体发送
- 把思维过程或工具调用理由混入 `content`

### `pass_and_wait`

用于本轮暂不继续，并把控制流切换到等待态。  
可选传入等待秒数，适合：

- 等用户下一条消息
- 设定一个稍后主动继续的 timer
- 工具链暂不需要再做 follow-up 的场景

### `stop_conversation`

用于显式结束本轮对话。  
与 `pass_and_wait` 的区别是：

- `pass_and_wait` 是“挂起，准备恢复”
- `stop_conversation` 是“结束这一轮，让外部决定何时再次启动”

## Sub-agent 协作说明

启用 `enable_sub_agent_collaboration` 后，DFC 会进入主代理协调模式。

此时主会话可通过注入的管理型 usable：

- 创建子代理
- 查询子代理状态
- 销毁子代理及其级联后代

协作链路具备这些特性：

- 子代理状态按 `stream_id` 聚合
- 子代理完成后可触发主会话 resume
- 主会话 follow-up 时可读取子代理回灌结果
- 协作场景下普通工具与 MCP 能力会受到更严格的暴露控制

如果你的插件需要“主代理编排多个子执行单元”的模式，DFC 已经提供了比较完整的骨架。

## 配置说明

DFC 插件的配置类为 `DefaultChatterConfig`。  
常用配置项主要集中在 `plugin` 段。

示例：

```toml
[plugin]
enabled = true
reinforce_negative_behaviors = true
enable_cooldown = true
enable_programmatic_controller = true
enable_action_suspend = true
enable_sub_agent_collaboration = false
sub_agent_task_name = "actor"
enable_stop_direct_message_wake = false
stop_direct_message_wake_probability = 0.0
native_multimodal = false

[plugin.theme_guide]
private = ""
group = ""
```

字段含义：

- `enabled`
  是否启用插件。
- `reinforce_negative_behaviors`
  是否在 prompt 中强化负面行为限制。
- `enable_cooldown`
  是否启用 stop 后冷却语义。
- `enable_programmatic_controller`
  是否启用程序化控制逻辑。
- `enable_action_suspend`
  是否启用 action-only suspend 占位。
- `enable_sub_agent_collaboration`
  是否启用子代理协作。
- `sub_agent_task_name`
  子代理使用的模型任务名。
- `enable_stop_direct_message_wake`
  `Stop` 后是否允许私聊直唤。
- `stop_direct_message_wake_probability`
  私聊直唤概率。
- `native_multimodal`
  是否启用原生多模态 unread 注入。
- `theme_guide.private`
  私聊主题引导。
- `theme_guide.group`
  群聊主题引导。

## 适合哪些场景

DFC 适合这些类型的插件或能力模块：

- 想复用完整聊天链路，而不想再自己拼装请求流程
- 想把“聊天能力”当成 SDK / 工具包嵌入自己的插件
- 想保留 Neo-MoFox 当前 wait/resume/stop 调度语义
- 想接入 sub-agent 协作
- 想在不改会话状态机的前提下替换 prompt / unread / tool 执行实现

如果你只是需要“一次性发一个 LLM 请求”，DFC 可能偏重。  
如果你需要“可持续推进的一条聊天会话链路”，DFC 就是更合适的抽象。

## 使用建议

- 一个 `stream_id` 的一次运行，创建一个新的 session。
- 不要在多个流之间复用同一个 session。
- 跨插件场景优先通过 `get_service("default_chatter:service:chat_core")` + `create_default_session(...)` 接入。
- 只有在插件内部或框架内部代码里，才建议自己组 adapters 或直接引用 `plugins.default_chatter` 内部模块。
- 把 DFC 当成“聊天执行核心”，不要把它当成单纯 prompt helper。
- 如果你要自定义 adapters，尽量保持语义兼容，而不是绕过 session 自己改流程。

## 当前设计边界

DFC 已经把主要聊天链路都收进了 service/session 核心，但仍保留了清晰边界：

- `send_text` / `pass_and_wait` / `stop_conversation` 仍然是插件层 action 组件
- service 是工厂，不保存会话运行态
- session 才保存单次运行的状态
- 外部通过 options 和 adapters 定制行为，而不是通过继承 session 改流程

这也是它更像“聊天 SDK / 框架工具包”的原因。

## 最后总结

可以把现在的 DFC 理解成：

- 对框架：默认聊天器
- 对插件：可复用聊天 service
- 对会话：完整状态机核心
- 对扩展方：基于 options + adapters 的能力包装层

如果你想在 Neo-MoFox 里复用一条完整、可恢复、可挂起、可协作、可注入工具链的聊天执行流程，优先考虑直接接入：

```text
default_chatter:service:chat_core
```
