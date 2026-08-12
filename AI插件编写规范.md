# Neo-MoFox 插件编写规范（AI Agent 版）

本文件是给 AI agent 的单文档规范。目标是让 agent 仅凭这一篇文档，就能理解 Neo-MoFox 插件系统的结构、真实加载机制、主要组件边界，以及插件代码必须满足的约束。

本文件优先级低于源码。若文档与实现冲突，以公开入口、基类、加载器、管理器的真实行为为准。

## 1. 系统全景

Neo-MoFox 的插件系统对插件作者公开三层入口：

- `src.app.plugin_system.base`: 所有基类、`register_plugin`、`cmd_route`、配置工具。
- `src.app.plugin_system.api`: 面向插件作者的运行时 API，按能力拆分为多个模块。
- `src.app.plugin_system.types`: 公共类型与枚举，例如 `ChatType`、`ComponentType`。

`api` 层本质上是对各类 manager 的延迟导入封装。对插件作者来说，优先使用 `api` 层，而不是直接依赖内部 manager。

插件系统的基本结构是：

1. 一个插件类继承 `BasePlugin`，并通过 `@register_plugin` 注册。
2. 插件类通过 `get_components()` 返回自己提供的组件类。
3. `PluginManager` 根据组件继承关系识别组件类型，并自动注入 `_plugin_` 与 `_signature_`。
4. 组件被注册到全局注册表后，其他系统模块才能发现和使用它们。

组件签名是全系统的统一定位键，格式固定为：

```text
plugin_name:component_type:component_name
```

示例：

```text
emoji_sender:action:send_emoji_meme
default_chatter:chatter:default_chatter
```

## 2. 插件最小契约

每个插件都必须满足以下条件。

### 2.1 插件类

- 必须继承 `BasePlugin`。
- 必须使用 `@register_plugin` 装饰器。
- 必须定义类属性 `plugin_name: str`。
- 版本号、描述、作者等元数据**只在 `manifest.json` 中声明**，插件类上不重复定义。
  运行时 `plugin_version` 由框架从 manifest 注入（见 `PluginManager._inject_manifest_metadata`）；
  插件类若再显式声明 `plugin_version` / `plugin_description` / `plugin_author`，
  会被视为历史遗留冗余并触发 `DeprecationWarning`（不影响加载运行）。
- 如果插件有配置类，必须在 `configs: list[type]` 中声明。
- `get_components()` 必须返回组件类列表，返回值是 `list[type]`，不是实例列表。

最小形式：

```python
from src.app.plugin_system.base import BasePlugin, register_plugin


@register_plugin
class MyPlugin(BasePlugin):
    plugin_name = "my_plugin"

    configs: list[type] = []
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        return []
```

### 2.2 配置声明位置

配置类必须放在插件类的 `configs` 属性里声明：

```python
configs: list[type] = [MyPluginConfig]
```

不要把 `BaseConfig` 子类放进 `get_components()` 返回值。加载器会优先处理 `configs`，这是实际约束，不是风格建议。

### 2.3 生命周期钩子

插件可选实现：

- `async def on_plugin_loaded(self) -> None`
- `async def on_plugin_unloaded(self) -> None`

这两个钩子用于初始化和清理运行时资源。涉及异步后台任务时，统一使用 `task_manager`，不要直接使用 `asyncio.create_task()`。

### 2.4 名称一致性不是软建议

从真实加载流程看，以下三个名字最好保持一致：

- 插件目录名，例如 `plugins/my_plugin/`
- `manifest.json` 中的 `name`
- 插件类上的 `plugin_name`

原因不是“为了整洁”，而是加载器和插件管理器分工不同：

- 模块导入时使用的是插件目录名或压缩包解包后的包名。
- 插件类查找时使用的是 `manifest.name`，并要求它能对应到 `@register_plugin` 注册出来的 `plugin_name`。

因此，**`manifest.name` 与 `plugin_name` 不一致会直接导致加载失败**。目录名理论上可以不同，但会增加相对导入、卸载清理、调试定位时的认知成本，不建议这样做。

### 2.5 插件导入边界是强约束

- 插件代码**禁止直接导入其他插件模块**，例如禁止在一个插件内写 `from plugins.other_plugin... import ...`。
- 插件之间的能力依赖必须通过公开组件签名、Service、API 层或协议边界建立，不允许通过源码级直接 import 耦合。
- 如果插件内部需要表达对外部配置或服务的最小形状，应在本插件内定义 `Protocol`、类型别名或本地抽象，不要直接引用其他插件中的实现类或配置类。
- 插件导入自己内部模块时，**必须使用相对导入**：
  - 同目录模块使用 `.xxx`
  - 子目录模块使用 `.sub.xxx`
  - 上级目录模块使用 `..xxx`
