# WaitResumeEvent 简化计划

## 1. 背景

当前 `BaseChatter.execute()` 被定义为双向异步生成器：

```python
async def execute(self) -> AsyncGenerator[ChatterResult, WaitResumeEvent | None]
```

这要求 chatter 在 `yield Wait()` 后，必须通过 `resume_event = yield ...` 这种"双向 yield"才能拿到框架送回的恢复原因（`WaitResumeEvent`）。这种协议对用户和开发者强加了三条隐性负担：

1. **必须掌握"双向 yield + asend"生成器技巧**。`resume = yield Wait()` 的语义不直观：它既是产出值，又是下一轮入参，新手很容易写错顺序。
2. **转发场景必须复制样板**。任何把会话逻辑转发出去的 chatter（`NeoChatter.execute`、`DefaultChatterSession.execute`、`default_chatter/plugin.py`、README 示例）都得照抄同一段 `while True: result = await runner.asend(resume); resume = yield result`。
3. **首步协议陷阱**。新建的异步生成器第一次只能 `anext()` / `asend(None)`，否则触发协议错误。`loop.py:332-358` 专门写了 prime 兜底分支来处理"生成器刚创建就遇到 resume 事件"的特殊情形，逻辑分叉多、可读性差。

结果是：一个本应"消费 WaitResumeEvent 做分支判断"的简单需求，被生成器协议细节绑架，开发者注意力被消耗在驱动机制而非业务逻辑上。

## 2. 目标

把 `WaitResumeEvent` 从 `asend` 通道搬到"聊天流侧通道"，让 `execute()` 退化为单向异步生成器，消除"双向 yield + asend + prime"三件套。

### 改造前

```python
async def execute(self) -> AsyncGenerator[ChatterResult, WaitResumeEvent | None]:
    while True:
        resume_event = yield Wait()          # 双向 yield，必须 asend 回灌
        if _is_sub_agent_resume_event(resume_event):
            ...
```

转发场景：

```python
async def execute(self):
    runner = session.execute()
    resume_event: WaitResumeEvent | None = None
    while True:
        try:
            result = await runner.asend(resume_event)
        except StopAsyncIteration:
            return
        resume_event = yield result
```

### 改造后

```python
async def execute(self) -> AsyncGenerator[ChatterResult, None]:
    while True:
        event = self.current_resume_event()   # 旁路读取，无双向 yield
        if event is not None and event.source == "sub_agent":
            ...
        yield Wait()
```

转发场景坍缩为一行：

```python
async def execute(self):
    async for result in session.execute():
        yield result
```

## 3. 设计要点

### 3.1 恢复事件的载体迁移

恢复事件不再走生成器 `asend` 通道，而是写入"聊天流上下文"侧通道。框架在步进生成器**之前**把恢复事件写入槽位，chatter 通过 `BaseChatter.current_resume_event()` 读取（take 语义：读后即清）。

存储位置直接复用 `StreamLoopManager._pending_wait_resume_events` 字典（按 `stream_id` 索引），无需在 `StreamContext` 上新增字段。只需在 `BaseChatter` 上新增一个 chatter 友好的读取入口。

### 3.2 take 语义与生命周期约定

`current_resume_event()` 的契约：

- **仅在 tick 步进开始时有效**：框架在每个 tick 步进生成器之前写入恢复事件，chatter 在本次步进内读取。
- **读后即清**：首次读取返回当前事件并清空槽位，后续在同一 tick 内重复读取返回 `None`。这避免同一事件被多次消费。
- **无恢复时返回 None**：普通 tick（无 Wait/Stop 恢复、无外部触发）返回 `None`，chatter 按正常流程处理。

### 3.3 驱动循环简化

`loop.py:332-358` 的 prime/asend 分支整段删除，统一改为 `anext(chatter_gene)`。`_take_wait_resume_event` 仍负责从 `StreamLoopManager` 取出事件，只是不再通过 `asend` 注入生成器，而是写回 `StreamLoopManager._pending_wait_resume_events[stream_id]`（供 chatter 通过 `current_resume_event()` 读取）。

