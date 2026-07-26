# 插件 API 分模块版本号设计计划

| 字段 | 内容 |
|---|---|
| 状态 | Implemented |
| 起草日期 | 2026-07-22 |
| 影响范围 | `src/app/plugin_system/api/*`、`src/core/components/loader.py`、`src/core/config/*`、`AI插件编写规范.md`、内置插件 `manifest.json`（已适配 dict 形式） |
| 相关源码 | `src/core/components/loader.py`、`src/app/plugin_system/api/__init__.py`、`src/app/plugin_system/api/*_api.py`、`src/core/config/core_config.py` |
| 相关文档 | `docs/core/components/loader.md`、`docs/guides/plugin-market/server-backend.md`、`docs/guides/plugin-market/cli.md`、`AI插件编写规范.md` 5.1.3 节、MoFox-Bot-Docs `docs/development/plugin_develop/manifest.md` |

---

## 1. 背景与动机

Neo-MoFox 当前使用单一全局常量 `PLUGIN_API_VERSION = "1.0.0"` 描述「插件 API」的整体版本。该常量在 `src/core/config/core_config.py:12` 定义，仅被 `src/core/components/loader.py` 中的 `_check_api_version_compatibility` 消费，用于在加载阶段比对插件 manifest 中声明的 `api_version` 字符串。

随着插件系统对外暴露的 API 模块数量增长到 19 个（`action_api`、`adapter_api`、`agent_api`、`chat_api`、`command_api`、`config_api`、`database_api`、`event_api`、`llm_api`、`log_api`、`media_api`、`message_api`、`permission_api`、`plugin_api`、`prompt_api`、`router_api`、`send_api`、`service_api`、`storage_api`、`stream_api`），单一版本号面临以下问题：

1. **演进粒度过粗**：任何一个 API 模块发生破坏性变更都需要整体 major 升级，导致所有插件被强制重新声明版本，即便它们并未使用该模块。
2. **版本号信息失真**：维护者无法从 manifest 中看出插件实际依赖哪些 API 模块，也无法判断一次 minor 升级是否会影响某个具体插件。
3. **缺乏演进治理**：新增模块、弃用模块、对单个模块引入破坏性变更缺乏统一、可观察的治理路径。

本计划提出「按 API 模块分版本号」方案：每个 `*_api.py` 顶部声明自己的 `API_VERSION`，在 `api/__init__.py` 聚合为 `PLUGIN_API_VERSIONS: dict[str, str]`，加载器据此对插件 manifest 中的 `api_version` 字段（兼容字符串与新引入的 dict 形式）逐一校验。

---

## 2. 现状分析

### 2.1 核心侧版本常量

- `src/core/config/core_config.py:11-12`

  ```python
  CORE_VERSION = "1.2.0-rc.2"
  PLUGIN_API_VERSION = "1.0.0"
  ```

- `src/core/config/__init__.py:27-33, 49-52` 重新导出 `PLUGIN_API_VERSION`。

- `CORE_VERSION` 同时被 `src/app/cloud_telemetry/client.py:179`、`src/app/runtime/bot.py:56` 使用，作为 Bot 版本上报。本计划不动 `CORE_VERSION`。

### 2.2 兼容性校验入口

`src/core/components/loader.py`：

- `PluginManifest` 数据类（行 262-297），其中 `api_version: str = ""`（行 293）。
- `load_manifest`（行 300-372）解析 manifest.json 并构造 `PluginManifest`，第 361 行读取 `api_version`。
- `_check_version_compatibility`（行 419-442）按优先级分发：
  1. 若 `manifest.api_version` 非空 → 走 `_check_api_version_compatibility`
  2. 否则若 `min_core_version` 非空 → 走 `_check_core_version_compatibility`
  3. 否则 → 警告但加载
- `_check_api_version_compatibility`（行 444-485）执行 major/minor/micro 整数比较：
  - major 不一致 → 拒绝
  - core 低于插件要求 → 拒绝
  - core 高于插件要求 → 警告但加载
  - 否则 → 兼容
- `_prune_unloadable_plugins`（行 539-595）在版本不兼容或依赖缺失时剔除插件，并把失败原因记入 `_failed_plugins`。

### 2.3 已发布插件的 manifest 现状

