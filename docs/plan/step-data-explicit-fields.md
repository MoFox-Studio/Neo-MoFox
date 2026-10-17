# step_data 显性化计划

## 1. 背景

`Wait / Stop / Success / Failure` 四个 chatter 结果类型上都有一个 `step_data: dict[str, Any] | None` 字段（`src/core/components/base/chatter.py:47,86,103,119`）。它的设计意图是"让 chatter 在步进完成后把本回合的元数据报给框架，由框架在发布 `after_chatter_step` 通知事件时平铺给下游订阅者"。

但这个字段是一个**完全无类型的 escape hatch**，落到代码里就是"叽里咕噜没记载的一堆东西"：

### 1.1 契约只存在于源码注释里

`chatter.py` 的 docstring 只写：

> `step_data: 可选的步骤元数据，供框架在步进完成后发布通知事件`

——没说该放什么 key、值的类型是什么、订阅者怎么读。`loop.py` 的 `_extract_step_data` / `_publish_after_chatter_step_notification` 也没说发布出去的 params 长什么样。

### 1.2 真实契约靠"看源码猜"

NDFC 与 DFC 各自维护一份 `_consume_step_data` / `_consume_actor_round_step_data`，偷偷约定返回 `{"step_scope": "actor_round", "used_tools": [...]}`：

- `plugins/neo_default_chatter/session.py:123-137` — `_consume_step_data`
- `plugins/default_chatter/session.py:25,97-111` — `_AFTER_CHATTER_STEP_SCOPE` + `_consume_actor_round_step_data`

DFC 还多塞两个 key（`tool_results` / `internal_context_ids`），但 NDFC 不塞；这两个 key 是不是契约的一部分、第三方能不能依赖，没人说。

### 1.3 消费者直接读魔法字符串

`plugins/booku_memory/event_handler.py:453-468` 的 `MemoryToolUsageWarningHandler` 是目前唯一外部消费者，直接：

```python
step_scope = str(params.get("step_scope") or "").strip()
if step_scope != "actor_round":
    return EventDecision.SUCCESS, params
used_tools = {
    str(item).strip()
    for item in params.get("used_tools", []) or []
    if str(item).strip()
}
```

——`step_scope` / `used_tools` 这两个 key 既不在框架类型里，也不在事件 schema 文档里，纯靠 NDFC/DFC 与 booku_memory 三方心照不宣。

### 1.4 平铺机制本身也隐蔽

`loop.py:67-78` 的实现：

```python
step_data = _extract_step_data(result)
params: dict[str, object] = {
    "stream_id": stream_id,
    ...
    "step_data": step_data,
}
if step_data:
    params.update(step_data)   # ← 平铺：step_data 的 key 直接提升为顶层 params key
```

这个 `params.update(step_data)` 是"契约"的实际承载点——chatter 塞进 `step_data` 的每个 key 都会变成 `after_chatter_step` 事件的顶层 param。但：

1. 它不在 `EventType.AFTER_CHATTER_STEP` 的 schema 文档里；
2. `step_data` 同时以整体 dict（`params["step_data"]`）和拆开形式（`params["step_scope"]` 等）出现，重复但不对齐；
3. chatter 想加新字段（如 `tool_results`）只需往 dict 里塞，框架无校验、无文档，第三方没法发现。

### 1.5 测试也耦合魔法字符串

- `test/plugins/test_default_chatter_session.py:870-873` 断言 `Wait.step_data == {"step_scope": "actor_round", "used_tools": [...]}`
- `test/core/transport/test_stream_loop_chatter_step_event.py:587-590` 用 `Wait(step_data={...})` 触发发布
- `test/plugins/booku_memory/test_flashback_injector.py:202-251` 用 `{"stream_id": ..., "step_scope": "actor_round", "used_tools": [...]}` 直接构造 handler 输入

## 2. 目标

把 `step_data: dict[str, Any] | None` 拆成**两个显式字段**，让"本回合做了什么"成为 chatter 结果类型的一等公民：

