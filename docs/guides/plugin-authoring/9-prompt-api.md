# 9. 系统能力导览：先认识 Prompt API

写到这里，`echo_demo` 已经有了 Command、Service、Config 和 Tool。

如果继续只盯着“组件怎么写”，接下来很容易开始觉得插件系统像一堆零件的拼装说明书：这个组件做什么，那个组件做什么，写法都知道了，但这些能力怎么真正组织成一段可用的上下文，反而还是模糊的。

这时候，把视线转到 Prompt API 会很合适。

因为它处理的不是“再加一个组件”，而是另一类问题：

> **当插件已经开始有能力时，你准备怎样把这些信息组织成一段可用的 prompt。**

这一章我们先不碰真正的模型调用，也不急着接入完整对话链。先只做两件事：

- 认识 PromptTemplate 是怎么注册、取回和渲染的。
- 认识 system reminder 是什么，以及它在系统里扮演什么角色。

把这两件事看顺了，后面再接 LLM API，你会轻松很多。

## 9.1 先建立一个最小认知：Prompt API 不等于“直接调模型”

第一次看到 `prompt_api`，很多人会下意识把它理解成“发起模型请求前的一个辅助工具”。

这么理解不能算错，但还不够准确。

更贴近实际的说法是：

> **Prompt API 负责组织 prompt 资产，不负责替你完成模型调用。**

这里的“prompt 资产”，可以先理解成两类：

- **模板**：一段可复用的 prompt 骨架。
- **提醒**：一段可以被系统保存和取回的补充性上下文。

所以这一章你看到的重点会是：

- 怎么把一段 prompt 模板放进系统里。
- 怎么在真正需要的时候把它取出来并填值。
- 怎么存一段 reminder，供后续流程按需取用。

而真正“把 prompt 发给模型”这件事，我们留到后面的 LLM API 再接上。

## 9.2 当前这块 API 的导入边界，要先说清楚

前面我们一直在尽量遵循一个原则：

> **插件作者优先从 `src.app.plugin_system` 这层导入，而不是直接碰更底层实现。**

这一点在 Prompt 这里现在已经顺了一些。当前更推荐这样理解：

- `prompt_api` 本身来自 `src.app.plugin_system.api`
- `PromptTemplate` 这类常用类型，可以从 `src.app.plugin_system.types` 导入

也就是说，这一章的写法会是这样的：

```python
from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.types import PromptTemplate
```

所以这里最稳妥的理解方式是：

- **模板对象本身**，优先从 `plugin_system.types` 拿。
- **模板的注册、查询和 reminder 管理**，优先走 `prompt_api`。

你也可以顺手记住一件事：`plugin_system.types` 现在不只是为了这章临时存在的一层。

它更适合被理解成：

- 插件作者常用类型的统一入口。
- 放 `PromptTemplate`、`ChatType`、`Message`、`ChatStream`、`ROLE` 这类高频类型的位置。

也就是说，后面你在插件里需要拿到运行时消息模型、聊天流模型、常见枚举或 LLM 内容类型时，优先先看看 `src.app.plugin_system.types`，而不是下意识往 `src.core` 或 `src.kernel` 里继续翻。

## 9.3 先给 `echo_demo` 注册一个模板

我们先不把事情做复杂。

这一章给 `echo_demo` 加一个最小模板：`echo_demo.reply`。

这个模板不负责真正调用模型，它只负责把插件当前关心的信息组织成一段完整 prompt。比如：

- 用户输入了什么
- 当前想要什么格式模式
- 这段回复应该保持什么风格

可以先在 `plugin.py` 里加一段注册逻辑：

```python
from __future__ import annotations

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.base import BaseCommand, BasePlugin, cmd_route, register_plugin
from src.app.plugin_system.types import PromptTemplate

from .config import EchoDemoConfig
from .service import EchoService
from .tool import EchoFormatterTool


class EchoCommand(BaseCommand):
    ...


@register_plugin
class EchoDemoPlugin(BasePlugin):
    """最小回显插件。"""

    plugin_name = "echo_demo"

    configs: list[type] = [EchoDemoConfig]

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后注册一个可复用 prompt 模板。"""
        prompt_api.register_template(
            PromptTemplate(
                name="echo_demo.reply",
                template=(
                    "你正在处理 echo_demo 插件的文本任务。\n"
                    "用户输入：{user_input}\n"
                    "格式模式：{mode}\n"
                    "请输出一条简洁、自然、不过度发挥的回复。"
                ),
            )
        )

    def get_components(self) -> list[type]:
        """返回当前插件包含的组件。"""
        if isinstance(self.config, EchoDemoConfig) and not self.config.plugin.enabled:
            return []
        return [EchoCommand, EchoService, EchoFormatterTool]
```