- 插件内部禁止使用 `plugins.my_plugin...` 这类“导入自己”的绝对路径写法；这会放大包名耦合，也会让目录重命名和打包加载更脆弱。

正确示例：

```python
from .config import MyPluginConfig
from .service import MyService
from .src.worker import Worker
from ..protocol import ConfigLike
```

错误示例：

```python
from plugins.other_plugin.service import OtherService
from plugins.my_plugin.config import MyPluginConfig
```

## 3. 组件速查矩阵

下面只列插件系统真实识别并自动注册的主要组件类型。

> **统一命名约定**：从 `BaseComponent` 起，所有组件使用统一属性 `name` / `description` 标识自身。
> 旧属性名（如 `tool_name` / `tool_description`、`action_name` / `action_description` 等）仍可通过 `BaseComponent._legacy_name_attr` / `_legacy_desc_attr` 桥接使用，但会触发 `DeprecationWarning`。
> **新生成的插件代码一律使用统一属性 `name` / `description`，不要继续使用旧属性名。**

| 组件类型 | 基类 | 必填名称属性 | 何时使用 | 构造函数 | 必须实现 | 返回约定 | 关键边界 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Plugin | `BasePlugin` | `plugin_name` | 作为组件容器 | `__init__(config=None)` | `get_components()` | `list[type]` | 插件本身不是普通组件列表项 |
| Config | `BaseConfig` | `name` | 插件配置 | 无需实例化入口约定 | 定义配置节 | 配置模型 | 必须放入 `BasePlugin.configs` |
| Tool | `BaseTool` | `name` | 给 LLM 查询信息或计算 | `__init__(plugin)` | `execute()` | `(bool, str | dict)` | 偏信息获取，不做明显副作用 |
| Action | `BaseAction` | `name` | 给 LLM 执行动作 | `__init__(chat_stream, plugin)` | `execute()` | `(bool, str)` | 偏副作用；会注入 `ChatStream` |
| Agent | `BaseAgent` | `name` | 需要私有 usables 的复杂任务代理 | `__init__(stream_id, plugin)` | `execute()` | `(bool, str | dict)` | `usables` 是私有组件集，不进全局注册表 |
| Chatter | `BaseChatter` | `name` | 作为对话主控制器 | `__init__(stream_id, plugin)` | `execute()` | `AsyncGenerator[Wait | Success | Failure | Stop, None]` | 是对话流程核心，不等于普通 Tool |
| Command | `BaseCommand` | `name` | 处理命令式输入 | `__init__(plugin, stream_id)` | 路由方法而非通常重写 `execute()` | `(bool, str)` | 使用 `@cmd_route` 构建 Trie 路由 |
| Service | `BaseService` | `name` | 给其他插件/组件直接调用能力 | `__init__(plugin)` | 自定义公开方法 | 自定 | 适合暴露稳定能力接口 |
| EventHandler | `BaseEventHandler` | `name` | 响应系统事件 | `__init__(plugin)` | `execute(event_name, params)` | `(EventDecision, dict[str, Any])` | 可订阅、拦截、调整事件参数 |
| Router | `BaseRouter` | `name` | 暴露 HTTP API | `__init__(plugin)` | `register_endpoints()` | 无 | 基于 FastAPI，不要用于聊天逻辑 |
| Adapter | `BaseAdapter` | `name` | 平台协议桥接 | `__init__(core_sink, plugin=None, **kwargs)` | `from_platform_message()`、`get_bot_info()` | `MessageEnvelope` / `dict` | 负责平台消息与统一消息模型转换 |

> 上表"必填名称属性"列均为统一属性 `name`；对应描述属性为统一属性 `description`（未在表中单列）。
> Adapter 额外保留 `adapter_version`、`platform` 等适配器专属元数据，不受统一命名影响。

## 4. 每类组件的精确规则

### 4.1 Config

- 继承 `BaseConfig`。
- 必须定义统一属性 `name`（配置节文件名，旧属性 `config_name` 已弃用但仍可用）和 `description`。
- 使用 `@config_section`、`SectionBase`、`Field` 定义节和字段。
- 默认配置路径为：

```text
config/plugins/{plugin_name}/{name}.toml
```

- 可通过 `load_for_plugin()` 加载，也可依赖插件管理器在加载插件时提前处理。

最小示例：

```python
from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class DemoConfig(BaseConfig):
    name = "config"
    description = "demo 插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        enabled: bool = Field(default=True, description="是否启用")

    plugin: PluginSection = Field(default_factory=PluginSection)
```

### 4.2 Tool

- 继承 `BaseTool`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `tool_name` / `tool_description` 已弃用但仍可用）。
- 必须实现 `async def execute(...) -> tuple[bool, str | dict]`。
- 参数必须有类型注解和含义清晰的文档字符串，管理器会据此生成 schema。
- Tool 用于查询和返回信息，不适合承担明确的外部副作用。

