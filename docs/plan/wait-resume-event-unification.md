# WaitResumeEvent 触发统一计划

## 1. 背景

`WaitResumeEvent` 是 chatter 在 `yield Wait/Stop` 之后、框架回灌给生成器的恢复信号。当前它的**触发点散落在两个 manager、共 6 处**，且对外入口不清晰：

### 1.1 内部触发（`StreamLoopManager._wait_state_check`）

`src/core/transport/distribution/stream_loop_manager.py:520-609` 在 5 个分支里**直接 `WaitResumeEvent(...)` 硬编码构造**，参数（`wait_time` / `unread_count`）从 `_wait_states` 内部状态现场拼装：

| # | 行号 | source | 触发条件 |
|---|---|---|---|
| N1 | L548 | `"message"` | `Wait(None)` 期间出现新未读 |
| N2 | L557 | `"message"` | `Wait(seconds)` 期间出现新未读 |
| N3 | L565 | `"timer"`  | `Wait(seconds)` 到期、无新未读 |
| N4 | L585 | `"message"` | `Stop` 冷却中被直接消息提前唤醒 |
| N5 | L597 | `"message"` | `Stop` 冷却结束且有新未读 |

### 1.2 外部触发

- **对外 API**：`ChatterManager.resume_chatter(stream_id, source, *, extra, context_key)`（`chatter_manager.py:152`）→ 内部转调 `StreamLoopManager.trigger_external_resume(stream_id, event)`（`stream_loop_manager.py:287`）。
- **实际调用方**：`plugins/default_chatter/sub_agent_collaboration.py:384` `resume_chatter(stream_id, source="sub_agent")`。

### 1.3 "hyw" 的具体表现

1. **对外入口分裂**：`ChatterManager.resume_chatter` 与 `StreamLoopManager.trigger_external_resume` 两个入口都"能注入恢复事件"，使用者不知道该用哪个；`WaitResumeEvent` 的 docstring（`chatter.py:60`）还把 `trigger_external_resume()` 当作对外推荐入口写出来。
2. **内部构造散落**：5 处 `WaitResumeEvent(source=..., wait_time=..., unread_count=...)` 直接写在 `_wait_state_check` 的分支里，参数计算与事件构造耦合，无法复用、无法单测。
3. **`source` 是自由字符串**：5 个约定值（`message` / `timer` / `sub_agent` / `internal_context` / 外部自定义）散落在 docstring 与注释中，IDE 无类型提示，新插件容易拼错。
4. **`resume_chatter` 入参残缺**：它只接受 `source` / `extra` / `context_key`，无法表达 `wait_time` / `unread_count`——也就是说外部插件**无法**构造一个等价于内部 `timer` / `message` 恢复的事件，能力不对等。
5. **公开方法 `trigger_external_resume` 多余**：全代码库仅 `chatter_manager.py:189` 一处调用，却以公开方法暴露在 `StreamLoopManager` 上，并被 `WaitResumeEvent` docstring 当作对外推荐入口提及，制造"两个对外入口"的错觉。

## 2. 目标

把 `WaitResumeEvent` 的**构造**与**对外触发入口**统一收敛：

1. **构造统一**：所有 `WaitResumeEvent(...)` 构造点收敛到一处工厂，参数计算与事件构造解耦。
2. **入口单一**：对外只暴露 `ChatterManager.resume_chatter` 一个 API；公开方法 `trigger_external_resume` **删除**，逻辑下沉为 `StreamLoopManager._inject_external_resume` 私有方法。
3. **source 类型化**：用 `Literal` 给 `source` 提供类型约束，IDE 可提示，约定值集中在一处。
4. **能力对等**：`resume_chatter` 扩展入参，让外部插件能完整表达 `wait_time` / `unread_count` 等字段。

## 3. 设计要点

### 3.1 source 类型化

在 `src/core/components/base/chatter.py` 中：

- 新增 `WaitResumeSource` 常量类（或 `Literal` 别名），集中列举内置约定值：

  ```python
  class WaitResumeSource:
      MESSAGE = "message"
      TIMER = "timer"
      SUB_AGENT = "sub_agent"
      INTERNAL_CONTEXT = "internal_context"
  ```

- `WaitResumeEvent.source` 类型从 `str` 改为 `str`（保留运行时宽松），但在 docstring 与 `resume_chatter` 签名中用 `Literal["message", "timer", "sub_agent", "internal_context"] | str` 标注，让类型检查器对内置值给出补全提示，同时不阻断外部自定义 source。

> 不引入运行时校验：保持 `source` 的开放语义，避免破坏现有外部插件。仅在 `resume_chatter` 内对未知 source 打一次 `debug` 日志，便于排查。

### 3.2 统一构造工厂

在 `chatter.py` 给 `WaitResumeEvent` 新增类方法 `create`：

```python
@classmethod
def create(
    cls,
    source: str,
    *,
    wait_time: float | int | None = None,
    unread_count: int = 0,
    context_key: str = "",
    extra: dict[str, Any] | None = None,
) -> "WaitResumeEvent":
    ...
```