```python
@dataclass
class Wait:
    time: float | int | None = None
    step_scope: str | None = None
    used_tools: list[str] | None = None
```

`Stop / Success / Failure` 同步加这两个字段。

框架在发布 `after_chatter_step` 时**显式列出** `step_scope` / `used_tools` 两个 param，不再走 `params.update(step_data)` 平铺；订阅者按命名 key 读取。

### 改造前

```python
# chatter 构造
resume_event = yield Wait(step_data=_consume_step_data(state))
stop_result = Stop(cooldown, step_data=_consume_step_data(state))

# _consume_step_data 返回 dict
def _consume_step_data(state) -> dict[str, Any]:
    used_tools = sorted(state.used_tools_in_round)
    state.used_tools_in_round.clear()
    return {"step_scope": "actor_round", "used_tools": used_tools}

# 框架发布
step_data = getattr(result, "step_data", None)
params = {..., "step_data": step_data}
if step_data:
    params.update(step_data)

# 订阅者
step_scope = str(params.get("step_scope") or "").strip()
used_tools = params.get("used_tools", []) or []
```

### 改造后

```python
# chatter 构造
step_scope, used_tools = _consume_step_data(state)
resume_event = yield Wait(step_scope=step_scope, used_tools=used_tools)
stop_result = Stop(cooldown, step_scope=step_scope, used_tools=used_tools)

# _consume_step_data 返回元组
def _consume_step_data(state) -> tuple[str, list[str]]:
    used_tools = sorted(state.used_tools_in_round)
    state.used_tools_in_round.clear()
    return _AFTER_CHATTER_STEP_SCOPE, used_tools

# 框架发布
step_scope = getattr(result, "step_scope", None)
used_tools = getattr(result, "used_tools", None)
params = {
    ...,
    "step_scope": step_scope,
    "used_tools": used_tools,
}

# 订阅者（不变）
step_scope = str(params.get("step_scope") or "").strip()
used_tools = params.get("used_tools", []) or []
```

## 3. 设计要点

### 3.1 字段形状

四个 chatter 结果类型（`Wait` / `Stop` / `Success` / `Failure`）统一新增两个字段：

| 字段 | 类型 | 默认值 | 语义 |
|---|---|---|---|
| `step_scope` | `str \| None` | `None` | 本回合步骤的语义作用域。框架当前约定的唯一值是 `"actor_round"`（NDFC/DFC 共用）。`None` 表示不携带步骤元数据。 |
| `used_tools` | `list[str] \| None` | `None` | 本回合使用的工具名列表（已规范化、去空、排序与否由 chatter 自定）。`None` 或空列表表示无工具调用。 |

**为什么不用 `Literal["actor_round"]`？** 保持 `str` 而非 `Literal`，给第三方 chatter 留自定义作用域的空间（如未来的 `"planner_round"` / `"summarizer_round"`）。`step_scope` 是开放枚举，框架不强校验。

**为什么 `used_tools` 是 `list[str]` 不是 `set[str]`？** 与 `_consume_step_data` 现状对齐（`sorted(...)` 返回 list），且 list 在 dataclass 默认值上更友好（虽仍需 `field(default_factory=list)`，但本计划用 `None` 哨兵避开可变默认值问题）。订阅者侧仍按可迭代处理。

**为什么 `Success` / `Failure` 也加？** 当前 chatter 实际只 yield `Wait` / `Stop` 携带 step 信息，但 `loop.py:_extract_step_data` 用 `getattr` 对四种类型一视同仁。为保持对称、避免未来 `Success`/`Failure` 想报 step 时再改一次基类，四个类型同步加。

### 3.2 框架发布行为

`loop.py:_publish_after_chatter_step_notification` 改为：

```python
step_scope = getattr(result, "step_scope", None)
used_tools = getattr(result, "used_tools", None)
params: dict[str, object] = {
    "stream_id": stream_id,
    "context": context,
    "tick": tick,
    "chatter_name": chatter_name,
    "result": result,
    "result_type": type(result).__name__.lower(),
    "step_scope": step_scope,
    "used_tools": used_tools,
}
```

