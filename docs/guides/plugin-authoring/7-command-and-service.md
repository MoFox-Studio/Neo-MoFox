# 7. 从单组件走向多组件

> **导读** 单个 Command 组件终究装不下所有逻辑。本章将 `echo_demo` 从"命令里直接做事"演进为"Command 负责入口、Service 负责能力"，这一步看似朴素，却是后续所有复杂插件设计的起点。本章同时系统性地介绍 `BaseCommand` 和 `BaseService` 的定义，帮助你真正理解这两类组件各自的边界。

到了这里，`echo_demo` 已经不再只是一个最小样例了。

它现在已经有：

- 一个插件类。
- 一个命令组件。
- 一个配置类。

这时候继续往前走，就会自然碰到一个新问题：

> **命令组件是不是应该一直把所有事情都做完？**

在最开始的样例里，这么写当然没问题。因为功能很小，逻辑也很短，直接把“接收命令”和“处理逻辑”放在一起，读起来反而最直观。

但只要插件稍微往真实使用迈一步，这种写法就会慢慢开始不够用。不是因为它立刻错了，而是因为它会越来越容易把不同职责揉在一起。

所以这一章先不展开 Tool，也不急着把插件复杂化。先只做一件事：

> **把 `echo_demo` 从“命令里直接做事”，演进到“命令负责入口，Service 负责能力”。**

这一步看起来很朴素，但它几乎是后面所有复杂插件设计的起点。

## 7.1 为什么单个 Command 迟早会显得拥挤

先想象一下，如果我们继续让 `EchoCommand` 承担所有逻辑，接下来插件需求一变，它可能很快就会开始长成这样：

- `/echo ping`
- `/echo say text`
- `/echo stats`
- `/echo format`
- `/echo history`

然后每个命令分支里，又各自带一点格式化、状态处理、文本拼接，甚至以后还会去调外部 API。

这时候 Command 文件最容易出现的问题，不是“不能工作”，而是“什么都往里塞”。

久而久之，Command 组件就会同时承担：

- 用户入口
- 参数解析
- 业务编排
- 数据处理
- 结果格式化

这里面最不该长期黏在一起的，通常就是“用户入口”和“可复用能力”。

因为命令入口天然是面向触发方式的，而真正的业务能力，往往不应该只服务于一个命令。

## 7.2 先建立一个最重要的分工认知

这一章最值得记住的一句话是：

> **Command 更像入口，Service 更像能力。**

你可以这样简单理解：

- **Command** 负责接收用户命令。
- **Service** 负责把某件事真正做出来。

也就是说，Command 更像“门”，Service 更像“房间里的功能”。

这个区分一旦建立起来，后面很多设计都会自然很多。因为你会开始本能地问自己：

- 这段代码是在处理命令入口，还是在实现能力本身？
- 这段逻辑以后会不会被别的组件复用？
- 这部分东西以后是不是还可能通过 API、Prompt、Tool 或别的方式被调用？

如果答案偏向“会”，那它通常更适合往 Service 方向走。

## 7.3 为什么先讲 Service，而不是立刻讲 Tool

这里有意放慢一点节奏。

很多人一听“多组件协作”，会立刻想跳到 Tool、Agent、Chatter 这些更有存在感的部分。但从学习曲线来说，先把 Service 理顺更稳。

原因很简单：

- Service 最接近普通开发者熟悉的“业务能力封装”。
- 它不像 Tool 那样天然和 LLM 调用绑定在一起。
- 它也不像 Agent、Chatter 那样带着更重的系统协作语义。

换句话说，Service 是最适合拿来理解“为什么要拆组件职责”的第一站。

而且这一步还有一个非常现实的好处：

> **你今天把逻辑抽成 Service，明天它就有机会被别的 Command、别的组件，甚至高层的 `service_api` 调用。**

这也是为什么这一章虽然还没正式进入 API 专题，但已经可以明确点出来：Service 不只是插件内部整理代码的一种方式，它也是后续 API 协作的基础接口面之一。

## 7.4 先看一个目标版本

这一章做完之后，我们希望 `echo_demo` 大致变成这样：

```text
echo_demo/
├── manifest.json
├── plugin.py
├── config.py
└── service.py
```

这里故意没有拆太多文件。我们只多加一个 `service.py`，就够了。

因为这一章要建立的是职责边界，不是文件数量。