### 4.3 Action

- 继承 `BaseAction`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `action_name` / `action_description` 已弃用但仍可用）。
- 必须实现 `async def execute(...) -> tuple[bool, str]`。
- 与 Tool 最大区别：Action 是动作，重点是执行副作用，不是返回信息。
- `BaseAction` 构造函数会注入 `chat_stream`；如果动作需要上下文消息、历史消息、发送目标等，优先从 `self.chat_stream` 获取。

### 4.4 Agent

- 继承 `BaseAgent`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `agent_name` / `agent_description` 已弃用但仍可用）。
- 必须实现 `async def execute(...) -> tuple[bool, str | dict]`。
- `usables` 是 Agent 私有能力集，可以写组件类，也可以写组件签名字符串。
- Agent 的私有 usables 不进入全局注册表，只对该 Agent 可见。
- 当需求只是"查询信息"时优先用 Tool；只有当需要让一个子代理编排私有 tools/actions/agents 时才用 Agent。

### 4.5 Chatter

- 继承 `BaseChatter`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `chatter_name` / `chatter_description` 已弃用但仍可用）。
- 必须实现 `async def execute(self) -> AsyncGenerator[ChatterResult, None]`。
- `ChatterResult` 由 `Wait`、`Success`、`Failure`、`Stop` 组成。
- Chatter 是对话主流程控制器，会组合 Tool、Action、Agent，而不是替代它们。

### 4.6 Command

- 继承 `BaseCommand`。
- 必须定义统一属性 `name`（旧属性 `command_name` 已弃用但仍可用），可定义 `command_prefix`、`permission_level`。
- 通常不要重写 `execute()`；正确做法是写若干个被 `@cmd_route(...)` 装饰的方法。
- 路由方法返回值约定为 `(bool, str)`。
- `BaseCommand.execute()` 接收的是已经去掉前缀和 `name` 的子路由文本，不要按完整原始命令文本编写逻辑。

### 4.7 Service

- 继承 `BaseService`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `service_name` / `service_description` 已弃用但仍可用）。
- Service 不服务于 LLM schema，而服务于插件间或模块间直接调用。
- 如果能力应被其他插件稳定复用，优先设计为 Service，而不是伪装成 Tool。

### 4.8 EventHandler

- 继承 `BaseEventHandler`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `handler_name` / `handler_description` 已弃用但仍可用）。
- 若要初始化订阅，使用 `init_subscribe`。
- `execute(event_name, params)` 的返回值必须是：
  - `EventDecision.SUCCESS, params`
  - `EventDecision.STOP, params`
  - `EventDecision.PASS, params`
- 需要改变事件传播行为时用 EventHandler，不要把这类逻辑塞进 Chatter 或 Adapter。

### 4.9 Router

- 继承 `BaseRouter`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `router_name` / `router_description` 已弃用但仍可用）。
- 必须实现 `register_endpoints()` 并在其中向 `self.app` 注册 FastAPI 端点。
- Router 用于 HTTP 接口，不应用来承载命令、聊天主流程或平台适配逻辑。

### 4.10 Adapter

- 继承 `BaseAdapter`。
- 必须定义统一属性 `name` 和 `description`（旧属性 `adapter_name` / `adapter_description` 已弃用但仍可用），以及 `adapter_version`、`platform`。
- 必须实现：
  - `async def from_platform_message(self, raw: Any) -> MessageEnvelope`
  - `async def get_bot_info(self) -> dict[str, Any]`
- 可选实现：
  - `on_adapter_loaded()`
  - `on_adapter_unloaded()`
  - `health_check()`
  - `reconnect()`
- Adapter 只负责平台协议桥接和消息模型转换，不负责业务决策。

## 5. manifest 与加载规则

每个插件目录必须提供 `manifest.json`。加载器支持目录、`.zip`、`.mfp` 三种来源，但三者最终都依赖 manifest。

最小示例（字符串形式，最简）：

```json
{
  "name": "demo_plugin",
  "version": "1.0.0",
  "description": "Demo plugin",
  "author": "MoFox Team",
  "dependencies": {
    "plugins": [],
    "components": []
  },
  "include": [
    {
      "component_type": "tool",
      "component_name": "demo_tool",
      "dependencies": [],
      "enabled": true
    }
  ],
  "entry_point": "plugin.py",
  "api_version": "1.0.0",
  "python_dependencies": [],
  "dependencies_required": true
}
```

推荐形式（dict，精确声明所用 API 模块）：

