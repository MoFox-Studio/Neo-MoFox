# MoFox 插件真正热加载调研与设计要求

## 文档目的

本文记录 AstrBot、deepseek-harness（简称 DSH）和 MoFox 当前插件加载机制的对比结果，并给出 MoFox 实现真正热加载所需的运行时设计。

本文只描述机制、边界、验收标准和实施顺序，不包含本分支的运行时代码修改。

当前执行目标限定为配置热加载和插件编排变化。插件源码修改不属于热加载范围。

目标是让 MoFox 在不重启进程的情况下完成以下操作。

1. 插件配置文件修改后，新的配置进入新的插件实例。
2. 新增、删除插件或修改 `manifest.json` 后，插件组合和依赖计划可以更新。
3. 旧插件实例、事件订阅、路由、适配器和工具注册不会残留。
4. 热加载失败时，进程和其他插件继续运行，并且修复配置或插件目录后可以自动重试。

插件 `.py`、helper、模板和其他源码文件变化只被监听器忽略，不会触发插件重载。

当前实现已经接入 `PluginHotReloadService`。配置变化重载单个插件，删除插件只卸载被删除的插件，新增插件会重新加载当前插件组合并发布 `ON_ALL_PLUGIN_LOADED`。动态依赖只读取 `manifest.json` 的 `python_dependencies`，不读取 `requirements.txt`。

## 当前 Git 与目录基线

当前探索分支为 `feat/plugin-hot-reload-exploration`，基于 `dev` 的提交 `0bc8a4e`。

`engram-memory-dev` 已与最新 `dev` 同步，并推送到 GitHub。此前按要求删除了相对 `dev` 多出的 `plugins/Mnemosyne_memory/test/` 测试文件。

本地目录的跟踪情况如下。

| 目录 | 跟踪文件数量 | 与远程 `dev` 的差异 |
| --- | ---: | --- |
| `docs/` | 145 | 当前探索分支基于 `dev`，没有额外差异 |
| `test/` | 210 | 当前探索分支基于 `dev`，没有额外差异 |
| `tests/` | 0 | Git 不跟踪空目录 |

当前工作区仍有用户自己的未提交文件。本文不处理这些文件，也不将它们视为热加载设计的一部分。

## 证据索引

### AstrBot

- CLI 的 `--reload` 选项通过环境变量 `ASTRBOT_RELOAD=1` 启用热加载。
- `PluginManager` 在构造时创建插件文件监听任务。
- 监听使用 `watchfiles.awatch`、递归目录和 `PythonFilter`。
- 变化事件按照插件目录归属合并，同一批变化不会重复重载同一个插件。
- 重载过程包含插件终止、注册信息解绑、处理器清理、工具清理、平台适配器清理和 `sys.modules` 清理。
- Dashboard 保存插件配置后主动调用插件重载。

参考文件。

- `AstrBot/astrbot/cli/commands/cmd_run.py`
- `AstrBot/astrbot/core/star/star_manager.py`
- `AstrBot/astrbot/dashboard/services/config_service.py`

### DSH

- `dsh-hmr` 将文件监听、配置更新、Loader 更新和插件重载放进同一个 `runExclusive()` 队列。
- `watchConfig()` 只观察目标文件和必要的父目录，默认等待写入稳定。
- Loader 支持 `volatile` 配置字段就地提交，普通配置字段触发 fiber 重建。
- HMR 会读取 ESM 模块依赖、CommonJS `children`、Node ESM 缓存和 CommonJS 缓存。
- 受影响的运行时会停止，模块缓存被清理，新的 Loader entry 按依赖关系重新启动。
- profile 重组负责判断新旧插件组合、依赖、失败条目和需要重启的宿主模块。
- HMR 明确记录无法替换的宿主依赖、Worker、动态导入和模块缓存检查。

参考文件。