仓库内 `.gitignore` 豁免的 7 个内置插件原本使用字符串形式 `"api_version": "1.0.0"`（`perm_plugin`、`skill_manager`、`emoji_sender`、`default_chatter`、`utility_commands`、`booku_memory`、`onebot_adapter`），本计划已将其统一适配为 dict 形式，精确声明各自用到的 API 模块。其他第三方插件（如 `notice_processor` 使用 dict 形式 `{"log_api": "1.0.0", "service_api": "1.0.0"}`，其余多使用 `min_core_version` 或字符串 `api_version`）零改动可继续加载。

### 2.4 API 模块现状

`src/app/plugin_system/api/__init__.py` 显式 import 并 `__all__` 导出 19 个 `*_api` 模块。每个模块是薄 facade，内部通过 lazy import 拿到对应 manager 进行委托。模块对 core 的引用全部走 `TYPE_CHECKING` 或函数内 lazy import，运行期不存在 `app → core` 的导入环。

### 2.5 测试现状

`test/core/components/test_plugin_loader_dependencies.py` 仅覆盖插件间依赖约束（如 `"asr_adapter>=1.0.0"`），**完全没有** `_check_api_version_compatibility` 的直接测试。这是显著的测试缺口，本计划必须补齐。

---

## 3. 设计目标与非目标

### 3.1 目标

1. 每个 `*_api.py` 模块独立维护自身 API 版本号，作为该模块演进的唯一事实源。
2. 加载器在加载阶段按 manifest 声明的 API 列表逐一校验，能够针对单模块拒绝或警告。
3. 现有字符串形式 `api_version` 完全向后兼容，已发布插件无需改动。
4. 显式拒绝拼写错误的 API 名称，避免「声明了但被静默忽略」的隐式行为。
5. 不引入运行期循环导入，保持启动路径稳定。
6. 提供完整测试覆盖与文档更新。

### 3.2 非目标

- 不引入 `ComponentInclude.api_version` 子字段（保持 manifest 顶层声明）。
- 不引入 `packaging.specifiers.SpecifierSet` 等复杂约束语法（沿用现有 major/minor/micro 整数规则）。
- 不强制现存第三方插件迁移到 dict 形式（字符串形式继续合法）。

### 3.3 关键语义变更：`min_core_version` 与 `api_version` 同等判断（AND 语义）

本计划对原 `_check_version_compatibility` 的优先级分发（`api_version` 短路 `min_core_version`）做了重要修正：

- **原行为**：`api_version` 优先，存在即短路，`min_core_version` 仅为「已弃用的回退字段」。
- **现行为（AND 语义）**：声明了 `api_version` 就校验；声明了 `min_core_version` 就校验；**只要任一项声明且不满足即拒绝注册**；两者都未声明才回退到「警告但加载」。

声明是「或」关系——**按需填写**：插件 `import` 了任何 `*_api` 模块就写 `api_version`；插件用到「必须更高版本核心才支持」的能力（新事件、新内核接口、新组件机制）就写 `min_core_version`；两者都不需要也可都不写（仅警告但加载）。一旦声明，校验即按 AND 同等判断。

两个字段各司其职，**不存在二流声明**：

- `api_version`：声明插件依赖的 **插件 API 模块** 版本（按 20 个 `*_api` 模块逐一校验）。用于表达「这个插件用到了哪些 API 模块、要求它们的版本」。
- `min_core_version`：声明插件依赖的 **核心能力** 版本（基于 `CORE_VERSION` 做简单 `>=` 比较）。适用于插件依赖核心某些新功能——例如某些新的事件、新的核心组件机制、新的内核接口等——这些能力不通过 `*_api` 模块暴露，无法被 `api_version` 覆盖，必须由 `min_core_version` 兜底。

**何时该用哪个**：

| 场景 | 用 `api_version` | 用 `min_core_version` |
|---|---|---|
| 插件 `import` 了 `*_api` 模块 | ✅ 必填（dict 形式精确声明用到的模块） | 可选 |
| 插件依赖核心新事件 / 新内核接口 / 新组件机制 | ❌ 无法表达 | ✅ 必填 |
| 插件只用稳定 API、不碰核心新功能 | ✅ | 留空 |
| 插件同时用到 API 模块且依赖新核心能力 | ✅ | ✅（两者都填，AND 校验） |

---

## 4. 设计决策汇总