## 7.5 先写 EchoService

新增一个 `service.py`：

```python
from __future__ import annotations

from src.app.plugin_system.base import BaseService


class EchoService(BaseService):
    """EchoDemo 的核心回显能力。"""

    service_name = "echo_service"
    service_description = "提供基础的回显与文本处理能力"

    async def ping(self) -> str:
        """返回最简单的连通性响应。"""
        return "pong"

    async def echo_text(self, text: str) -> str:
        """回显一段文本。"""
        return f"echo: {text}"
```

如果你盯着这段代码看，会发现它和 Command 有一个很明显的气质差异：

- 它不关心命令前缀。
- 它不关心子路由。
- 它不关心用户是怎么触发它的。

它只关心一件事：

> **如果有人要我提供回显能力，我该返回什么。**

这就是 Service 最核心的味道。

## 7.6 再改 EchoCommand

有了 Service 之后，命令组件就不需要继续把所有逻辑都扛在自己身上了。

这时候可以把 `EchoCommand` 改成这样：

```python
from __future__ import annotations

from src.app.plugin_system.base import BaseCommand, BasePlugin, cmd_route, register_plugin
from src.app.plugin_system.api.send_api import send_text

from .config import EchoDemoConfig
from .service import EchoService


class EchoCommand(BaseCommand):
    """最小回显命令。"""

    command_name = "echo"
    command_description = "一个用于演示插件系统的最小回显命令"
    command_prefix = "/"

    async def _get_service(self) -> EchoService:
        """创建当前插件对应的 EchoService 实例。"""
        return EchoService(self.plugin)

    @cmd_route("ping")
    async def handle_ping(self) -> tuple[bool, str]:
        """检查命令是否已经正常工作。"""
        service = await self._get_service()
        result = await service.ping()
        await send_text(result)
        return True, result

    @cmd_route("say")
    async def handle_say(self, text: str) -> tuple[bool, str]:
        """回显一段文本。"""
        service = await self._get_service()
        result = await service.echo_text(text)
        await send_text(result)
        return True, result


@register_plugin
class EchoDemoPlugin(BasePlugin):
    """最小回显插件。"""

    plugin_name = "echo_demo"
    plugin_description = "一个用于演示插件加载与命令执行的最小插件"
    plugin_version = "1.0.0"

    configs: list[type] = [EchoDemoConfig]

    def get_components(self) -> list[type]:
        """返回当前插件包含的组件。"""
        if isinstance(self.config, EchoDemoConfig) and not self.config.plugin.enabled:
            return []
        return [EchoCommand, EchoService]
```

你会发现，命令本身并没有消失，它依然是用户最直接接触到的入口。

但它现在的角色已经比前面清楚很多了：

- 它负责接收 `/echo ping` 和 `/echo say`。
- 它负责把参数交给更合适的能力层。
- 它自己不再承担具体的回显实现。

这就是这一步最关键的变化。

## 7.7 get_components 为什么也要跟着变

既然我们已经新增了一个 Service，那插件类自然也要把它纳入组件列表：

```python
return [EchoCommand, EchoService]
```

这一步的意义很简单：

> 系统只有在你明确返回它之后，才会把这个 Service 当成正式组件注册进去。

所以千万不要把“我已经写了一个 `service.py`”误以为“系统自然会知道它存在”。

不会。插件系统只认插件明确交出来的组件。

这也是为什么我一直在反复强调：插件结构和运行时注册，是两回事。文件存在，不代表组件已经进入系统；类写出来，不代表它已经被系统接纳。

## 7.8 现在 Command 和 Service 的边界清楚了吗

如果把这一章最核心的分工压缩一下，现在其实可以变得很清楚：

### Command 负责什么

- 对应用户触发入口。
- 组织命令路由。
- 接收参数。
- 调用更合适的能力层。

### Service 负责什么

- 承载插件内部真正可复用的业务能力。
- 不关心命令前缀和路由形式。
- 可以被多个组件复用。
- 天然更适合后续通过高层 API 暴露出去。

这一步的价值不只是“代码更整洁”，而是你开始有了可扩展的能力边界。

## 7.9 这一步为什么也和 API 有关系

这一章虽然表面上是在讲多组件拆分，但其实已经悄悄碰到了后面更大的主题：API。

原因很简单。