- `deepseek-harness/packages/boot/hmr/README.md`
- `deepseek-harness/packages/boot/hmr/src/index.ts`
- `deepseek-harness/packages/boot/hmr/src/watch-config.ts`
- `deepseek-harness/vendor/loader/src/config/entry.ts`
- `deepseek-harness/packages/boot/app-boot/src/index.ts`

## AstrBot 机制分析

### 启动入口

AstrBot 通过 CLI 的 `--reload` 选项设置环境变量。未设置该变量时，插件管理器不会创建文件监听任务。

这种方式适合开发环境，但热加载是全局开关，缺少细粒度配置，例如只监听某个插件或只监听配置文件。

### 文件变化映射

AstrBot 监听普通插件目录和内置插件目录。收到变化后，遍历已注册插件，判断变化路径是否位于插件根目录内，然后调用单插件重载。

它的优点是实现简单，插件目录和插件实例之间的映射清晰。

它的边界是只从插件根目录判断影响范围，无法判断共享 helper、外部链接包或被多个插件共同导入的模块。

### 重载生命周期

AstrBot 的重载顺序可以抽象为以下步骤。

1. 获取插件管理器互斥锁。
2. 查找插件模块路径。
3. 调用插件终止钩子。
4. 解除事件处理器、LLM 工具、平台适配器和注册表绑定。
5. 从 `sys.modules` 删除插件包及其子模块。
6. 清空旧注册状态。
7. 重新导入插件模块。
8. 重新读取插件元数据和配置 schema。
9. 创建新的插件实例。
10. 注册组件和事件处理器。
11. 调用初始化钩子。

### 配置变化

AstrBot 的 Dashboard 配置保存逻辑负责写文件，随后直接调用插件重载。配置变化因此走完整插件生命周期，而不是只更新一个已有配置对象。

这能保证构造函数读取到新配置，但会让所有配置修改都触发完整重载。它没有将安全的运行时字段和必须重建的字段区分开。

## DSH 机制分析

### 统一事务队列

DSH 的核心不是监听器本身，而是监听器与 Loader、配置编辑器和 profile 重组共享一条事务队列。

队列需要满足以下性质。

- 文件事件不会与手动配置写入并发执行。
- 一个事务失败不会破坏后续事务。
- 事务期间收到的事件会在当前事务完成后重新处理。
- 不允许事务嵌套，避免同一个运行时状态被重复重组。
- 关闭 HMR 时会等待或取消正在等待的重载。

### 配置差异模型

DSH 将配置变化分成两类。

- `volatile` 字段只更新正在运行的配置引用，并发出更新事件。
- 普通字段改变会触发插件 fiber 重建。

这一区分适合 MoFox 后续引入。第一阶段可以让所有插件配置走完整插件重载，第二阶段再允许插件声明不需要重建的字段。

### 模块依赖和缓存

DSH 不仅删除被编辑的入口模块，还会追踪静态依赖和反向导入者。

它需要处理以下缓存。

- Node ESM `ModuleJob`。
- CommonJS `require.cache`。
- CommonJS 父模块的 `children`。
- `package.json` 解析缓存。
- ESM resolve cache。

MoFox 当前使用 `importlib.util.spec_from_file_location()` 导入文件，并通过插件包名前缀清理 `sys.modules`。这足以覆盖插件包内的普通相对导入，但无法覆盖插件用绝对路径导入、动态导入、共享目录导入或外部链接包的情况。

## MoFox 当前插件加载机制

### 启动发现和依赖计划

`PluginLoader` 负责扫描 `plugins/`、读取 `manifest.json`、检查 API 和核心版本、剔除不可用依赖，并计算拓扑加载顺序。

`plan_plugins()` 只在启动流程中调用。当前没有运行时重新扫描和重新计算依赖图的服务。

### 单插件导入

`PluginManager.load_plugin_from_manifest()` 的主要步骤如下。

1. 判断插件是否已经加载。
2. 通过文件夹或压缩包导入入口模块。
3. 从全局插件注册表获取 `@register_plugin` 注册的插件类。
4. 根据插件类的 `configs` 属性加载配置。
5. 创建插件实例。
6. 注入 manifest 元数据。
7. 注册插件组件。
8. 写入 `_loaded_plugins`、`_manifests` 和 `_plugin_paths`。
9. 调用 `on_plugin_loaded()`。
10. 注册事件处理器。