约定：**全代码库新建 `WaitResumeEvent` 实例时一律走 `WaitResumeEvent.create(...)`**。直接 `WaitResumeEvent(...)` 仅允许出现在 `create` 内部与测试断言中。

### 3.3 内部触发收敛

把 `_wait_state_check` 中的参数计算从事件构造中剥离，提取两个纯函数：

```python
def _compute_wait_resume(
    last_yield: Wait | Stop,
    context: StreamContext,
    yielded_at: float,
    unread_count_at_yield: int,
    now: float,
) -> WaitResumeEvent | None:
    """根据 Wait/Stop 状态与当前未读数计算恢复事件；不满足恢复条件时返回 None。"""
```

- 函数返回 `WaitResumeEvent` 实例（已通过 `create` 构造）或 `None`（仍在等待）。
- `_wait_state_check` 只负责调用该函数、写入 `_pending_wait_resume_events`、清理 `_wait_states`，不再现场构造事件。
- `_should_wake_stop_early` 提前唤醒分支也并入该函数返回，不再单独写 `_pending_wait_resume_events`。

收益：参数计算可单测；`_wait_state_check` 退化为薄薄一层调度。

### 3.4 对外 API 单一化

**扩展 `ChatterManager.resume_chatter`** 签名：

```python
async def resume_chatter(
    self,
    stream_id: str,
    source: str,
    *,
    extra: dict[str, Any] | None = None,
    context_key: str = "",
    wait_time: float | int | None = None,
    unread_count: int = 0,
) -> bool:
    ...
```

- 新增 `wait_time` / `unread_count` 两个可选参数，默认值与 `WaitResumeEvent.create` 一致。
- 内部统一走 `WaitResumeEvent.create(...)` 构造，再调 `StreamLoopManager.trigger_external_resume`。
- docstring 更新：明确"这是注入恢复事件的**唯一对外入口**"，给出 `message` / `timer` / `sub_agent` / `internal_context` 四种内置 source 的示例。

**`StreamLoopManager.trigger_external_resume` 删除并改名 `_inject_external_resume`**：

- 公开方法 `trigger_external_resume` **删除**：全代码库仅 `ChatterManager.resume_chatter` 一处调用，无任何插件、文档示例、测试直接调用它，删除安全。
- 原逻辑（清 `_wait_states`、写 `_pending_wait_resume_events`、`start_stream_loop`）迁移到新私有方法 `_inject_external_resume(stream_id, event)`，仅供 `resume_chatter` 内部转调。
- 从 `WaitResumeEvent` 的 docstring（`chatter.py:60`）中移除对 `trigger_external_resume()` 的提及，改为引用 `ChatterManager.resume_chatter`。

### 3.5 路径收敛图

```
┌─────────────────────────────────────────────────────────────┐
│  内部触发                                                    │
│  StreamLoopManager._wait_state_check                        │
│    └─ _compute_wait_resume(...) -> WaitResumeEvent | None   │
│         └─ WaitResumeEvent.create(...)                      │
│             └─ 写入 _pending_wait_resume_events             │
├─────────────────────────────────────────────────────────────┤
│  外部触发                                                    │
│  插件 / sub_agent_collaboration                              │
│    └─ ChatterManager.resume_chatter(stream_id, source, ...) │
│         └─ WaitResumeEvent.create(...)                      │
│             └─ StreamLoopManager._inject_external_resume    │
│                 └─ 写入 _pending_wait_resume_events         │
│                 └─ start_stream_loop(stream_id)             │
└─────────────────────────────────────────────────────────────┘
```

两条路径都收敛到 `WaitResumeEvent.create`，差异仅在"是否需要重启 stream loop"。

## 4. 改动清单

### 4.1 框架核心

| # | 文件 | 改动 |
|---|---|---|
| C1 | `src/core/components/base/chatter.py` | 新增 `WaitResumeSource` 常量类；新增 `WaitResumeEvent.create` 类方法；更新 `WaitResumeEvent` docstring（移除 `trigger_external_resume` 提及，改引 `ChatterManager.resume_chatter`）。 |
| C2 | `src/core/transport/distribution/stream_loop_manager.py` | 提取 `_compute_wait_resume` 纯函数（含 `_should_wake_stop_early` 逻辑合并）；`_wait_state_check` 中 5 处 `WaitResumeEvent(...)` 改为 `_compute_wait_resume(...)` 返回值；**删除公开方法 `trigger_external_resume`，改名 `_inject_external_resume`**（逻辑不变，仅供 `resume_chatter` 内部转调）。 |
| C3 | `src/core/managers/chatter_manager.py` | `resume_chatter` 新增 `wait_time` / `unread_count` 入参；内部改用 `WaitResumeEvent.create`；docstring 重写为"唯一对外入口"并补四种 source 示例。 |

### 4.2 调用方核对（预期无需改动，仅验证）