**删除 `params.update(step_data)`**：不再把任意 dict 平铺为顶层 param。`after_chatter_step` 的事件 schema 从此**显式且封闭**——只有 `step_scope` / `used_tools` 两个 step 相关字段。

**`_extract_step_data` 函数删除**，替换为 `_extract_step_scope` / `_extract_used_tools` 两个薄读取函数（或直接内联）。

### 3.3 chatter 侧 helper 形状

NDFC / DFC 各自的 `_consume_*_step_data` 函数签名从 `-> dict[str, Any]` 改为 `-> tuple[str, list[str]]`：

```python
def _consume_step_data(state: _SessionState) -> tuple[str, list[str]]:
    """汇总本回合步骤元数据，供框架在步进完成后发布 after_chatter_step 通知事件。

    同时清空 used_tools_in_round，使下一回合统计从 0 开始。
    """
    used_tools = sorted(state.used_tools_in_round)
    state.used_tools_in_round.clear()
    return _AFTER_CHATTER_STEP_SCOPE, used_tools
```

调用点从 `Wait(step_data=_consume_step_data(state))` 改为：

```python
step_scope, used_tools = _consume_step_data(state)
resume_event = yield Wait(step_scope=step_scope, used_tools=used_tools)
```

**注意"消费但不上报"分支**：NDFC `session.py:735,749` 与 DFC `session.py:837,849` 有几处 `_consume_step_data(state)` 单独调用——目的是清空 `used_tools_in_round` 但不把结果绑到任何 yield（因为该分支既不 Wait 也不 Stop）。改造后这几处仍需保留调用以维持"清空"副作用：

```python
_consume_step_data(state)  # 仅清空，丢弃返回值
```

### 3.4 DFC 的额外字段怎么办

DFC 的 `_consume_actor_round_step_data` 当前还会条件性塞 `tool_results` / `internal_context_ids` 两个 key（`session.py:108-110`）：

```python
if internal_context_ids:
    step_data["tool_results"] = tool_results
    step_data["internal_context_ids"] = internal_context_ids
```

这两个字段：

- 当前**没有任何消费者**（全代码库 `rg "tool_results"` / `"internal_context_ids"` 在 `after_chatter_step` 订阅者侧无命中）；
- 是 DFC 子代理协作遗留产物（NDFC 已移除子代理，不需要）。

**处理方式**：本计划**不**为 `tool_results` / `internal_context_ids` 在基类新增字段。DFC 改造时这两个字段直接删除——它们从未构成公开契约，删除零破坏。如果将来确有需求，再走"基类加字段 + 框架显式发布"的流程，而非塞回 dict。

### 3.5 订阅者侧零改动

`booku_memory/event_handler.py:453-468` 的 `MemoryToolUsageWarningHandler` 读的就是 `params["step_scope"]` / `params["used_tools"]`——这两个 key 在改造前后**名字不变**，只是从"dict 平铺出来"变成"框架显式发布"。订阅者代码与测试均无需改动。

## 4. 改动清单

### 4.1 框架核心

| # | 文件 | 改动 |
|---|---|---|
| C1 | `src/core/components/base/chatter.py` | `Wait / Stop / Success / Failure` 四个 dataclass：删除 `step_data: dict[str, Any] \| None` 字段；新增 `step_scope: str \| None = None` 与 `used_tools: list[str] \| None = None` 字段；同步更新四段 docstring。 |
| C2 | `src/core/transport/distribution/loop.py` | 删除 `_extract_step_data`；新增 `_extract_step_scope` / `_extract_used_tools`（或内联）；`_publish_after_chatter_step_notification` 的 params 显式列出 `step_scope` / `used_tools`，删除 `params.update(step_data)` 平铺逻辑。 |

### 4.2 插件

