# 6. 给插件加配置

> **导读** 这一章解决插件开发中几乎无法回避的问题：如何让插件的行为变得可配置。从一个最小开关配置出发，本章将完整介绍 `BaseConfig`、`SectionBase`、`Field`、`config_section` 这套配置体系，并系统梳理其内置方法与属性。读完之后，你不只是会套用一个配置模板，而是真正理解这套设计背后的原因。

到了这里，插件终于要开始摆脱“写死行为”了。

前几章那个 `echo_demo` 虽然已经能跑，但它其实还有一个很明显的特点：**它的行为几乎全写死在代码里。**

这在入门阶段没有问题，甚至是故意这样安排的。因为一开始我们只想先打通“插件能被加载、组件能被注册、命令能被执行”这条主线。

但只要插件稍微往真实使用走一步，配置几乎就会立刻出现。你会开始想要：

- 临时关闭某个插件。
- 调整一些行为参数。
- 让用户不用改代码，只改配置就能改变插件表现。

这就是这一章要解决的问题。

> **我们要给 `echo_demo` 加上第一个真正的插件配置。**

这一章会先从一个最小开关配置开始，然后把配置系统本身是怎么组织的讲清楚。读完之后，你不只是会“抄一个配置类”，而是会开始明白：为什么 Neo-MoFox 的配置要长成这样。

## 6.1 什么时候应该给插件加配置

不是所有插件一开始都必须有配置，但只要你的插件开始出现下面这些需求，配置通常就该来了：

- 某些行为需要启用或关闭。
- 某些参数希望用户可调整。
- 你不希望用户每次改行为都去改 Python 代码。

哪怕只是一个最简单的 `enabled` 开关，也已经足以让插件从“代码样例”开始往“可使用功能”迈一步。

所以这一章我们不做复杂配置，先做一个最小但真实有意义的版本：

- 给 `echo_demo` 增加一个 `enabled` 开关。
- 当配置关闭时，插件不注册任何组件。

这个例子虽然很小，但它已经把配置系统最关键的一条链路全走通了：

```text
定义配置类
-> 在插件类里声明 configs
-> 启动时自动加载配置
-> 配置实例注入插件
-> 插件根据配置决定行为
```

## 6.2 看最终目标

这一章做完之后，`echo_demo` 会变成这样：

- 插件目录里多一个 `config.py`。
- 插件类通过 `configs` 声明配置类。
- 配置文件默认放在 `config/plugins/echo_demo/config.toml`。
- 如果这个文件还不存在，系统会在首次加载时自动生成默认配置。
- 如果配置里的 `enabled = false`，插件就不会注册命令组件。

## 6.3 新增 config.py

在 `echo_demo` 目录里新增一个 `config.py`：

```python
from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import (
    BaseConfig,
    Field,
    SectionBase,
    config_section,
)


class EchoDemoConfig(BaseConfig):
    """EchoDemo 插件配置。"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "EchoDemo 插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件基础配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用 echo_demo 插件",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
```

如果你第一次看这段代码，可能会觉得“明明只是一个开关，为什么要写成这样”。

这个疑问很正常，而且值得认真回答。因为如果只看结果，一个布尔值确实很简单；但配置系统设计得稍微完整一些，目的从来不只是装下今天这一个布尔值，而是为明天的更多配置项留出空间。

所以接下来我们把这几个核心角色拆开讲。

## 6.4 BaseConfig 是什么

`BaseConfig` 是插件配置的基类。你可以先把它理解成一句话：

> **只要某个类继承自 `BaseConfig`，系统就会把它当成插件配置来理解。**

它帮你处理了几件非常重要的事：

- 提供统一的配置模型基类。
- 约定默认配置路径。
- 支持默认配置文件生成。
- 支持按插件名加载配置。

也就是说，插件作者写配置类时，不需要从零开始解决“配置文件放哪”“找不到配置怎么办”“默认值怎么写回 TOML”这些问题。这些基础工作，`BaseConfig` 已经替你铺好了。

这也是为什么我们这章可以把重点放在“怎么描述配置”，而不是先去造一套自己的配置读写框架。

## 6.5 config_name 和 config_description 在干什么

这两个字段虽然简单，但很值得一开始就理解清楚。

```python
config_name: ClassVar[str] = "config"
config_description: ClassVar[str] = "EchoDemo 插件配置"
```

### `config_name`

它决定这个配置文件的基本文件名。

在当前实现里，如果插件名是 `echo_demo`，`config_name` 是 `config`，那么默认配置路径就会是：

```text
config/plugins/echo_demo/config.toml
```

所以 `config_name` 并不是给人看的文案，而是会直接影响配置文件路径。

### `config_description`

它更偏向配置本身的描述信息，主要是给系统和未来的配置展示层预留语义。

在入门阶段，你可以先把它理解成“这个配置类是干什么的”。

## 6.6 SectionBase 和 config_section 为什么要一起出现

现在来看最关键的一层：