注意：当前 `_wait_state_check` 已经在做"将恢复事件预填到 `_pending_wait_resume_events`"的工作，`take_wait_resume_event` 又把它取走。改造后改为：`take_wait_resume_event` 仅取出，由 loop 在步进前再写回；或更直接地，loop 不再调用 `take_wait_resume_event`，而是让 chatter 直接从 manager 读取并清空。具体走哪种实现路径在编码阶段决定，对外契约不变。

## 4. 改动清单

### 4.1 框架核心

| # | 文件 | 改动 |
|---|---|---|
| C1 | `src/core/components/base/chatter.py` | `execute()` 抽象签名改为 `AsyncGenerator[ChatterResult, None]`；新增 `current_resume_event() -> WaitResumeEvent \| None` 方法，从 `StreamLoopManager._pending_wait_resume_events` 读取并清空当前 `stream_id` 槽位。 |
| C2 | `src/core/transport/distribution/loop.py` | 删除 `loop.py:332-358` 的 prime/asend 分支，统一 `anext(chatter_gene)`；`_take_wait_resume_event` 调用点调整：取出后立即写回 manager（或保持原样让 chatter 直接读，二选一）。 |
| C3 | `src/core/transport/distribution/stream_loop_manager.py` | 无需结构性改动；如 C2 选择"chatter 直接读"路径，则 `take_wait_resume_event` 改为 `peek + pop` 语义或新增 `consume_wait_resume_event(stream_id)` 方法供 chatter 调用。 |

### 4.2 转发站点简化

| # | 文件 | 改动 |
|---|---|---|
| F1 | `plugins/neo_default_chatter/components/chatter.py` | `NeoChatter.execute` 改为 `async for result in session.execute(): yield result`。 |
| F2 | `plugins/default_chatter/session.py` | `DefaultChatterSession.execute` 的 `while True: result = await runner.asend(resume_event); resume_event = yield result` 改为 `async for result in self.execute_with_stream(...): yield result`。 |
| F3 | `plugins/default_chatter/plugin.py:213` | 同 F1 模式简化。 |
| F4 | `plugins/default_chatter/README.md:408-436` | 更新"如何驱动一个 Session"示例为 `async for + yield` 单行转发。 |
| F5 | `plugins/neo_default_chatter/docs/nfc-neo-default-chatter-design.md:560-570, 654-668` | 更新 `NeoChatter.execute` 示例与"驱动协议"章节。 |

### 4.3 chatter 内部消费点改写

| # | 文件 | 改动 |
|---|---|---|
| I1 | `plugins/default_chatter/session.py:488-570` | `_is_timer_resume_event(current_resume_event)` 等判断中的 `current_resume_event` 来源从 `yield` 接收改为 `self.current_resume_event()`（实际调用入口在 `execute_with_stream`，需把方法引用透传或在 session 持有 chatter 引用）。 |
| I2 | `plugins/neo_default_chatter/session.py:336, 437-446` | 同 I1 模式。 |

### 4.4 测试

| # | 文件 | 改动 |
|---|---|---|
| T1 | `test/core/transport/test_stream_loop_wait_state.py` | 验证恢复事件经侧通道到达 chatter；`WaitResumeEvent` 断言不变。 |
| T2 | `test/core/transport/test_stream_loop_chatter_step_event.py` | 同 T1。 |
| T3 | 新增 `test/core/components/base/test_chatter_resume_event.py` | 覆盖 `current_resume_event()` 的 take 语义：首次返回事件并清空、二次返回 None、无事件返回 None。 |

## 5. 兼容性策略

采用**双轨过渡**，避免一次性破坏所有下游 chatter：