| # | 文件 | 改动 |
|---|---|---|
| P1 | `plugins/neo_default_chatter/session.py` | `_consume_step_data` 返回类型从 `dict[str, Any]` 改为 `tuple[str, list[str]]`，函数体返回 `(step_scope, used_tools)`；7 处调用点（`session.py:495,603,614,678,709,735,745,749`）改为解构 + 传 `step_scope=` / `used_tools=`；2 处"仅清空"调用（`735,749`）保留 `_consume_step_data(state)` 单语句。 |
| P2 | `plugins/default_chatter/session.py` | `_consume_actor_round_step_data` 同步改造：返回 `tuple[str, list[str]]`，删除 `tool_results` / `internal_context_ids` 分支；8 处调用点（`session.py:688,724,735,744,782,810,837,846,849`）改为解构 + 传字段；`_apply_stop_wake_config`（`session.py:342-352`）的 `step_data=result.step_data` 透传改为 `step_scope=result.step_scope, used_tools=result.used_tools`。 |

### 4.3 测试

| # | 文件 | 改动 |
|---|---|---|
| T1 | `test/plugins/test_default_chatter_session.py:870-873` | 断言从 `getattr(first, "step_data", None) == {"step_scope": ..., "used_tools": ...}` 改为 `getattr(first, "step_scope", None) == "actor_round"` + `getattr(first, "used_tools", None) == [...]`。 |
| T2 | `test/core/transport/test_stream_loop_chatter_step_event.py:585-591,671-673` | `Wait(step_data={...})` 改为 `Wait(step_scope="actor_round", used_tools=[...])`；断言 `after_params["step_scope"]` / `after_params["used_tools"]` 不变（key 名一致）。 |
| T3 | `test/plugins/booku_memory/test_flashback_injector.py:202-251` | **预期无需改动**——handler 输入 params 用的就是 `step_scope` / `used_tools` key 名，与改造后框架发布的 params 一致。核对一遍即可。 |

### 4.4 文档

| # | 文件 | 改动 |
|---|---|---|
| D1 | `plugins/neo_default_chatter/docs/ndfc-event-hooks.md` | §2.1 表格 `after_chatter_step` 行的"可写字段"列从"（只读）"改为"（只读）`step_scope` / `used_tools`"；§2 表格下方补一段说明这两个字段的语义与来源（chatter 结果对象的同名字段）。 |
| D2 | `plugins/neo_default_chatter/docs/ndfc-neo-default-chatter-status.md:180,297` | 第 180 行描述 `_consume_step_data` 的段落改为"返回 `(step_scope, used_tools)` 元组，分别赋给 `Wait/Stop.step_scope` / `.used_tools`"；第 297 行"可考虑提供默认 actor_round EventHandler"段落保留（本计划不引入内置订阅者）。 |

## 5. 兼容性策略

- **`step_data` 字段删除是破坏性改动**：任何第三方 chatter 若直接传 `Wait(step_data={...})`，改造后会 `TypeError`。**不提供过渡期**——`step_data` 从未文档化，按"内部契约"处理，破坏即接受。
- **订阅者侧零破坏**：`after_chatter_step` 事件的 `step_scope` / `used_tools` 两个 param key 名不变，值类型不变（`str | None` / `list[str] | None`，下游原本就按"可能为 None / 空"处理）。`booku_memory` 等现有订阅者代码与测试无需改动。
- **`params["step_data"]` 整体 dict 字段删除**：原 `loop.py` 同时发布 `params["step_data"]`（整体 dict）与平铺后的 `params["step_scope"]` 等。改造后 `params["step_data"]` 不再存在。若有订阅者读 `params["step_data"]`（全代码库检索仅 `loop.py` 内部，无外部消费者），零破坏。
- **DFC 的 `tool_results` / `internal_context_ids` 删除**：见 §3.4，无消费者，零破坏。
- **`Success` / `Failure` 的 `step_data` 同步删除**：当前无调用方给 `Success`/`Failure` 传 `step_data`（全代码库检索仅 `Wait`/`Stop` 用到），零破坏。