```python
@config_section("plugin")
class PluginSection(SectionBase):
    enabled: bool = Field(default=True, description="是否启用 echo_demo 插件")
```

这里有两个点需要一起看。

### `SectionBase`

它表示“这是一段配置节”。

你可以把它想成 TOML 里的一块小节，比如：

```toml
[plugin]
enabled = true
```

也就是说，`SectionBase` 不是整个配置文件，它只是配置文件里的某一块结构。

### `@config_section("plugin")`

这个装饰器则是在告诉系统：

> 这一段配置节在配置文件里对应的名字叫 `plugin`。

所以这两者放在一起，表达的其实是：

> 我现在定义了一个名为 `plugin` 的配置节，它里面会放插件级的基础配置。

这也是为什么后面我们还会再写：

```python
plugin: PluginSection = Field(default_factory=PluginSection)
```

它是在把这个配置节真正挂回整个配置模型里。

## 6.7 Field 到底是干什么的

在配置系统里，`Field` 主要有两类作用。

### 第一类作用：给字段默认值

比如：

```python
enabled: bool = Field(default=True, description="是否启用 echo_demo 插件")
```

这里最直接的含义就是：如果用户没有显式设置这个值，那它默认就是 `True`。

### 第二类作用：给字段附加说明信息

比如这里的 `description`，它并不只是为了好看。它本质上是在给这个配置项补充语义说明。

在更完整的插件里，你还会看到更多元数据，比如：

- label
- hint
- order
- tag
- input_type

这些信息在入门阶段暂时不用一次掌握完，但你至少要先知道：配置字段不是只有“类型”和“默认值”，它还可以携带一些帮助系统理解或展示这个字段的信息。

## 6.8 为什么最后还要写 plugin: PluginSection

这句：

```python
plugin: PluginSection = Field(default_factory=PluginSection)
```

非常容易被初学者忽略，因为它看起来像重复了一遍。

但其实它很重要。前面的内部类只是定义了一个配置节的结构，而这句是在告诉整个配置模型：

> 我这个配置文件里，真的有一个 `plugin` 字段，它的类型就是刚才定义的 `PluginSection`。

如果少了这句，你只是定义了一个内部结构，却没有把它真正纳入最终配置模型。

所以你可以把整个配置类理解成两层：

- 上层：配置文件整体长什么样。
- 下层：每个配置节内部长什么样。

## 6.9 现在修改 plugin.py

有了配置类之后，插件本身还要明确声明“我使用这个配置”。

把 `plugin.py` 改成这样：

```python
from __future__ import annotations

from src.app.plugin_system.base import (
    BaseCommand,
    BasePlugin,
    cmd_route,
    register_plugin,
)

from .config import EchoDemoConfig


class EchoCommand(BaseCommand):
    """最小回显命令。"""

    command_name = "echo"
    command_description = "一个用于演示插件系统的最小回显命令"
    command_prefix = "/"

    @cmd_route("ping")
    async def handle_ping(self) -> tuple[bool, str]:
        """检查命令是否已经正常工作。"""
        return True, "pong"

    @cmd_route("say")
    async def handle_say(self, text: str) -> tuple[bool, str]:
        """回显一段文本。"""
        return True, f"echo: {text}"


@register_plugin
class EchoDemoPlugin(BasePlugin):
    """最小回显插件。"""

    plugin_name = "echo_demo"

    configs: list[type] = [EchoDemoConfig]

    def get_components(self) -> list[type]:
        """返回当前插件包含的组件。"""
        if isinstance(self.config, EchoDemoConfig) and not self.config.plugin.enabled:
            return []
        return [EchoCommand]
```

这一改动看起来很小，但意义非常大。

### 第一处变化：声明 configs

```python
configs: list[type] = [EchoDemoConfig]
```

这句是在告诉插件管理器：

> 这个插件有配置类，而且在实例化插件之前，应该先把它加载好。

这也是当前实现里最关键的约定之一：**配置不是靠 `get_components()` 返回的，而是靠插件类上的 `configs` 声明。**

这一点一定要记住，因为它和其他组件的接入方式不一样。

### 第二处变化：根据配置决定是否注册组件

```python
if isinstance(self.config, EchoDemoConfig) and not self.config.plugin.enabled:
    return []
```

这一段是在说：如果插件配置已经加载成功，而且 `enabled` 被关掉了，那这个插件就不注册任何组件。

你可以把它理解成一种很直接的插件级开关。

它的好处是很清楚：

- 配置打开，插件正常工作。
- 配置关闭，插件仍然存在于项目里，但不会对系统暴露功能。

对于很多社区插件来说，这都是一个非常实用的默认能力。

## 6.10 配置文件会放在哪里

在当前实现下，这个配置的默认位置是：

```text
config/plugins/echo_demo/config.toml
```

这个路径不是我们手写出来的，而是由插件名和 `config_name` 一起决定的。

这点很重要，因为它意味着不同插件的配置天然就会各自收在自己的目录里，不容易打架，也更方便用户查找。