| # | 文件 | 核对点 |
|---|---|---|
| V1 | `plugins/default_chatter/sub_agent_collaboration.py:384` | 已走 `resume_chatter(stream_id, source="sub_agent")`，确认无需调整。 |
| V2 | `src/core/components/base/chatter.py:60` docstring | 确认 `trigger_external_resume` 提及已移除。 |
| V3 | `plugins/*/docs/*.md`、`plugins/*/README.md` | 检索 `trigger_external_resume` 出现位置，全部改为 `ChatterManager.resume_chatter`。 |
| V4 | 全代码库 `rg "trigger_external_resume"` | 删除后应 0 命中（除本计划文档与 git 历史）。 |

### 4.3 测试

| # | 文件 | 改动 |
|---|---|---|
| T1 | 新增 `test/core/components/base/test_wait_resume_event_create.py` | 覆盖 `WaitResumeEvent.create` 默认值、显式入参、`extra=None` 与 `{}` 等价、`unread_count` 负数兜底。 |
| T2 | 新增 `test/core/transport/test_compute_wait_resume.py` | 覆盖 `_compute_wait_resume` 五种分支：`Wait(None)`+新消息、`Wait(seconds)`+新消息、`Wait(seconds)`+到期、`Stop`+提前唤醒、`Stop`+冷却结束；断言返回的 `WaitResumeEvent` 字段与原 `_wait_state_check` 行为一致。 |
| T3 | `test/core/transport/test_stream_loop_wait_state.py` | 现有断言保留；验证 `_wait_state_check` 行为不变（`WaitResumeEvent` 字段比较仍通过）。 |
| T4 | `test/core/transport/test_stream_loop_chatter_step_event.py` | 同 T3。 |
| T5 | 新增 `test/core/managers/test_chatter_manager_resume.py` | 覆盖 `resume_chatter` 新增 `wait_time` / `unread_count` 入参能正确透传到 `_inject_external_resume` 收到的事件。 |

## 5. 兼容性策略

- **`source` 仍是 `str`**：`Literal` 仅作类型提示，运行时不校验，老插件传任意字符串不受影响。
- **`resume_chatter` 新增参数全有默认值**：现有调用方（`sub_agent_collaboration._resume_actor`）零改动。
- **`WaitResumeEvent(...)` 直接构造不删除**：保留 `__init__` 不变，仅在文档与 review 规范中约定"新代码走 `create`"。老测试中的 `WaitResumeEvent(source=...)` 断言可继续工作。

## 6. 风险与取舍

| 风险 | 评估 | 缓解 |
|---|---|---|
| `_compute_wait_resume` 拆分后行为偏移 | 中。`_wait_state_check` 当前 5 处分支条件微妙（尤其 `Stop` 提前唤醒分支会提前 `return True`）。 | T2 单测逐分支对齐原行为；T3/T4 现有集成测试兜底。 |
| `Literal` 类型提示对运行时无约束 | 低。插件传未列举的字符串，类型检查器警告但运行正常。 | `resume_chatter` 内对未知 source 打 `debug` 日志；docstring 明确"内置约定值，外部插件可自定义"。 |
| `resume_chatter` 入参扩展后与 `WaitResumeEvent.create` 重复 | 低。两者签名几乎一致。 | `resume_chatter` 内部直接转发给 `create`，不重复默认值逻辑；docstring 交叉引用。 |
| 删除 `trigger_external_resume` 漏改外部调用 | 低。全代码库仅 `chatter_manager.py:189` 一处调用，无插件/测试直接引用。 | V4 验收项 `rg "trigger_external_resume"` 应 0 命中。 |

## 7. 验收标准

1. `rg "WaitResumeEvent\(" --type py` 的结果中，**非测试文件**仅出现在 `WaitResumeEvent.create` 内部；测试文件中的直接构造允许保留（断言场景）。
2. `rg "trigger_external_resume" --type py` 应 **0 命中**（公开方法已删除）。
3. `rg "_inject_external_resume" --type py` 仅出现在 `stream_loop_manager.py`（定义）与 `chatter_manager.py`（`resume_chatter` 内部转调）。
4. `ChatterManager.resume_chatter` 签名包含 `wait_time` / `unread_count` 关键字参数，且能透传到注入的 `WaitResumeEvent`。
5. `WaitResumeSource` 常量类存在，`WaitResumeEvent` docstring 引用 `ChatterManager.resume_chatter` 而非 `trigger_external_resume`。
6. T1–T5 测试全绿；T3/T4 现有断言无改动通过。
7. DFC 子代理协作端到端跑通：`sub_agent_collaboration._resume_actor` 触发的 `source="sub_agent"` 恢复事件能被 chatter 正确识别。

## 8. 不在本次范围

- **`asend` 协议简化**：见 `docs/plan/wait-resume-event-simplification.md`，本计划仅统一构造与对外入口，不改动生成器驱动协议。
- **`WaitResumeEvent` 字段精简**：`source` / `wait_time` / `unread_count` / `context_key` / `extra` 五字段是否冗余，单独议题。
- **`Wait` / `Stop` 语义合并**：`Stop` 保留销毁生成器 + 冷却 + 私聊直唤的复合语义，不在本次简化。
- **运行时 source 校验**：保持宽松，不引入枚举强校验。