### 单插件卸载

`unload_plugin()` 当前会执行以下操作。

- 调用 `on_plugin_unloaded()`。
- 发布 `ON_PLUGIN_UNLOADED`。
- 标记插件和组件状态。
- 注销事件处理器。
- 注销组件注册表。
- 注销全局插件类。
- 清理插件包名前缀的 `sys.modules` 条目。
- 删除插件实例、manifest 和路径引用。

当前卸载流程没有统一处理所有插件拥有的外部运行资源。

### 手动重载

`reload_plugin()` 只接受已经加载的插件，执行卸载后再次调用 `load_plugin()`。

它没有文件监听，没有配置文件事件，没有重载队列，也没有失败后的自动重试机制。

### 配置缓存

MoFox 的配置路径由 `BaseConfig.load_for_plugin()` 约定为：

```text
config/plugins/<plugin_name>/<config_name>.toml
```

`ConfigManager.load_config()` 按插件名缓存配置对象。插件重载时如果不删除缓存，新的插件类仍可能拿到旧的配置实例和旧的配置类引用。

### 持久运行资源

以下管理器拥有插件相关的运行时实例或缓存。

| 资源 | 所属管理器 | 重载要求 |
| --- | --- | --- |
| EventHandler 订阅 | `EventManager` | 注销全部旧订阅 |
| Router Mount | `RouterManager` | 调用 Router shutdown，移除旧 Mount |
| Adapter 连接 | `AdapterManager` | 停止连接、健康检查任务和 sink |
| Action schema | `ActionManager` | 清除旧签名缓存 |
| Tool schema | `ToolManager` | 清除旧签名缓存 |
| Chatter 实例 | `ChatterManager`、stream loop | 处理活动流中的旧实例 |
| Scheduler 任务 | `UnifiedScheduler` | 按插件所有权取消或重建 |
| 插件自有任务 | `TaskManager` 或插件内部 | 需要插件生命周期协作 |

## 目标架构

### 组件划分

建议新增独立的 `PluginHotReloadService`。它不应把文件系统监听、事务队列和插件实例管理全部塞进 `PluginManager`。

建议包含以下对象。

| 对象 | 职责 |
| --- | --- |
| `PluginHotReloadService` | 监听文件、生成变化批次、管理生命周期 |
| `PluginReloadQueue` | 串行执行配置写入、重载和重试 |
| `PluginChangeClassifier` | 判断配置、源码、manifest、依赖和无关文件 |
| `PluginModuleTracker` | 记录插件模块、依赖和模块缓存 |
| `PluginRuntimeSnapshot` | 保存旧实例、组件、资源和失败恢复信息 |
| `PluginReloadReport` | 记录成功、失败、跳过、待重启和重试 |

### 监听范围

默认监听以下目录。

```text
plugins/
config/plugins/
```

监听器必须排除以下路径。

- `__pycache__/`
- `.pytest_cache/`
- `.git/`
- 临时上传目录
- 插件运行时数据目录
- 日志目录

建议支持配置项。

```toml
[runtime.plugin_hot_reload]
enabled = true
debounce_ms = 200
write_stability_ms = 500
watch_plugin_code = false
watch_plugin_config = true
watch_manifest = true
retry_failed_reload = true
```

### 变化分类

| 变化 | 默认动作 |
| --- | --- |
| 插件 `.py` 文件 | 忽略，不触发热加载 |
| 插件内资源文件 | 忽略，不触发热加载 |
| `_conf_schema.json` 或配置 schema | 视为配置结构变化，重载对应插件 |
| `manifest.json` | 重新读取 manifest，重新校验并重载 |
| `config/plugins/<name>/` | 重载对应插件 |
| 新增插件目录 | 重新规划依赖并重新加载当前插件组合 |
| 删除插件目录 | 卸载插件并移除运行状态 |
| `requirements.txt` | 忽略，不作为插件依赖来源 |
| 核心 `src/` 文件 | 不进行插件级替换，报告需要重启 |