| 决策点 | 选择 | 说明 |
|---|---|---|
| 版本粒度 | 按 20 个 `*_api.py` 模块 | 与 `src/app/plugin_system/api/` 模块一一对应 |
| 核心侧版本源 | 每个模块顶部 `API_VERSION` 常量，在 `api/__init__.py` 聚合为 `PLUGIN_API_VERSIONS` | 单一事实源；版本随 API 文件演进 |
| `core_config.py` 中的 `PLUGIN_API_VERSION` | **删除** | 旧常量被 `PLUGIN_API_VERSIONS` 取代 |
| manifest `api_version` 字段类型 | `str \| dict[str, str]` | 字符串形式继续合法 |
| dict key 命名 | 完整模块名（如 `llm_api`、`send_api`） | 与 Python import 路径完全对应，零歧义 |
| 字符串形式语义 | 等价于「对全部 20 个 API 模块都要求该版本」 | 现存第三方插件零改动兼容 |
| 未知 API key | **拒绝加载** | 防止拼写错误被静默接受 |
| Major 不匹配语义 | 保持现规则 | major 不一致 → 拒绝；core 低于要求 → 拒绝；core 高于要求 → 警告但加载 |
| `min_core_version` 与 `api_version` 关系 | **AND 同等判断** | 两者都声明 → 都须满足；只声明其一 → 只校验该项；均未声明 → 警告但加载。`min_core_version` 不再是「弃用回退」，而是表达核心能力依赖的一等字段 |
| 循环导入规避 | loader 检查方法内 lazy import | 仅在调用时触发 `from src.app.plugin_system.api import PLUGIN_API_VERSIONS` |
| `ComponentInclude.api_version` | 不添加 | 保持组件包含声明简单 |

---

## 5. 详细设计

### 5.1 核心侧版本常量分布与聚合

#### 5.1.1 每个 `*_api.py` 顶部新增 `API_VERSION`

文件清单（共 20 个，路径前缀 `src/app/plugin_system/api/`）：

```
action_api.py、adapter_api.py、agent_api.py、chat_api.py、command_api.py、
config_api.py、database_api.py、event_api.py、llm_api.py、log_api.py、
media_api.py、message_api.py、permission_api.py、plugin_api.py、prompt_api.py、
router_api.py、send_api.py、service_api.py、storage_api.py、stream_api.py
```

每个文件在首行 docstring 与 imports 之后追加：

```python
API_VERSION = "1.0.0"
```

并将其加入该模块的 `__all__`。

所有模块初始统一为 `"1.0.0"`，与原全局 `PLUGIN_API_VERSION` 一致，保证现有字符串形式 manifest 与新机制完全等价。

#### 5.1.2 `api/__init__.py` 聚合

在现有 20 个 `from src.app.plugin_system.api import xxx_api` 之后追加：

```python
PLUGIN_API_VERSIONS: dict[str, str] = {
    "action_api": action_api.API_VERSION,
    "adapter_api": adapter_api.API_VERSION,
    "agent_api": agent_api.API_VERSION,
    "chat_api": chat_api.API_VERSION,
    "command_api": command_api.API_VERSION,
    "config_api": config_api.API_VERSION,
    "database_api": database_api.API_VERSION,
    "event_api": event_api.API_VERSION,
    "llm_api": llm_api.API_VERSION,
    "log_api": log_api.API_VERSION,
    "media_api": media_api.API_VERSION,
    "message_api": message_api.API_VERSION,
    "permission_api": permission_api.API_VERSION,
    "plugin_api": plugin_api.API_VERSION,
    "prompt_api": prompt_api.API_VERSION,
    "router_api": router_api.API_VERSION,
    "send_api": send_api.API_VERSION,
    "service_api": service_api.API_VERSION,
    "storage_api": storage_api.API_VERSION,
    "stream_api": stream_api.API_VERSION,
}
```

并将 `"PLUGIN_API_VERSIONS"` 加入 `__all__`。

> 注意：上述字典的 key 集合即「合法 API 名称全集」，校验时以此为准。

#### 5.1.3 删除 `core_config.py` 中的旧常量

- `src/core/config/core_config.py:12` 删除 `PLUGIN_API_VERSION = "1.0.0"`（保留 `CORE_VERSION`）。
- `src/core/config/__init__.py:31-32` 移除 `PLUGIN_API_VERSION` 的 import；`__init__.py:51-52` 移除 `__all__` 中的对应条目。

> 仓库全局仅 `loader.py` 一处引用 `PLUGIN_API_VERSION`，删除安全。

### 5.2 manifest 字段类型与解析

#### 5.2.1 `PluginManifest.api_version` 字段类型

`src/core/components/loader.py:293` 由：

```python
api_version: str = ""
```

改为：

```python
api_version: str | dict[str, str] = ""
```

保持空字符串作为「未声明」哨兵，dict 形式仅在 manifest 显式提供时构造。

#### 5.2.2 `load_manifest` 解析归一化