```json
{
  "name": "demo_plugin",
  "version": "1.0.0",
  "description": "Demo plugin",
  "author": "MoFox Team",
  "dependencies": {
    "plugins": [],
    "components": []
  },
  "include": [
    {
      "component_type": "tool",
      "component_name": "demo_tool",
      "dependencies": [],
      "enabled": true
    }
  ],
  "entry_point": "plugin.py",
  "api_version": {
    "llm_api": "1.0.0",
    "send_api": "1.0.0"
  },
  "python_dependencies": [],
  "dependencies_required": true
}
```

硬性规则：

- `include` 字段必须手工维护。系统不会自动扫描你的组件文件。
- `component_type` 必须与真实组件类型匹配。
- `component_name` 必须与组件类上的统一名称属性 `name` 匹配（旧属性如 `tool_name` / `action_name` 已弃用但仍可桥接到 `name`）。
- `dependencies` 内的每一项都必须是完整组件签名。
- `entry_point` 默认语义是插件根目录下的 `plugin.py`。
- 如果声明了 `python_dependencies`，要确保运行环境可安装这些依赖。
- `api_version` 声明插件用到的插件 API 模块版本，支持字符串（等价于对全部 20 个 `*_api` 模块应用同一要求）与 `dict[str, str]`（仅校验声明模块，key 必须是合法 API 模块名）两种形式。插件 `import` 了任何 `*_api` 模块就应声明此项，优先用 dict 形式精确声明。与 `min_core_version` 同等判断（声明即校验，都声明则都须满足）。

### 5.1 真实加载行为补充

下面这些不是风格建议，而是当前源码中的实际行为。

#### 5.1.1 `manifest.name` 必须能找到同名已注册插件类

插件导入后，`PluginManager` 会按 `manifest.name` 去全局注册表取插件类。
这意味着：

- 你必须使用 `@register_plugin`。
- 插件类上的 `plugin_name` 必须与 `manifest.name` 一致。

否则即使模块本身成功导入，也会报“插件类未注册”。

#### 5.1.2 `entry_point` 不一定非得是根目录 `plugin.py`

文档中的推荐形式是根目录 `plugin.py`，但源码允许 `entry_point` 指向插件目录内部的相对路径，例如：

```json
"entry_point": "src/plugin.py"
```

前提是该文件确实位于插件目录内部，并且相对导入关系是自洽的。

#### 5.1.3 版本兼容性声明

插件通过 `manifest.json` 中的 `api_version` 与 `min_core_version` 声明对核心框架的版本要求。**两者同等判断（AND 语义）**：声明了哪一项就校验哪一项，只要任一项声明且不满足即拒绝注册；两者都未声明才回退到「警告但加载」。不存在二流声明。

##### `api_version`（推荐，声明插件 API 模块版本）

声明插件用到的 **插件 API 模块** 版本（按 20 个 `*_api` 模块逐一校验）。支持两种形式：

- **字符串形式**（最简）：`"api_version": "1.0.0"`，等价于对该版本声明时全部 20 个 API 模块都要求该版本。
- **dict 形式**（推荐，精确声明）：`"api_version": {"llm_api": "1.0.0", "send_api": "1.2.0"}`，仅校验声明的 keys，未声明的模块不校验。

格式为 `x.y.z`（语义化版本）：

- **主版本号 （x）**：破坏性更新，API 签名或行为发生不可兼容的变更
- **次版本号 （y）**：部分非兼容性更新，可能包含弃用、行为微调等
- **小版本号 （z）**：可向下兼容的更新，仅新增可选 API 或修复问题

兼容性检查规则（每个声明模块）：

- 主版本号不一致 → **拒绝加载**（存在破坏性变更）
- 核心 API 的次版本号低于插件要求 → **拒绝加载**（核心过旧）
- 核心 API 的次版本号与小版本号均不低于插件要求 → **允许加载**（兼容或仅有可向下兼容的差异）
- 核心 API 的次版本号高于插件要求 → **允许加载，但警告**（可能存在非兼容变更）

dict 形式示例：

```json
"api_version": {
  "llm_api": "1.0.0",
  "send_api": "1.2.0",
  "service_api": "1.0.0"
}
```

合法 API 模块名（20 个，与 `src/app/plugin_system/api/` 一一对应）：`action_api`、`adapter_api`、`agent_api`、`chat_api`、`command_api`、`config_api`、`database_api`、`event_api`、`llm_api`、`log_api`、`media_api`、`message_api`、`permission_api`、`plugin_api`、`prompt_api`、`router_api`、`send_api`、`service_api`、`storage_api`、`stream_api`。任何不在此清单中的 key 都会被拒绝加载（防止拼写错误被静默接受）。

##### `min_core_version`（声明核心能力版本）

声明插件依赖的 **核心能力** 版本（基于 `CORE_VERSION` 做简单 `>=` 比较）。适用于插件依赖核心某些新功能——例如某些新的事件、新的核心组件机制、新的内核接口等——这些能力不通过 `*_api` 模块暴露，无法被 `api_version` 覆盖，必须由 `min_core_version` 兜底。