### 统一事务模型

所有重载入口都必须调用同一个事务入口。

```text
enqueue(change_batch)
  -> merge pending changes
  -> classify affected plugins
  -> resolve dependency order
  -> capture runtime snapshot
  -> stop affected resources
  -> invalidate config and module caches
  -> import current files
  -> validate manifest and config
  -> register components
  -> start resources
  -> publish reload result
```

事务约束如下。

- 同一插件不会同时运行两个重载事务。
- 同一批文件变化只重载一次插件。
- 文件写入期间的新事件会合并到下一批。
- 事务失败后必须恢复一致的注册表状态。
- 事务必须可取消，关闭 Bot 时不能创建新的重载。

## 配置热加载设计

### 第一阶段的完整重载

第一阶段不引入 `volatile` 字段。任何插件配置变化都走完整插件生命周期，行为清晰且容易验证。

处理顺序如下。

1. 读取配置文件到候选对象。
2. 使用配置类执行校验。
3. 校验失败时拒绝提交，保留旧实例。
4. 读取当前插件 manifest。
5. 停止插件拥有的 Adapter、Router、Scheduler 任务和事件处理器。
6. 调用插件卸载钩子。
7. 删除 `ConfigManager` 缓存。
8. 删除插件模块缓存。
9. 重新创建配置对象。
10. 创建新插件实例。
11. 注册新组件。
12. 启动新资源。
13. 发布配置重载结果。

### 第二阶段的可变字段

插件可以声明无需重建的字段。

```python
class Config(BaseConfig):
    volatile_fields = {"log_level", "poll_interval"}
```

只有在插件提供显式更新钩子时才允许就地更新。

```python
async def on_config_changed(self, old_config, new_config, changed_paths):
    ...
```

如果插件没有这个钩子，字段仍然走完整重载。

## 代码修改处理边界

当前实现不替换插件源码模块，也不清理插件代码模块缓存。代码修改需要通过现有的进程重启或人工插件加载流程生效。

保留模块依赖和缓存分析章节是为了说明 DSH 的能力边界。它们不是本次 MoFox 热加载实现的验收范围。

### 未来模块范围

插件模块至少包括以下模块。

- 入口模块。
- 插件包内的所有子模块。
- 由插件包相对导入的 helper 模块。
- 被插件模块缓存引用的模板和资源加载器。

模块追踪器需要记录以下信息。

```text
plugin_name
module_name
source_path
mtime_ns
size
package_prefix
imported_children
importing_parents
```

### 未来 Python 模块缓存

最小实现需要删除以下条目。

```text
<plugin_package>
<plugin_package>.*
```

增强实现需要额外处理插件入口模块的静态依赖图。动态 `importlib.import_module()`、插件通过绝对名称导入的共享模块和外部链接模块必须被记录为不确定范围。

遇到不确定范围时有两种策略。

1. 将相关插件标记为需要完整插件重载。
2. 将宿主进程标记为需要重启。

不能继续使用旧模块并把操作报告为成功。

### 未来旧实例引用

Python 不会把旧类实例自动转换成新类实例。重载必须停止旧实例并创建新实例。

已经开始执行的请求可以继续使用旧实例完成。新的请求必须通过插件管理器解析到新实例。

## 资源释放要求

### Router

重载前必须调用每个 Router 的 `shutdown()`，从 FastAPI 或 Starlette 路由表删除 Mount，清理 Router 管理器缓存。

注册表移除必须发生在 Router 卸载之后，否则管理器可能无法找到旧 Router 定义。

### Adapter

重载前必须执行以下操作。

1. 停止适配器连接。
2. 取消健康检查任务。
3. 解绑 sink。
4. 清理适配器管理器中的实例。
5. 重新加载插件后按新组件定义创建适配器。