`src/core/components/loader.py:361` 由：

```python
api_version=manifest_data.get("api_version", ""),
```

改为：

```python
raw_api_version = manifest_data.get("api_version", "")
if isinstance(raw_api_version, dict):
    api_version: str | dict[str, str] = {
        str(k): str(v) for k, v in raw_api_version.items()
    }
else:
    api_version = str(raw_api_version or "")
```

并将 `api_version=api_version` 传入 `PluginManifest(...)` 构造。

#### 5.2.3 字段语义说明

- 字符串形式 `"1.0.0"`：等价于「对该版本声明时全部 19 个 API 模块都要求 `1.0.0`」。当核心新增 API 模块时，已发布的字符串 manifest 会自动覆盖新模块（要求新模块版本 ≤ 该字符串），不会因新模块引入而被拒绝。
- dict 形式 `{"llm_api": "1.0.0", "send_api": "1.2.0"}`：仅校验声明的 keys，未声明的模块不校验。

### 5.3 兼容性校验算法

#### 5.3.1 `_check_api_version_compatibility` 重写

`src/core/components/loader.py:444-485` 整体重写为：

```python
def _check_api_version_compatibility(
    self, manifest: PluginManifest
) -> tuple[bool, str]:
    """基于 PLUGIN_API_VERSIONS 的逐模块语义化版本兼容检查。"""
    from src.app.plugin_system.api import PLUGIN_API_VERSIONS  # lazy import

    # 1) 归一化为 dict 形式
    if isinstance(manifest.api_version, str):
        if not manifest.api_version:
            reason = "api_version 为空字符串，无法校验"
            logger.error(f"插件 '{manifest.name}' {reason}")
            return False, reason
        # 字符串形式：等价于对所有 API 模块应用同一要求
        plugin_reqs: dict[str, str] = {
            name: manifest.api_version for name in PLUGIN_API_VERSIONS
        }
    else:
        plugin_reqs = dict(manifest.api_version)

    # 2) 校验所有 key 都是已知 API 模块
    unknown = sorted(set(plugin_reqs) - set(PLUGIN_API_VERSIONS))
    if unknown:
        reason = f"manifest 声明了未知的 API 模块: {', '.join(unknown)}"
        logger.error(f"插件 '{manifest.name}' {reason}")
        return False, reason

    # 3) 逐模块比对
    reject_reasons: list[str] = []
    warn_reasons: list[str] = []
    for api_name, plugin_req_str in plugin_reqs.items():
        core_ver_str = PLUGIN_API_VERSIONS[api_name]
        try:
            plugin_req = Version(plugin_req_str)
            core_api = Version(core_ver_str)
        except InvalidVersion as e:
            reason = (
                f"API '{api_name}' 版本号格式无效："
                f"插件='{plugin_req_str}', 核心='{core_ver_str}' - {e}"
            )
            logger.error(f"插件 '{manifest.name}' {reason}")
            return False, reason

        if core_api.major != plugin_req.major:
            reject_reasons.append(
                f"API '{api_name}' 主版本不匹配 "
                f"(插件={plugin_req.major}, 核心={core_api.major})"
            )
            continue

        if core_api.minor < plugin_req.minor or (
            core_api.minor == plugin_req.minor and core_api.micro < plugin_req.micro
        ):
            reject_reasons.append(
                f"API '{api_name}' 核心版本 {core_ver_str} 低于插件要求 {plugin_req_str}"
            )
            continue

        if core_api.minor > plugin_req.minor:
            warn_reasons.append(
                f"API '{api_name}' 核心 {core_ver_str} 高于插件要求 {plugin_req_str}"
            )

    # 4) 聚合结果
    if reject_reasons:
        reason = "；".join(reject_reasons)
        logger.warning(f"插件 '{manifest.name}' API 版本不兼容：{reason}")
        return False, reason

    if warn_reasons:
        reason = "部分 API 次版本不一致，可能存在非兼容变更：" + "；".join(warn_reasons)
        logger.warning(f"插件 '{manifest.name}' {reason}")
        return True, reason

    return True, "兼容"
```

#### 5.3.2 调度入口改为 AND 同等判断

`_check_version_compatibility` 由「优先级短路分发」改为「AND 同等判断」：声明了哪一项就校验哪一项，只要任一项声明且不满足即拒绝注册。