1. **第一阶段（本次改造）**：
   - `execute()` 签名改为 `AsyncGenerator[ChatterResult, None]`。
   - 框架驱动循环只走 `anext`，不再 `asend`。
   - 老代码里 `resume_event = yield Wait()` 的写法仍能运行——因为框架不再 `asend`，`yield` 表达式的返回值恒为 `None`，老 chatter 若用 `resume_event` 做分支判断会失效，但不会报协议错误。这要求所有依赖 `resume_event` 分支的老 chatter **必须**在本次改造中迁移到 `current_resume_event()`。
   - 因此第一阶段必须同步完成 F1-F5、I1-I2 的迁移，不能留半成品。

2. **第二阶段（下个大版本）**：
   - 移除 `loop.py` 中残留的 prime/asend 兜底注释与死代码。
   - 文档全面更新，删除所有 `asend` 示例。

> 不采用"旁路 + asend 双写长期并存"方案，因为双写会让恢复事件被消费两次（一次 `asend`、一次 `current_resume_event`），chatter 侧需要做去重，反而引入新复杂度。一次性迁移更干净。

## 6. 风险与取舍

| 风险 | 评估 | 缓解 |
|---|---|---|
| `current_resume_event()` 改变了"参数传递"的可读性 | 中。从显式 `asend` 入参变成上下文隐式读取，新开发者需要理解 take 语义。 | 在 `BaseChatter.current_resume_event()` docstring 中写清楚生命周期与 take 语义；README 增加"恢复事件读取"小节。 |
| 第三方插件 chatter 未迁移会静默失效 | 高。老 chatter 的 `resume_event = yield ...` 拿到的永远是 `None`，分支不进，行为退化为"无恢复事件"。 | 第一阶段同步迁移所有已知 chatter（DFC、NFC）；在 `CHANGELOG` 与 `docs/guides` 中标注 breaking change；启动期可加一次性警告（检测 chatter 代码里是否含 `= yield` 模式并打日志）。 |
| `StreamLoopManager._pending_wait_resume_events` 被多个调用点读写，时序敏感 | 中。`_wait_state_check` 预填、`take_wait_resume_event` 取出、chatter 读取，三者时序需理清。 | 编码阶段画时序图；保持"框架在步进前写入、chatter 在步进内 consume"的单向时序；T1/T2 测试覆盖。 |
| `DefaultChatterSession.execute_with_stream` 是独立生成器，不直接持有 chatter 引用 | 中。`current_resume_event()` 是 `BaseChatter` 方法，session 需要拿到恢复事件。 | session 已持有 `self.stream_id`，可让 `current_resume_event()` 作为独立函数（基于 `stream_id` 查询 manager），或让 session 通过 `stream_api` 自行读取。优先独立函数方案，降低耦合。 |

## 7. 验收标准

1. `BaseChatter.execute` 签名为 `AsyncGenerator[ChatterResult, None]`，无 `WaitResumeEvent` 入参。
2. `loop.py` 不再出现 `asend`、`prime`、`chatter_gene_just_created` 分支。
3. `NeoChatter.execute`、`DefaultChatterSession.execute`、`default_chatter/plugin.py` 三处转发实现均为 `async for ... yield` 单行模式。
4. `current_resume_event()` 通过 T3 测试（take 语义正确）。
5. `test_stream_loop_wait_state.py`、`test_stream_loop_chatter_step_event.py` 全绿，恢复事件能正确驱动 chatter 分支。
6. DFC / NFC 端到端跑通：timer 到期、sub_agent 完成、message 唤醒、外部 `resume_chatter()` 四种来源均能被 chatter 正确识别。

## 8. 不在本次范围

- `WaitResumeEvent` 字段本身的精简（`source` / `wait_time` / `unread_count` / `context_key` / `extra` 五字段是否冗余）——单独议题。
- `Wait` / `Stop` 语义合并——`Stop` 仍保留销毁生成器 + 冷却 + 私聊直唤的复合语义，不在本次简化。
- `step_data` 通知机制的简化——`_publish_after_chatter_step_notification` 保持现状。