### EventHandler

必须使用原有的 unsubscribe 函数清理底层 EventBus 订阅，不能只删除管理器里的字典项。

### Tool 和 Action

插件重载后，旧类的 schema 缓存不能继续使用。最小实现可以按插件清空全局 schema 缓存。后续可以按签名精确清理。

### Chatter

活跃 stream 可能持有旧 Chatter 实例。重载策略必须明确以下行为。

- 让当前 tick 完成后切换。
- 取消当前 Chatter 任务并恢复默认绑定。
- 立即创建新 Chatter 实例。

建议第一阶段采用“当前 tick 完成后切换”，避免在生成器中途替换对象。

### 插件自有任务

插件必须通过框架提供的 TaskManager 或插件生命周期注册任务。框架无法可靠发现插件直接创建且未保存引用的后台任务。

## 依赖关系与影响范围

### 插件依赖

如果插件 A 依赖插件 B 的组件，B 的组件签名、接口或配置发生变化时，至少需要重启 A 的受影响组件。

建议把 manifest 依赖和组件依赖都转换成有向图。

```text
B -> A
```

重载 B 时，按照反向依赖顺序停止 A、B，再按照依赖顺序启动 B、A。

### 新增和删除插件

新增插件不能只调用单插件加载，因为它可能改变依赖计划。当前流程会重新扫描目录、读取 manifest、执行版本校验、计算依赖顺序，并重新加载当前插件组合。

删除插件时需要先卸载其资源，再从失败列表、manifest 表、路径表和注册表中删除记录。

## 失败与回滚策略

### 失败分类

| 阶段 | 典型失败 | 处理 |
| --- | --- | --- |
| 文件读取 | 文件不存在、写入未完成 | 等待下一次事件 |
| 配置解析 | TOML 或 schema 校验失败 | 保留旧实例 |
| manifest 校验 | 版本或字段不兼容 | 保留旧实例并报告 |
| 模块导入 | SyntaxError、ImportError | 停止旧实例后进入 failed |
| 组件注册 | 签名冲突、依赖缺失 | 清理部分注册并进入 failed |
| 资源启动 | Adapter 或 Router 启动失败 | 清理新实例并报告 |
| 关闭资源 | 旧资源无法停止 | 阻止新实例启动，报告人工处理 |

### 推荐状态机

```text
ACTIVE
  -> CHANGE_PENDING
  -> VALIDATING
  -> STOPPING
  -> LOADING
  -> STARTING
  -> ACTIVE
```

失败状态如下。

```text
VALIDATING -> ACTIVE
LOADING    -> FAILED
STARTING   -> FAILED
```

`FAILED` 状态必须保留路径、manifest、错误类型、错误文本和 traceback，以便下一次文件变化自动重试。

### 旧版本恢复

推荐分两个阶段实现。

- 第一阶段保证失败后不留下半注册状态。配置校验失败时保留旧实例，代码导入失败时进入明确 failed 状态。
- 第二阶段增加旧模块和旧实例快照，在资源停止失败或新版本启动失败时尝试恢复旧版本。

如果旧版本恢复无法证明资源和注册表完整，必须报告失败，不能伪装成成功。

## 可观测性

每次重载至少记录以下字段。

```text
reload_id
plugin_name
change_paths
change_kind
started_at
finished_at
duration_ms
old_version
new_version
state_before
state_after
stopped_resources
started_resources
error_type
error_message
retry_count
restart_required
```

建议增加事件。

- `PLUGIN_RELOAD_STARTED`
- `PLUGIN_RELOAD_SUCCEEDED`
- `PLUGIN_RELOAD_FAILED`
- `PLUGIN_RELOAD_RETRYING`
- `PLUGIN_RELOAD_RESTART_REQUIRED`
- `PLUGIN_CONFIG_CHANGED`

## 配置项要求

建议的核心配置如下。