## 6.11 第一次启动时会发生什么

当你现在再次启动项目：

```bash
uv run main.py
```

系统大致会做下面这些事：

1. 发现 `echo_demo` 插件。
2. 读取它的 manifest。
3. 导入 `plugin.py`。
4. 找到注册过的 `EchoDemoPlugin`。
5. 看到它声明了 `configs = [EchoDemoConfig]`。
6. 尝试加载 `config/plugins/echo_demo/config.toml`。
7. 如果文件不存在，就生成默认配置文件。
8. 把配置实例注入插件对象。
9. 插件在 `get_components()` 里根据配置决定是否返回组件。

这里有一个对新手很友好的点：**配置文件不存在时，系统会自动生成默认配置。**

不过这里不是所有问题都靠自动生成解决，只是在入门阶段，这个机制确实能让你少走很多弯路。至少你不用第一步就手写完整配置文件，才能验证插件机制本身是否通了。

## 6.12 一个最小配置文件大致会长什么样

首次生成之后，你最终看到的配置文件核心内容，大致会是这个样子：

```toml
[plugin]
enabled = true
```

实际生成结果可能会带上更多注释或渲染细节，但对入门理解来说，这个核心结构已经够用了。

如果你把它改成：

```toml
[plugin]
enabled = false
```

那么下一次加载插件时，`echo_demo` 就不会返回它的命令组件。

## 6.14 `BaseConfig` 基类速查

下面系统性地过一遍 `BaseConfig` 的定义，包括所有类属性和方法，供参考：

### 类属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `config_name` | `ClassVar[str]` | `"config"` | 配置文件基本文件名（不含 `.toml` 扩展名），影响默认路径生成 |
| `config_description` | `ClassVar[str]` | `""` | 配置的可读描述，用于展示层和文档 |
| `_plugin_` | `ClassVar[str]` | 由插件管理器注入 | 所属插件名称，用于生成路径和签名，开发者无需手动填写 |

### 主要方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_default_path()` | `cls → Path \| None` | 根据插件名和 `config_name` 计算默认配置文件路径，格式为 `config/plugins/{plugin_name}/{config_name}.toml` |
| `generate_default()` | `cls, path=None → None` | 按配置模型的默认值生成 TOML 文件；`path` 为 `None` 时使用 `get_default_path()` |
| `get_signature()` | `cls → str \| None` | 返回组件签名，格式为 `plugin_name:config:config_name` |

> `BaseConfig` 继承自 kernel 层的 `ConfigBase`（Pydantic 模型），因此也支持 `.model_validate()`、`.model_dump()` 等标准 Pydantic 方法。

### 关联辅助类型

**`SectionBase`**

所有配置节的基类。定义配置节结构的内部类须继承自 `SectionBase`。

**`@config_section(name: str)`**

将一个 `SectionBase` 子类标记为特定的配置节，参数 `name` 即 TOML 文件中对应的小节名。

**`Field(...)`**

为配置字段声明默认值和元数据：

```python
from src.app.plugin_system.base import Field

enabled: bool = Field(default=True, description="是否启用")
```

常用参数：`default`、`description`、`label`、`hint`、`order`、`tag`、`input_type`。


## 6.15 这一章最值得记住的几个点

到了这里，配置系统里最关键的几条关系应该已经比较清楚了：

- `BaseConfig` 表示这是一个插件配置类。
- `config_name` 决定默认配置文件名。
- `SectionBase` + `config_section` 用来定义配置节。
- `Field` 用来声明默认值和字段元数据。
- 插件通过 `configs` 声明要加载哪个配置类。
- 插件实例会通过 `self.config` 拿到配置对象。

如果你能把这几条关系记住，后面再看更复杂的插件配置，就不会觉得它们只是在“写很多很长的字段定义”了。你会知道它们其实是在用同一套模式，逐步把一个插件的可调行为描述出来。

## 6.16 为什么这一章故意先从 enabled 开始

你可能已经注意到了，这一章讲了不少配置系统本身的结构，但示例配置却只做了一个最简单的 `enabled` 开关。

这是故意的。

因为配置系统本身已经有一点抽象味道了。如果一开始再塞进一堆业务参数，读者很容易分不清：

- 哪部分是在学配置机制。
- 哪部分是在学某个插件自己的业务。

所以我们先用最简单的开关，把“配置如何定义、如何生成、如何注入、如何影响插件行为”这条链跑通。等后面再加更真实的参数时，你就会轻松很多。

## 6.17 这一章压缩成一句话

如果你想把这一章压缩成一句最值得带走的话，那就是：

> **配置类负责描述插件的可调行为，插件类通过 `configs` 声明它，系统在加载插件前把配置准备好，再把结果注入给插件使用。**

到这里为止，你的插件已经不再只是“能跑的代码”，而开始具备一点点“可使用、可调整”的味道了。

下一步，我们就要往前再走一步：从单组件，开始走向多组件。