## 6. 风险与取舍

| 风险 | 评估 | 缓解 |
|---|---|---|
| 第三方 chatter 用了 `step_data` 字段 | 低。`step_data` 未文档化，第三方无动机用。 | 全代码库 `rg "step_data="` 检索确认仅 NDFC/DFC/测试；改造后在 PR 描述与 changelog 标注 breaking。 |
| 第三方订阅者读 `params["step_data"]` 整体 dict | 极低。该字段未文档化且与平铺字段重复，无动机读它。 | 全代码库 `rg 'params\["step_data"\]'` 检索确认无外部消费者。 |
| DFC 删除 `tool_results` / `internal_context_ids` 后丢失未来扩展点 | 低。两字段无消费者，且若未来需要应走"基类加字段"正规流程而非塞 dict。 | 改动清单 P2 注明删除理由；status.md 记录决策。 |
| `used_tools` 用 `list[str]` 而非 `set[str]` 导致重复元素 | 低。`_consume_step_data` 已 `sorted(set)` 去重（NDFC `session.py:135` 用 `sorted(state.used_tools_in_round)`，`used_tools_in_round` 是 set）。 | 改造后保留 `sorted(...)` 调用，去重行为不变。 |
| 未来想加第三个 step 字段（如 `tool_results`）需再改基类 | 中。这是显性化的代价。 | 接受——这正是"显性化"的设计目标：每个字段都该经过基类声明 + 框架发布 + 文档登记的三重显式化，而非塞 dict 走捷径。 |

## 7. 验收标准

1. `rg "step_data" --type py` 在 `src/core/` 与 `plugins/` 下的结果为 **0**（仅允许在 git 历史与文档"背景/迁移说明"中出现）。
2. `rg "params.update\(step_data\)"` 在 `src/core/transport/distribution/loop.py` 中为 **0**。
3. `Wait / Stop / Success / Failure` 四个 dataclass 均有 `step_scope: str | None = None` 与 `used_tools: list[str] | None = None` 字段，docstring 说明语义。
4. `loop.py:_publish_after_chatter_step_notification` 发布的 params 显式包含 `"step_scope"` 与 `"used_tools"` 两个 key，值来自 `result.step_scope` / `result.used_tools`。
5. NDFC `_consume_step_data` 返回类型为 `tuple[str, list[str]]`；DFC `_consume_actor_round_step_data` 同。
6. `test/plugins/booku_memory/test_flashback_injector.py` **零改动**通过（验证订阅者侧兼容）。
7. T1/T2 测试改动后全绿；`pytest test/core/transport/test_stream_loop_chatter_step_event.py test/plugins/test_default_chatter_session.py test/plugins/booku_memory/test_flashback_injector.py` 全通过。
8. `ndfc-event-hooks.md` §2.1 表格与 §2 说明段更新；`ndfc-neo-default-chatter-status.md` 第 180 行描述更新。

## 8. 不在本次范围

- **新增默认 `actor_round` 订阅者**：`ndfc-neo-default-chatter-status.md:297` 提到的"可考虑提供默认统计/审计 EventHandler"是独立议题，本计划仅显性化字段，不引入新订阅者。
- **`step_scope` 取值强校验**：保持开放字符串，不引入 `Literal` 或枚举校验。第三方 chatter 可自定义作用域。
- **`WaitResumeEvent` 相关简化**：见 `docs/plan/wait-resume-event-simplification.md` 与 `wait-resume-event-unification.md`，与本计划正交。
- **`after_chatter_step` 事件 schema 全面文档化**：本计划仅显性化 `step_scope` / `used_tools` 两个 step 相关字段；事件的其他字段（`stream_id` / `context` / `tick` / `chatter_name` / `result` / `result_type`）的 schema 化是独立文档议题。
- **`Success` / `Failure` 携带 step 信息的实际启用**：本计划给它们加上字段以保持类型对称，但不强制 chatter 在 yield `Success`/`Failure` 时填充——当前无调用方这么做，保持现状。