```python
def _check_version_compatibility(
    self, manifest: PluginManifest
) -> tuple[bool, str]:
    """检查插件版本兼容性（AND 语义）。

    api_version 与 min_core_version 同等判断：
    - 声明了 api_version 就校验；声明了 min_core_version 就校验。
    - 只要任一项声明且不满足 → 拒绝注册。
    - 两者都未声明 → 允许加载但发出警告。

    两字段各司其职：
    - api_version：声明插件 API 模块版本（按 20 个 *_api 模块逐一校验）。
    - min_core_version：声明核心能力版本（基于 CORE_VERSION 做简单 >= 比较），
      适用于插件依赖核心某些新功能（如某些新事件、新的核心组件机制）。
    """
    failures: list[str] = []
    warnings: list[str] = []
    checked_any = False

    if manifest.api_version:
        checked_any = True
        ok, reason = self._check_api_version_compatibility(manifest)
        if not ok:
            failures.append(reason)
        elif reason and reason != "兼容":
            warnings.append(f"api_version: {reason}")

    if manifest.min_core_version:
        checked_any = True
        ok, reason = self._check_core_version_compatibility(manifest)
        if not ok:
            failures.append(reason)
        elif reason and "兼容" not in reason:
            warnings.append(f"min_core_version: {reason}")

    if not checked_any:
        logger.warning(
            f"插件 '{manifest.name}' 未声明 api_version 或 min_core_version，"
            "无法保证兼容性，将尝试加载"
        )
        return True, "未声明版本要求，已尝试加载"

    if failures:
        return False, "；".join(failures)

    if warnings:
        return True, "；".join(warnings)

    return True, "兼容"
```

> 关键修正：原 `min_core_version` 被标注为「已弃用」并附带迁移警告日志。本计划删除该弃用提示，
> `_check_core_version_compatibility` 改为静默执行 `>=` 比较，使其成为表达「核心能力依赖」的一等字段。
> `PluginManifest.min_core_version` 默认值由 `"1.0.0"` / `load_manifest` 中的 `"3.0.0"` 统一改为空字符串 `""`，
> 以便「未声明即跳过」语义在 AND 判断下正确生效（避免未声明 `min_core_version` 的插件被默认值误判为不兼容）。

#### 5.3.3 `_check_core_version_compatibility` 去弃用化

`_check_core_version_compatibility` 删除「已弃用」docstring 与迁移警告日志，改为：

```python
def _check_core_version_compatibility(
    self, manifest: PluginManifest
) -> tuple[bool, str]:
    """基于 CORE_VERSION 的核心版本兼容检查。

    min_core_version 声明插件依赖的核心能力版本（如某些新事件、新的核心
    组件机制），与 api_version 同等判断。当 api_version 与
    min_core_version 同时声明时，两者必须都满足才能通过注册。
    """
    try:
        current_version = Version(CORE_VERSION)
        required_version = Version(manifest.min_core_version)
        is_compatible = current_version >= required_version
    except InvalidVersion as e:
        reason = (
            f"版本号格式无效：min_core_version='{manifest.min_core_version}'，"
            f"CORE_VERSION='{CORE_VERSION}' - {e}"
        )
        logger.error(f"插件 '{manifest.name}' {reason}")
        return False, reason

    if not is_compatible:
        return (False, f"核心版本不兼容，需要 {manifest.min_core_version}")

    return True, "兼容"
```

### 5.4 未知 key 拒绝策略

在 5.3.1 第 2 步显式检查 `set(plugin_reqs) - set(PLUGIN_API_VERSIONS)`，发现任何未知 key（如 `"foo_api"`、拼写错误的 `"lmm_api"`）立即返回 `(False, reason)`，并把失败原因写入 `_failed_plugins`，插件被剔除。

### 5.5 字符串形式向后兼容

字符串形式归一化为「全部 20 个模块的 dict」。这意味着：

- 现存使用 `"api_version": "1.0.0"` 的第三方插件被等价处理为对所有 20 个 API 模块要求 `1.0.0`，由于所有 `*_api.py` 的初始 `API_VERSION` 也是 `"1.0.0"`，校验结果为「兼容」。
- 未来核心新增 API 模块时，已发布字符串 manifest 会自动覆盖新模块；只要新模块的 `API_VERSION` 仍为 `1.0.0`，旧 manifest 不会因新模块引入而被拒绝。
- 一旦某个 API 模块升级到 `2.0.0`，使用字符串 `"1.0.0"` 的插件将因该模块 major 不匹配而被拒绝——这是预期行为，因为该插件事实上无法使用破坏性升级后的 API。

### 5.6 循环导入规避