```json
"min_core_version": "1.2.0"
```

##### `api_version` 与 `min_core_version` 的区别

| 字段 | 校验对象 | 适用场景 |
|---|---|---|
| `api_version` | 20 个 `*_api` 插件 API 模块（逐一语义化版本校验） | 插件 `import` 了 `*_api` 模块 |
| `min_core_version` | `CORE_VERSION`（简单 `>=` 比较） | 插件依赖核心新事件 / 新内核接口 / 新组件机制 |

两个字段是「或」关系，**按需填写**：插件 `import` 了任何 `*_api` 模块就写 `api_version`；插件用到「必须更高版本核心才支持」的能力（新事件、新内核接口、新组件机制）就写 `min_core_version`；两者都不需要也可都不写（仅警告但加载）。一旦声明，即按 AND 同等判断：声明了哪一项就校验哪一项，只要任一项声明且不满足即拒绝注册。

#### 5.1.4 `dependencies.plugins` 目前只用于插件级加载顺序

源码当前会基于 `manifest.dependencies.plugins`：

- 计算插件加载顺序
- 剔除缺失依赖插件
- 检查循环依赖

但这里有两个实际限制：

- 解析时当前只取 `:` 前面的插件名，**不会真正执行版本约束判断**。
- `dependencies.components` 和 `include[].dependencies` 不参与插件级拓扑排序，它们主要是组件注册元数据。

所以如果你写：

```json
"plugins": ["other_plugin:>=1.2.0"]
```

当前真实生效的仍然主要是 `other_plugin` 这个名字本身。

#### 5.1.5 `include` 目前更接近声明清单，不是唯一注册来源

这是最容易误判的一点。

当前真实行为是：

- `manifest.include` 会被 loader 解析出来。
- 但真正注册组件时，`PluginManager` 仍然以 `plugin.get_components()` 加上 `plugin_class.configs` 为主。

换句话说：

- `include` **不是**唯一真源。
- `include[].enabled` 当前也**不会自动阻止**该组件被注册。

因此对 AI 来说，当前最稳妥的做法是：

- 把 `manifest.include` 当成必须维护的声明文档；
- 同时确保 `get_components()` 与 `configs` 的真实返回与之保持一致；
- 如果想禁用组件，不要只改 `manifest.include[].enabled`，还要在插件代码里控制 `get_components()` 的返回。

## 6. 目录与导入约定

插件没有唯一目录模板，但 AI 生成代码时应遵循下面的稳定模式。

推荐目录：

```text
plugins/
  my_plugin/
    manifest.json
    plugin.py
    config.py
    tool.py
    action.py
    service.py
    __init__.py
```

随着插件复杂度上升，再按子域拆目录，例如 `agent/`、`service/`、`adapter/`。

导入规则：

- 优先从 `src.app.plugin_system.base`、`src.app.plugin_system.api`、`src.app.plugin_system.types` 导入。
- 只有在公开入口缺失所需对象时，才回退到内部模块路径。
- 对外可复用的插件代码，不应默认依赖私有内部模块路径。
- 仓库中的部分现有插件仍使用 `src.core.components` 等旧路径，这属于历史实现；新生成代码不要优先模仿这种内部导入方式。

### 6.1 还需要看源码才能确定的运行时细节

如果插件涉及下面这些能力，仅靠本文还不够，建议同步查看对应源码。

#### 6.1.1 多配置类插件

源码当前允许在 `configs` 中声明多个 `BaseConfig` 子类，但 `PluginManager` 在实例化插件时只会取**第一个**可加载配置，并把它注入到 `plugin.config`。

这意味着：

- `configs` 里可以放多个配置类；
- 但插件实例构造时的 `self.config` 并不是“配置集合”，而是单个配置实例；
- 如果插件确实需要多个配置对象，应该通过 `config_api` 或配置管理器按类显式加载，而不要假设 `self.config` 已经全都有。

#### 6.1.2 Service 不是单例

`service_api.get_service()` 最终走 `ServiceManager.get_service()`，它**每次都会创建一个新的 Service 实例**。

因此：

- 不要把 Service 设计成依赖实例级缓存且假设跨调用复用；
- 如果需要共享状态，放到持久化存储、外部资源或显式的全局管理对象里，而不是只放在 Service 实例字段上。

#### 6.1.3 EventHandler 的异常语义与订阅时机

`BaseEventHandler.__init__` 里的 `init_subscribe` 只是记录“我要订阅哪些事件”，真正绑定到 EventBus 是在插件加载完成后，由事件管理器统一注册。

同时，事件管理器会给处理器包一层异常保护：

- 处理器抛异常时，不会让整个事件总线崩掉；
- 当前包装逻辑会把异常转换为 `EventDecision.PASS`。