这里你可以先只抓住一句话：

> **模板一旦注册，就可以在后续任何需要的地方按名称取回。**

这和把一段 prompt 字符串散落在各个函数里，是完全不同的感觉。

前者是在建设“系统里的可复用 prompt 资源”，后者只是临时拼接字符串。

## 9.4 为什么这里放在 `on_plugin_loaded()` 里

这个位置不是唯一选择，但很适合入门阶段理解。

因为它表达得很清楚：

- 插件被系统正式加载完成。
- 然后插件把自己需要的 prompt 资源注册进去。

这样读起来有一种很自然的时序感。

你可以把它理解成：

> **插件启动时，顺手把自己要用的 prompt 资产也准备好。**

当然，后面如果你的模板数量很多，或者你希望专门拆一个模块来管理模板，也完全可以再演进。但入门阶段先把注册动作放在插件生命周期里，最容易看懂。

## 9.5 然后在 Service 里把它取出来

模板注册完之后，下一步不是立刻调模型，而是先把“取回模板并构建 prompt”这一步走通。

这时候很适合把逻辑放进 `service.py`，因为它本质上还是一段可复用能力：

```python
from __future__ import annotations

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.base import BaseService


class EchoService(BaseService):
    """EchoDemo 的核心回显能力。"""

    service_name = "echo_service"
    service_description = "提供基础的回显与文本处理能力"

    async def build_reply_prompt(self, text: str, mode: str) -> str:
        """构建一段供后续模型使用的 prompt。"""
        template = prompt_api.get_template("echo_demo.reply")
        if template is None:
            return "你正在处理 echo_demo 插件的文本任务。"

        return await (
            template.set("user_input", text)
            .set("mode", mode)
            .build()
        )
```

这段代码有一个非常值得注意的细节：

```python
template = prompt_api.get_template("echo_demo.reply")
```

拿到的不是全局模板本体，而是一个可供当前调用使用的模板副本。这个设计很重要，因为它意味着你后面继续：

```python
template.set("user_input", text)
```

不会把全局模板污染掉。

这点一旦理解透，你对 PromptTemplate 的使用会踏实很多。因为它不是“大家公用同一份带状态的对象”，而更像“从注册中心取一份当前可操作副本”。

## 9.6 现在 Command 里可以先把它当成普通文本能力来验证

既然 `EchoService` 已经能构建 prompt，那命令层就可以先拿它做一个很朴素的验证。

例如在 `plugin.py` 里给 `EchoCommand` 补一个路由：

```python
class EchoCommand(BaseCommand):
    """最小回显命令。"""

    command_name = "echo"
    command_description = "一个用于演示插件系统的最小回显命令"
    command_prefix = "/"

    async def _get_service(self) -> EchoService:
        """创建当前插件对应的 EchoService 实例。"""
        return EchoService(self.plugin)

    @cmd_route("prompt")
    async def handle_prompt(self, text: str) -> tuple[bool, str]:
        """预览当前 prompt 的构建结果。"""
        service = await self._get_service()
        prompt_text = await service.build_reply_prompt(text=text, mode="plain")
        return True, prompt_text
```

这样你就可以先通过类似下面的命令，观察模板最终被渲染成什么样子：

```text
/echo prompt 你好，帮我写一句自我介绍
```

这一小步的意义其实很大。

因为它把 Prompt API 从“看起来很抽象的系统能力”，变成了一个你能立刻看到结果的可验证环节。

## 9.7 如果不想显式注册，也可以先用 `get_or_create()`

前面我先用了 `register_template()`，是因为它最能体现“先注册，再取回”的系统视角。

但在很多更轻量的场景里，你也可以直接用 `get_or_create()`：

```python
template = prompt_api.get_or_create(
    name="echo_demo.reply",
    template=(
        "你正在处理 echo_demo 插件的文本任务。\n"
        "用户输入：{user_input}\n"
        "格式模式：{mode}\n"
        "请输出一条简洁、自然、不过度发挥的回复。"
    ),
)
```