`loader.py` 位于 core 层，`PLUGIN_API_VERSIONS` 位于 app 层。直接顶层 import 会形成 `core → app` 静态依赖，违反三层架构。

规避方式：在 `_check_api_version_compatibility` 方法体内 lazy import：

```python
def _check_api_version_compatibility(self, manifest: PluginManifest) -> tuple[bool, str]:
    from src.app.plugin_system.api import PLUGIN_API_VERSIONS  # lazy import
    ...
```

该 import 仅在 `plan_plugins` → `_prune_unloadable_plugins` → `_check_version_compatibility` 调用链中被触发，此时 app 层已完成 bootstrap，import 必然成功。

---

## 6. 变更清单（按文件）

### 6.1 新增/修改

| 文件 | 变更类型 | 内容 |
|---|---|---|
| `src/app/plugin_system/api/{20 个 *_api.py}` | 修改 | 顶部新增 `API_VERSION = "1.0.0"` 并加入 `__all__` |
| `src/app/plugin_system/api/__init__.py` | 修改 | 聚合 `PLUGIN_API_VERSIONS` dict 并加入 `__all__` |
| `src/core/config/core_config.py` | 修改 | 删除 `PLUGIN_API_VERSION = "1.0.0"`（行 12） |
| `src/core/config/__init__.py` | 修改 | 移除 `PLUGIN_API_VERSION` 的 import 与 `__all__` 条目 |
| `src/core/components/loader.py` | 修改 | 见 5.2、5.3、5.6；`PluginManifest.min_core_version` 默认值改为 `""`，`load_manifest` 默认值同步改为 `""` |
| `plugins/{7 个内置插件}/manifest.json` | 修改 | 内置插件（`.gitignore` 豁免）的 `api_version` 由字符串改为 dict 形式，精确声明所用 API 模块 |
| `test/core/components/test_plugin_loader_api_version.py` | 新增 | 见第 8 节 |
| `AI插件编写规范.md` 5.1.3 节 | 修改 | 更新 `api_version` 字段说明，增补 dict 形式示例；去除 `min_core_version` 弃用措辞，说明两者同等判断 |
| `AI插件编写规范.md` 9.1 节 | 修改 | 最小 manifest 模板增补 dict 形式示例（保留字符串示例作为最简形式） |
| `docs/core/components/loader.md` | 修改 | 在「注意事项」补充 API 版本逐模块校验与 AND 语义说明 |
| `docs/core/components/README.md` | 修改 | 在「文档导航」加入本文档链接 |
| MoFox-Bot-Docs `manifest.md` | 修改 | 新增 `api_version` 字段，显式说明 `api_version` 与 `min_core_version` 的区别与同等判断语义 |

### 6.2 不动

- `src/core/components/base/*`（基类体系不变）
- `src/core/components/types.py`（`ComponentType` 枚举不变）
- `src/core/components/loader.py` 中 `ComponentInclude` 数据类（不添加 `api_version` 子字段）
- 第三方插件（不在 `.gitignore` 豁免清单内）的 `manifest.json`（字符串形式继续合法，不强制迁移）

---

## 7. 兼容性策略

### 7.1 字符串 → dict 自动归一化

加载器在 `_check_api_version_compatibility` 内部将字符串形式自动展开为「全 20 模块 dict」，与 dict 形式走同一条校验路径。无需 manifest 作者感知差异。

### 7.3 文档迁移建议

`AI插件编写规范.md` 与 MoFox-Bot-Docs `manifest.md` 中：

- 5.1.3 节 / `manifest.md` 说明 `api_version` 同时接受字符串与 dict，并推荐使用 dict 形式以便精确声明依赖。
- 显式说明 `api_version` 与 `min_core_version` 的区别：前者面向「插件 API 模块」，后者面向「核心能力」（如新事件、新内核接口），两者**同等判断**，不区分主次。
- 9.1 节最小 manifest 模板：保留字符串示例作为「最简形式」；新增「推荐形式」展示 dict 示例。

不强制现存第三方插件迁移，但建议新建插件采用 dict 形式。内置插件（`.gitignore` 豁免清单内）已统一适配为 dict 形式。

---

## 8. 测试计划

### 8.1 新增测试文件

`test/core/components/test_plugin_loader_api_version.py`，至少覆盖以下场景：