所以：

- 你不能依赖“抛异常来拦截后续处理器”；
- 真正要阻断传播，必须明确返回 `EventDecision.STOP`。

## 7. 消息模型与 Adapter 边界

Neo-MoFox 的统一线路消息是 `MessageEnvelope`。它不是平台原始消息，也不是最终业务消息对象，而是 Adapter 与核心之间交换消息的中间结构。

最重要的三层是：

1. `direction`: 消息方向。
2. `message_info`: 元信息层。
3. `message_segment`: 内容层。

AI 编写 Adapter 时必须遵守以下认知：

- 入站消息必须正确设置方向，否则接收链路可能直接忽略。
- `message_info` 负责平台、消息 ID、用户/群上下文等元信息，不等于消息内容。
- `message_segment` 负责内容段。
- 该模型应被理解为“核心骨架 + 常见运行时扩展”，不是封闭 schema。运行时可能会读写额外字段，例如 `message_info.message_type`、`message_info.extra`、`user_info.role`。
- Adapter 的职责是把平台原始事件转换为统一 envelope，再把统一消息发回平台，不负责对话决策、工具选择或业务编排。

### 7.1 入站信封预处理事件 `BEFORE_MESSAGE_RECEIVED`

当 `MessageReceiver` 收到一封 `direction == "incoming"` 且带 `message_info` 的 `MessageEnvelope` 后，**在路由到 `_handle_message` / `_handle_other` 之前**会先发布 `EventType.BEFORE_MESSAGE_RECEIVED`。订阅该事件的 EventHandler 可：

- 就地修改 `params["envelope"]` 的任意字段（例如改写 `message_segment`、调整 `message_info.message_type` 让它进入标准消息或 notice 路由、追加 `extra`）。
- 通过返回 `EventDecision.STOP` 直接拦截该消息：消息不再进入转换与下游 `ON_MESSAGE_RECEIVED` 事件。
- 通过返回 `EventDecision.SUCCESS` 并把回写的 envelope 放回 `params["envelope"]`，使后续 `_handle_message` / `_handle_other` 拿到改写后的 envelope。
- 通过返回 `EventDecision.PASS` 跳过本处理器，链路继续。

事件参数 schema：

```python
{
    "envelope": MessageEnvelope,        # 可就地修改，或回写新引用
    "adapter_signature": str,           # 发送方适配器签名
}
```

注意事项：

- 与 `neo_default_chatter:preprocess` 不同，本事件发生在**接收管线入口**，先于 `MessageConverter` 转换，先于 `ON_MESSAGE_RECEIVED` 分发，先于 chatter tick。
- 任何处理器抛异常都会被 EventBus 转成 `PASS`，不会阻断链路；要拦截必须显式返回 `STOP`。
- 若 `publish_event` 自身抛异常，receiver 会按放行处理（继续后续路由），并记录 error 日志。

最小示例：

```python
from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.core.components.types import EventType
from src.kernel.event import EventDecision


class RewriteTextHandler(BaseEventHandler):
    """把入站信封里的文本内容统一改写为小写。"""

    name = "rewrite_text"
    description = "入站信封预处理：把 message_segment 文本改写为小写"
    init_subscribe: list[EventType | str] = [EventType.BEFORE_MESSAGE_RECEIVED]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        envelope = params["envelope"]
        seg = envelope.get("message_segment")
        if isinstance(seg, list):
            for item in seg:
                if isinstance(item, dict) and item.get("type") == "text":
                    data = item.get("data") or {}
                    if isinstance(data.get("text"), str):
                        data["text"] = data["text"].lower()
                        item["data"] = data
        return EventDecision.SUCCESS, params
```

## 8. 跨组件硬规则

以下规则必须直接遵守。

### 8.1 组件识别依赖继承关系和名称属性

管理器通过组件继承关系识别类型，并读取统一名称属性 `name`：

- Action -> `name`
- Agent -> `name`
- Tool -> `name`
- Adapter -> `name`
- Chatter -> `name`
- Command -> `name`
- Config -> `name`
- EventHandler -> `name`
- Service -> `name`
- Router -> `name`

> 旧属性名（`action_name` / `tool_name` / `chatter_name` / `command_name` / `config_name` / `handler_name` / `service_name` / `router_name` / `adapter_name` / `agent_name`）由 `BaseComponent` 的 `_legacy_name_attr` 桥接到统一属性 `name`，仍可使用但会触发 `DeprecationWarning`。
> **新生成的插件代码必须直接使用 `name`**，不要继续依赖旧属性名。

统一属性 `name` 缺失、为空，或与 manifest 中的 `component_name` 不一致，都会造成识别或加载问题。

### 8.2 dependencies 必须写完整签名

组件级依赖的写法是：

```python
dependencies = ["other_plugin:service:storage"]
```