它的直觉很好懂：

- 如果系统里已经有这个模板，就直接拿来用。
- 如果还没有，就顺手创建并注册。

这个接口很适合“我现在就想先把流程跑起来”的场景。

不过也要顺手记住它和 `register_template()` 的一个差异：

- `register_template()` 更像明确覆盖或明确注册。
- `get_or_create()` 更像懒加载初始化。

入门阶段，两种都可以用。只是如果你很在意模板资源的初始化时机，`on_plugin_loaded()` 里的显式注册会更容易读懂。

## 9.8 system reminder 是另一种 prompt 资产，但它不是模板

到这里，模板这条线已经清楚不少了。接下来再看另一半：`system reminder`。

它和模板最关键的区别在于：

- **模板**更像可渲染的 prompt 骨架。
- **reminder**更像可存取的补充性系统上下文。

你可以把 reminder 想成一段“系统暂存的提醒信息”。它不是为了像模板那样反复填值渲染，而更像：

- 有一条系统级提示我想先存起来。
- 某条链路真正需要时，再把它读出来。

这一点在仓库里的真实插件里已经有使用，例如 `emoji_sender` 就会在插件加载时同步一段 actor reminder。

## 9.9 先给 `echo_demo` 加一条最小 reminder

为了让概念更具体，我们也给 `echo_demo` 补一条很小的 reminder：

```python
async def on_plugin_loaded(self) -> None:
    """插件加载完成后注册 prompt 资产。"""
    prompt_api.register_template(
        PromptTemplate(
            name="echo_demo.reply",
            template=(
                "你正在处理 echo_demo 插件的文本任务。\n"
                "用户输入：{user_input}\n"
                "格式模式：{mode}\n"
                "请输出一条简洁、自然、不过度发挥的回复。"
            ),
        )
    )

    prompt_api.add_system_reminder(
        bucket="actor",
        name="echo_demo_style",
        content="回复风格保持简洁、温和，不要为了显得聪明而过度展开。",
    )
```

这样做之后，这条 reminder 就被存进系统里了。

后面如果你想把它取出来，也可以这样写：

```python
reminder_text = prompt_api.get_system_reminder("actor", ["echo_demo_style"])
```

如果当前 bucket 下有内容，它会返回类似这样的文本：

```text
[echo_demo_style]
回复风格保持简洁、温和，不要为了显得聪明而过度展开。
```

## 9.10 这里有一个特别容易误会的点：reminder 不会自动注入

这个地方我建议你务必要记住，因为它特别容易产生“我不是已经存进去了吗，为什么模型没有自动用上”的错觉。

当前 `prompt_api.add_system_reminder()` 做的事情，本质上只是：

> **把 reminder 存起来。**

它不会自动替你把这段内容塞进某一次 LLM 请求里。

也就是说，正确理解应该是：

- `add_system_reminder()` 负责存储。
- `get_system_reminder()` 负责取回。
- 至于什么时候把它拼进 prompt 或注入到模型上下文，要由后续调用链自己决定。

这个设计其实很合理。因为 reminder 本来就可能服务于很多不同链路，而不是一存进去就强行污染所有请求。

## 9.11 现在你可以怎么理解 Prompt API

如果把这一章的重点压缩一下，Prompt API 现在可以先这样理解：

- 它不是模型调用器。
- 它更像 prompt 资源的管理入口。
- 模板适合承载“可复用、可填值、可渲染”的 prompt 骨架。
- reminder 适合承载“先存起来、按需取用”的补充上下文。

而对插件作者来说，这一层最大的价值是：

> **你终于不用把 prompt 当成一堆散落在业务代码里的长字符串了。**

这一步看起来没有“模型真的回答了”那么刺激，但它其实是在给后面的系统协作打地基。

## 9.12 这一章先停在这里，刚好

我刻意没有在这一章里继续往下冲到 LLM 请求。不是因为它不重要，而是因为如果把 Prompt API 和 LLM API 一口气揉在一起，第一次读会很容易把边界看花。

这一章你只要先把两件事记牢，就已经够了：

1. PromptTemplate 负责把一段 prompt 组织成可复用模板。
2. system reminder 负责把补充性上下文存起来，后续按需取回。

下一步如果继续往前走，就很自然了：

> **既然 prompt 已经能被组织出来了，那它接下来该怎么进入真正的模型请求？**

那就是 LLM API 要接上的部分。