当某个能力被抽成 Service 之后，它就不再只是“Command 里面的一个内部实现细节”了。它开始具备被系统其他部分调用的可能性。

比如当前项目里就已经有高层的 `service_api`：

```python
from src.app.plugin_system.api import service_api

service = service_api.get_service("echo_demo:service:echo_service")
```

你现在当然还不一定要立刻在教程里把这段用起来，但你应该开始意识到：

> **Service 一旦存在，它就有机会从“插件内部能力”变成“插件系统中的公开能力”。**

这也是为什么我说，Command 和 Service 的拆分不只是整理代码，它是在给后续更复杂的协作打基础。

## 7.10 那 Tool 呢，为什么这章不正式展开

因为 Tool 虽然也会承载能力，但它的语义和 Service 并不完全一样。

Service 更像：

- 面向程序调用
- 面向插件内或插件间复用
- 面向系统能力暴露

而 Tool 更像：

- 面向 LLM 调用
- 面向 schema 暴露
- 面向模型在运行时选择使用

所以它们虽然都能承载“能力”，但服务的调用场景并不相同。

也正因为这样，这一章先把 Service 理顺，下一章再把 Tool 拉进来，节奏会更稳。否则读者很容易把两者都理解成“反正都是把逻辑拆出去”，结果边界反而更模糊。

## 7.11 这一章真正想帮你建立的，不只是拆文件习惯

如果你只把这一章理解成“把一个类拆成两个类”，那其实还没抓到最重要的东西。

这一章真正想帮你建立的是一种判断：

> **凡是和触发方式强绑定的东西，更像入口；凡是和能力本身更绑定的东西，更适合往 Service 方向走。**

这个判断一旦建立起来，后面很多组件设计你都会更有把握。因为你已经不再是凭感觉拆代码，而是在按职责拆。

## 7.13 `BaseCommand` 基类速查

### 类属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `command_name` | `str` | `""` | 命令名称，用于组件签名和路由识别 |
| `command_description` | `str` | `""` | 命令描述 |
| `command_prefix` | `str` | `"/"` | 命令触发前缀 |
| `permission_level` | `PermissionLevel` | `PermissionLevel.USER` | 执行权限级别 |
| `chat_type` | `ChatType` | `ChatType.ALL` | 支持的聊天类型 |
| `associated_platforms` | `list[str]` | `[]` | 关联的平台列表，空列表表示所有平台 |
| `dependencies` | `list[str]` | `[]` | 组件级依赖列表，填写组件签名字符串 |

### 主要方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_signature()` | `cls → str \| None` | 返回 `plugin_name:command:command_name` 格式的签名 |

### 相关装饰器

**`@cmd_route(*path: str)`**

注册命令路由。参数为命令路径片段（可多级），处理函数须为 `async def`，返回 `tuple[bool, str]`。

```python
@cmd_route("set", "seconds")
async def handle_set_seconds(self, value: int) -> tuple[bool, str]:
    return True, f"已设置：{value} 秒"

@cmd_route("get")
async def handle_get(self) -> tuple[bool, str]:
    return True, "当前值"
```

命令处理函数的参数会根据类型注解自动解析。

---

## 7.14 `BaseService` 基类速查

### 类属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `service_name` | `str` | `""` | 服务名称，用于组件签名和注册 |
| `service_description` | `str` | `""` | 服务描述 |
| `version` | `str` | `"1.0.0"` | 服务版本 |
| `dependencies` | `list[str]` | `[]` | 组件级依赖列表 |

### 主要方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_signature()` | `cls → str \| None` | 返回 `plugin_name:service:service_name` 格式的签名 |

`BaseService` 不强制要求实现任何抽象方法，插件作者可以自由定义业务方法。对外暴露时可实现 `typing.Protocol` 所定义的接口（如 `MemoryService`、`ConfigService` 等），以便其他插件通过 `service_api` 按协议调用。


## 7.15 这一章压缩成一句话

如果要把这一章压缩成一句最值得带走的话，那我会建议你记住这句：

> **Command 负责接收请求，Service 负责把能力做出来；把两者分开，是插件从“能跑”走向“能扩展”的第一步。**

下一章，我们就顺着这一步继续往前走：把 Tool 正式拉进来。到那个时候，你会第一次明显感受到，为什么“可复用能力”和“可被模型调用的能力”虽然相似，但不能混成一类。