不要写简称，不要只写插件名，也不要写 Python 导入路径。

### 8.3 Action 与 Tool 不可混淆

- Tool: 重点是返回信息，返回值允许 `dict`。
- Action: 重点是执行动作，返回值只应是 `(bool, str)`。

如果一个组件的核心价值是“发送、写入、推送、调用外部副作用”，优先设计为 Action。

### 8.4 Agent 私有 usables 与全局注册表是两套概念

- Agent 本身会作为组件注册。
- Agent 的 `usables` 不会因为写进类属性就自动进入全局注册表。
- `usables` 只是该 Agent 的私有调用范围。

### 8.5 ChatStream 注入不是通用能力

- `BaseAction.__init__` 会收到 `chat_stream`。
- `BaseTool`、`BaseService`、`BaseCommand`、`BaseEventHandler` 默认没有 `chat_stream` 注入。
- 不要在 Tool/Service 中假设存在 `self.chat_stream`。

### 8.6 异步任务统一走 task_manager

插件代码涉及后台任务时，统一使用 `src.kernel.concurrency` 提供的 `task_manager`。不要直接调用 `asyncio.create_task()`。

### 8.7 仓库级编码规范同样适用于插件

- Python 版本要求 `>=3.11`。
- 参数与返回值必须有类型注解。
- 文件、类、函数都应有文档字符串。
- 新增 `src/` 代码时需要配套测试；新增插件代码也应补对应测试。
- 不要依赖含糊 fallback 掩盖真实错误，优先修根因。

### 8.8 `dependent_components` 当前不是核心加载真源

`BasePlugin` 上有 `dependent_components` 类属性，但从当前主加载流程看，插件加载顺序与可加载性判断主要来自 `manifest.dependencies.plugins`，组件注册来自 `get_components()` 与 `configs`。

因此：

- `dependent_components` 可以保留为插件内部声明；
- 但不要只写这里而不写 manifest；
- 当前真正影响插件级加载计划的仍然是 `manifest.json`。

### 8.9 system reminder 默认只存储，不会自动全局注入

`prompt_api.add_system_reminder()` 只负责把 reminder 写进 store。它不会自动进入所有 LLM 请求。

实际是否注入，取决于调用方是否在创建请求时显式使用了 `with_reminder`，例如：

- `llm_api.create_llm_request(..., with_reminder="actor")`
- `BaseChatter.create_request(..., with_reminder="actor")`
- `BaseAgent.create_llm_request(..., with_reminder="sub_actor")`

所以如果一个插件的设计目标是“通过 reminder 影响模型行为”，除了写 reminder 之外，还必须确认目标调用链真的会注入对应 bucket。

### 8.10 PromptTemplate 在构建前会触发 `on_prompt_build`

这点对提示词注入类插件很重要。

`PromptTemplate.build()` 在真正渲染前会发布 `on_prompt_build` 事件，并把以下信息放进参数里：

- `name`
- `template`
- `values`
- `policies`
- `strict`

事件处理器可以修改这些字段再继续渲染。因此：

- 如果你要做 prompt 注入、notice 注入、上下文增强，优先考虑订阅 `on_prompt_build`；
- 如果你只改了 system reminder，但目标 prompt 根本不用对应 bucket，那效果不会自动出现；
- `PromptTemplate.build()` 对事件异常会静默降级，不会因为某个注入器出错就阻断整个 prompt 构建。

## 9. 最小完整插件模板

下面给出一个足够小但完整的 Tool 型插件模板。AI 生成新插件时，优先从这个骨架改写。

### 9.1 manifest.json

最简形式（字符串 `api_version`，等价于对全部 20 个 API 模块要求 `1.0.0`）：

```json
{
  "name": "demo_plugin",
  "version": "1.0.0",
  "description": "最小 demo 插件",
  "author": "MoFox Team",
  "dependencies": {
    "plugins": [],
    "components": []
  },
  "include": [
    {
      "component_type": "tool",
      "component_name": "demo_tool",
      "dependencies": [],
      "enabled": true
    }
  ],
  "entry_point": "plugin.py",
  "api_version": "1.0.0",
  "python_dependencies": [],
  "dependencies_required": true
}
```

推荐形式（dict `api_version`，精确声明用到的 API 模块；若还依赖核心新能力可同时填 `min_core_version`，两者 AND 同等判断）：

```json
{
  "name": "demo_plugin",
  "version": "1.0.0",
  "description": "最小 demo 插件",
  "author": "MoFox Team",
  "dependencies": {
    "plugins": [],
    "components": []
  },
  "include": [
    {
      "component_type": "tool",
      "component_name": "demo_tool",
      "dependencies": [],
      "enabled": true
    }
  ],
  "entry_point": "plugin.py",
  "api_version": {
    "llm_api": "1.0.0",
    "send_api": "1.0.0"
  },
  "min_core_version": "1.2.0",
  "python_dependencies": [],
  "dependencies_required": true
}
```