| # | 场景 | 输入 | 预期结果 |
|---|---|---|---|
| 1 | 字符串形式，全兼容 | `api_version="1.0.0"`，所有 `API_VERSION="1.0.0"` | `(True, "兼容")` |
| 2 | dict 形式，全兼容 | `{"llm_api":"1.0.0","send_api":"1.0.0"}` | `(True, "兼容")` |
| 3 | 字符串形式，单模块 major 不匹配 | monkeypatch `llm_api.API_VERSION="2.0.0"`，`api_version="1.0.0"` | `(False, ...)` |
| 4 | dict 形式，单模块 major 不匹配 | `{"llm_api":"1.0.0"}`，monkeypatch `llm_api.API_VERSION="2.0.0"` | `(False, ...)` |
| 5 | dict 形式，core 低于要求 | `{"llm_api":"1.5.0"}`，monkeypatch `llm_api.API_VERSION="1.2.0"` | `(False, ...)` |
| 6 | dict 形式，core 高于要求 | `{"llm_api":"1.0.0"}`，monkeypatch `llm_api.API_VERSION="1.5.0"` | `(True, warn)` |
| 7 | dict 形式，未知 key | `{"foo_api":"1.0.0"}` | `(False, ...)` |
| 8 | dict 形式，混合已知与未知 | `{"llm_api":"1.0.0","bar_api":"1.0.0"}` | `(False, ...)` |
| 9 | 空字符串 | `api_version=""` | 走 `min_core_version` 或警告分支 |
| 10 | 空 dict | `{}` | 视为已声明但无模块 → 兼容（无校验对象） |
| 11 | 版本号格式无效 | `{"llm_api":"not-a-version"}` | `(False, ...)` |
| 12 | 字符串与等价 dict 结果一致 | `api_version="1.0.0"` vs `{all 19 modules: "1.0.0"}` | 结果相同 |
| 13 | 集成：`_prune_unloadable_plugins` 剔除 | 构造一个 manifest 不兼容，验证被加入 `_failed_plugins` | 剔除成功 |
| 14 | 集成：`_prune_unloadable_plugins` 不影响其他 | 不兼容插件被剔除后，依赖它的插件也被剔除 | 递归剔除 |

### 8.2 回归测试

- `test/core/components/test_plugin_loader_dependencies.py`：现有插件间依赖解析测试，验证不受影响。
- `test/core/components/test_plugin_manager_*.py`：现有 PluginManager 测试，验证 `PluginManifest` 字段类型变更不破坏构造。

### 8.3 冒烟测试

启动 bot，确认 7 个内置插件（`perm_plugin`、`skill_manager`、`emoji_sender`、`default_chatter`、`utility_commands`、`booku_memory`、`onebot_adapter`，均已适配 dict 形式 `api_version`）与若干第三方插件（如 `notice_processor`）全部正常加载。

---

## 9. 文档同步

### 9.1 `AI插件编写规范.md`

#### 5.1.3 节（行 334-367）

- 将 `api_version` 字段说明从「字符串」更新为「字符串 | dict[str, str]」。
- 说明字符串形式语义：等价于对该版本声明时全部 20 个 API 模块应用同一要求。
- 说明 dict 形式语义：仅校验声明模块，未声明模块不校验。
- 说明未知 API key 行为：拒绝加载。
- **去除 `min_core_version` 的「已弃用」措辞**，改为说明其与 `api_version` 同等判断（AND 语义），用于声明核心能力依赖。
- 提供 dict 形式示例：

  ```json
  "api_version": {
    "llm_api": "1.0.0",
    "send_api": "1.2.0",
    "service_api": "1.0.0"
  }
  ```

- 列出全部合法 API 名称（20 个模块名）。

#### 9.1 节（行 601-626）

- 保留字符串示例作为「最简形式」。
- 新增「推荐形式」展示 dict 示例。

### 9.2 `docs/core/components/loader.md`

在「注意事项」补充：

> - API 版本兼容性按 20 个 `*_api` 模块逐一校验。manifest 中 `api_version` 支持字符串（等价于对所有模块应用同一要求）与 dict（仅校验声明模块）两种形式。
> - `api_version` 与 `min_core_version` **同等判断（AND 语义）**：声明了哪一项就校验哪一项，只要任一项声明且不满足即拒绝注册。前者面向插件 API 模块版本，后者面向核心能力版本（如新事件、新内核接口）。
> - 详见 [plugin-api-versioning.md](./plugin-api-versioning.md)。

### 9.3 `docs/core/components/README.md`

在「文档导航」加入：

```markdown
- [plugin-api-versioning](./plugin-api-versioning.md): 按 API 模块的版本号设计与校验
```

### 9.4 `docs/guides/plugin-market/server-backend.md` 与 `cli.md`