```toml
[runtime.plugin_hot_reload]
enabled = true
watch_plugin_code = false
watch_plugin_config = true
watch_manifest = true
debounce_ms = 200
write_stability_ms = 500
retry_failed_reload = true
max_retry_delay_ms = 5000
```

配置项必须在进程启动时读取。热加载服务本身的配置变化可以记录为需要重启，避免监听器在自身事务中被替换。

## 安全要求

- 只监听配置允许的插件根目录。
- 不跟随插件目录之外的任意符号链接，或明确记录解析后的真实路径。
- 不执行新文件中的模块代码，直到 manifest、版本和配置预检通过。
- 插件依赖安装不能直接在文件事件回调中执行。
- 上传临时目录和未完成的压缩包不能被发现为正式插件。
- 配置文件写入采用临时文件加原子替换。
- 监听器错误不能让 Bot 主循环退出。

## 验收矩阵

### 配置

- 修改已有插件 TOML，插件在不重启进程的情况下获得新值。
- 配置写入一半时不会加载不完整内容。
- 非法配置不会覆盖当前运行实例。
- 配置修复后可以自动重试。
- 修改 schema 后旧配置缓存不会继续生效。

### 代码变化边界

- 修改入口模块不会触发插件热加载。
- 修改插件 helper 模块不会触发插件热加载。
- 修改插件内资源不会触发插件热加载。
- 修改 manifest 后重新执行版本和字段校验。
- 新增插件目录后可以被发现并加载。
- 删除插件目录后可以被卸载。

### 资源

- EventHandler 不重复订阅。
- Router 不重复挂载。
- Adapter 不重复连接。
- 健康检查任务不残留。
- Tool 和 Action schema 不使用旧类。
- Chatter 活动流不会出现两个并行实例争夺同一流。

### 并发

- Dashboard 保存和文件监听不会并发重载同一插件。
- 同一批多文件修改只产生一次重载。
- 插件依赖按拓扑顺序停止和启动。
- Bot 关闭时不会创建新重载。

### 可观测性

- 每次重载有唯一 ID。
- 日志包含变化路径和结果。
- API 或管理界面可以查询失败插件。
- 需要重启的变化不会被报告为成功热加载。

## 实施阶段

### 阶段一

实现配置目录监听、单插件串行队列、配置缓存失效、事件处理器清理和基本失败状态。

验收重点是配置修改不重启即可生效。

### 阶段二

实现新增、删除插件、manifest 变化、Router 和 Adapter 生命周期清理、Tool 和 Action schema 失效。

验收重点是插件编排变化可以进入运行时，插件源码变化保持忽略。

### 阶段三

实现配置失败重试、依赖图影响范围和更细的插件编排事务。

验收重点是插件组合变化不需要重启进程。

### 阶段四

实现 `volatile` 配置字段、旧实例恢复和更细的配置更新策略。插件源码热替换仍然保持在独立的未来范围。

验收重点是减少不必要的完整插件重载，并明确不可热替换的范围。

## 不能承诺的范围

以下情况不能在不重启进程的前提下保证完全替换。

- 核心 `src/` 模块被插件直接共享且已经被宿主缓存。
- Python Worker 或独立线程拥有自己的模块缓存。
- 插件通过动态路径导入未知模块。
- 插件创建后台任务但没有向框架注册。
- 外部库在 C 扩展层保存旧类或旧回调。
- 正在执行的请求已经捕获旧插件对象。

这些情况必须进入 `restart_required` 结果，而不是静默使用旧代码。

## 最终设计判断

AstrBot 提供了 MoFox 第一阶段所需的基础模式。DSH 提供了 MoFox 第二阶段和第三阶段需要的事务队列、配置差异、模块依赖和失败诊断模型。

MoFox 不应只增加一个 `watchfiles` 任务。真正热加载必须同时处理配置缓存、模块缓存、事件注册、Router、Adapter、schema 缓存、Chatter 实例、插件依赖和失败恢复。

第一阶段建议先实现完整插件重载，保证状态一致性和可观测性，再逐步增加 `volatile` 配置和精细模块替换。