### 9.2 config.py

```python
from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class DemoConfig(BaseConfig):
    """demo 插件配置。"""

    name = "config"
    description = "demo 插件配置"

    @config_section("plugin")
    class PluginSection(SectionBase):
        """插件主配置。"""

        enabled: bool = Field(default=True, description="是否启用")

    plugin: PluginSection = Field(default_factory=PluginSection)
```

### 9.3 tool.py

```python
from typing import Annotated

from src.app.plugin_system.base import BaseTool


class DemoTool(BaseTool):
    """返回简单文本的示例工具。"""

    name = "demo_tool"
    description = "返回带前缀的文本。"

    async def execute(
        self,
        text: Annotated[str, "需要处理的输入文本"],
    ) -> tuple[bool, str]:
        """执行示例工具。"""

        return True, f"demo:{text}"
```

### 9.4 plugin.py

```python
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import DemoConfig
from .tool import DemoTool


@register_plugin
class DemoPlugin(BasePlugin):
    """最小 demo 插件。"""

    plugin_name = "demo_plugin"

    configs: list[type] = [DemoConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """返回插件组件类。"""

        return [DemoTool]
```

## 10. 组件选择规则

AI 在生成插件前，先做下面的判定。

- 如果功能是给 LLM 查询信息、做计算、查状态，用 Tool。
- 如果功能是执行动作、发消息、写外部状态、触发副作用，用 Action。
- 如果功能是插件间复用能力，用 Service。
- 如果功能是命令式触发，用 Command。
- 如果功能是聊天主流程控制，用 Chatter。
- 如果功能是平台接入，用 Adapter。
- 如果功能是 HTTP 接口，用 Router。
- 如果功能是事件订阅与拦截，用 EventHandler。
- 如果功能是需要私有工具集的复杂子代理，用 Agent。

## 11. 生成前自检清单

在输出插件代码前，逐条检查：

1. 是否使用了公开入口 `src.app.plugin_system.base/api/types`。
2. 插件类是否有 `@register_plugin`，以及 `plugin_name`。版本/描述/作者是否只在 `manifest.json` 声明，插件类上没有冗余的 `plugin_version` / `plugin_description` / `plugin_author`。
3. `get_components()` 是否返回类而不是实例。
4. 配置类是否放在 `configs` 中，而不是放进 `get_components()`。
5. 每个组件是否定义了统一属性 `name`（不要继续使用 `tool_name` / `action_name` 等旧属性名），并与 manifest 中的 `component_name` 一致。
6. Tool、Action、Agent、Chatter 的方法签名和返回值是否匹配基类要求。
7. `dependencies` 是否写成完整组件签名。
8. 是否误把副作用组件写成 Tool，或误把查询组件写成 Action。
9. 是否错误假设 Tool/Service 拥有 `chat_stream`。
10. 若有后台任务，是否使用了 `task_manager`。
11. 是否补了文档字符串、类型注解，以及后续应补的测试。
12. `manifest.name` 是否与插件类 `plugin_name` 一致。
13. 是否误以为 `manifest.include[].enabled` 会自动禁用组件。
14. 如果使用 Service，是否避免依赖单例实例语义。
15. 如果依赖 system reminder，是否确认目标请求链实际传入了 `with_reminder`。

如果以上任一项不满足，不应认为插件代码已经符合 Neo-MoFox 规范。

## 12. 建议同步查看的源码入口

如果 AI 要生成的插件碰到下面这些场景，建议在动手前先读对应源码，而不要只依赖本文。

- `src/core/components/loader.py`
  需要确认 manifest 真实字段要求、插件级依赖裁剪、压缩包支持和默认值行为时看这里。
- `src/core/managers/plugin_manager.py`
  需要确认组件真实注册来源、`configs` 注入行为、入口点导入方式时看这里。
- `src/core/managers/config_manager.py`
  需要确认配置缓存、重载、多配置类插件的实际读取方式时看这里。
- `src/core/managers/service_manager.py`
  需要确认 Service 的实例化语义时看这里。
- `src/core/prompt/template.py`
  需要做 prompt 注入、动态模板增强、事件型 prompt 修改时看这里。
- `src/app/plugin_system/api/llm_api.py` 与 `src/core/components/base/chatter.py`
  需要确认 system reminder 怎样进入 LLM 上下文时看这里。
- `src/core/managers/event_manager.py`
  需要确认 EventHandler 的订阅、包装、异常传播、优先级行为时看这里。

如果生成逻辑依赖这些细节，而本文又没有明确写出，请以源码为准，并把“源码事实”优先纳入设计，而不是按一般插件框架经验脑补。