这两个文档中提到的 `plugin_api_version` 字段（server-backend.md:69、cli.md:103）属于市场端的提案字段，语义上对应本计划的 `api_version`。本次不强制对齐命名，但建议在后续市场端落地时统一为 `api_version`，并支持 dict 形式存储。

---

## 10. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 循环导入：`loader.py` 顶层引用 `PLUGIN_API_VERSIONS` 导致启动失败 | 高 | 严格 lazy import，仅在 `_check_api_version_compatibility` 方法体内 import |
| `PLUGIN_API_VERSIONS` 在加载器调用时未初始化 | 中 | `_check_api_version_compatibility` 仅在 `plan_plugins` 内被调用，此时 app 已 bootstrap 完成 |
| AND 语义下，旧默认值 `min_core_version="3.0.0"` 误杀未声明该字段的插件 | 高 | `PluginManifest.min_core_version` 与 `load_manifest` 默认值统一改为 `""`，未声明即跳过校验 |
| 第三方插件的 manifest 仍是字符串形式 | 低 | 字符串 → 全模块复制语义保证零改动兼容 |
| 字符串形式 manifest 在核心新增 API 模块时被意外拒绝 | 中 | 新模块初始 `API_VERSION="1.0.0"`；现有字符串 manifest（多为 `"1.0.0"`）会自动覆盖新模块 |
| 单模块 major 升级导致使用字符串形式的所有插件被拒绝 | 中 | 这是预期行为：该插件事实上无法使用破坏性升级后的 API。文档应明确建议新插件采用 dict 形式 |
| 拼写错误的 API key 被静默接受 | 中 | 未知 key 显式拒绝加载，详见 5.4 |
| `ComponentInclude` docstring 与 `ComponentType` 不一致（含 `collection`，缺 `agent`/`config`） | 低 | 与本计划无关，不在范围内；可在同一 PR 顺手修正 docstring |

---

## 11. 验证步骤

实现完成后依次执行：

1. **常量聚合验证**

   ```bash
   python -c "from src.app.plugin_system.api import PLUGIN_API_VERSIONS; import json; print(json.dumps(PLUGIN_API_VERSIONS, indent=2, ensure_ascii=False))"
   ```

   应输出包含 20 个 key 的 dict（与 `api/__init__.py` 中导入的 20 个 `*_api` 模块一一对应）。

2. **旧常量已删除**

   ```bash
   python -c "from src.core.config import PLUGIN_API_VERSION"
   ```

   应抛出 `ImportError`。

3. **新测试通过**

   ```bash
   python -m pytest test/core/components/test_plugin_loader_api_version.py -v
   ```

4. **回归测试通过**

   ```bash
   python -m pytest test/core/components/test_plugin_loader_dependencies.py -v
   python -m pytest test/core/components/test_plugin_manager_*.py -v
   ```

5. **冒烟测试**：启动 bot，确认 8 个示例插件全部正常加载。

6. **lint 与 typecheck**：执行项目约定的 lint/typecheck 命令（如 `ruff check`、`mypy`）。

---

## 12. 后续工作

本计划完成后，可作为后续演进的基础：

1. **CLI 工具支持**：插件市场 CLI（`docs/guides/plugin-market/cli.md`）在打包时自动扫描插件源码中对 `*_api` 模块的 import，生成 dict 形式的 `api_version`，避免作者手工维护。
2. **市场端存储**：插件市场后端（`docs/guides/plugin-market/server-backend.md`）的 `PluginVersion.plugin_api_version` 字段升级为 JSON dict 存储，支持按模块查询兼容性。
3. **运行期 API 版本查询**：在 `plugin_api.py` 中暴露 `get_api_version(api_name: str) -> str | None`，允许插件在运行时查询核心实际提供的 API 版本，便于动态适配。
4. **`ComponentInclude.api_version` 子字段**：若未来出现「同一插件对不同组件要求不同 API 版本」的需求，可在 `ComponentInclude` 上引入子字段覆盖 manifest 顶层声明。当前不引入。

---

## 13. 附录：合法 API 模块名称清单

以下 20 个名称是 manifest `api_version` dict 形式中合法的 key（与 `src/app/plugin_system/api/__init__.py` 导入的模块一一对应）：

```
action_api
adapter_api
agent_api
chat_api
command_api
config_api
database_api
event_api
llm_api
log_api
media_api
message_api
permission_api
plugin_api
prompt_api
router_api
send_api
service_api
storage_api
stream_api
```

任何不在此清单中的 key 都会被拒绝加载。
