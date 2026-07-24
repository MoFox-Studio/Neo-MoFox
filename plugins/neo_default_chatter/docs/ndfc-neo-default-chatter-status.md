# NDFC（Neo-Default-Chatter）现状文档

> 全名：**Neo-Default-Chatter**，插件标识 `neo_default_chatter`。
> 本文中简称 **NDFC**（Neo-Default-Fox-Chatter，强调与 MoFox 主干项目的归属关系）。
> 与同目录设计文档 `nfc-neo-default-chatter-design.md` 中使用的「NFC」缩写指向同一个插件，仅命名习惯不同。
> 类型：Neo-MoFox 插件，遵循 `Neo-MoFox/AI插件编写规范.md`。
> 当前版本：`0.1.0`。
> 状态：**核心已实现并可用**，相对设计文档存在能力扩展（详见 §4、§5）。

## 1. 文档目的

本文档对照 `nfc-neo-default-chatter-design.md`（设计阶段）梳理代码落地后的真实状态：

- 哪些设计目标已实现；
- 哪些设计目标在实现阶段被调整 / 增强；
- 哪些项尚未落地或留待后续。

阅读对象：插件维护者、需要复用 NDFC 主会话逻辑的第三方插件作者。

## 2. 实现进度总览

| 设计章节 | 设计目标 | 当前状态 | 备注 |
| --- | --- | --- | --- |
| §2 定位 | Plugin / Chatter / Service / 3 Action / 1 Config | ✅ 已落地 | 额外新增 2 个 EventHandler |
| §3 架构 | ConversationSession 自包含主流程 | ✅ 已落地 | `session.py` 809 行，状态机式实现 |
| §4 组件清单 | 5 个注册组件 | ✅ 已落地 + 2 新增 | manifest 已同步登记 |
| §5 会话流程 | 8 步主流程 | ✅ 已落地 | 详见 §3.2 |
| §6 配置 | 9 字段 + theme_guide 子节 | ✅ 已落地 + 3 子节 | 详见 §4.2 |
| §7 原生多模态 | 占位符内联 + base64 | ✅ 已落地 | `utils/multimodal.py` |
| §8 stop 冷却 | Stop + 直接唤醒概率 | ✅ 已落地 | `_apply_stop_wake` |
| §9 默认动作契约 | send_text / pass_and_wait / stop_conversation | ✅ 已落地 | `actions/` 完整 |
| §10 目录结构 | components + utils | ✅ 已落地 + 1 子目录 | 新增 `event_handlers/` |
| §11 manifest | 注册 5 组件 | ✅ 已落地 | 实际为 7 组件 |
| §12 关键文件骨架 | chatter.py / plugin.py | ✅ 已落地 | 与设计基本一致 |
| §13 Service 对外契约 | `create_session(stream_id, plugin)` | ✅ 已落地 | 签名严格匹配设计 |
| §14 与 default_chatter 关系 | 更瘦可复用骨架 | ⚠️ 偏离 | NDFC 已内建预处理策略（详见 §5） |
| §15 实现路线 8 步 | 1-7 已完成，8 单测未完成 | 🟡 进行中 | 路线 1-7 完成，路线 8 未落地 |

## 3. 架构与主流程现状

### 3.1 整体结构

```
plugins/neo_default_chatter/
├── manifest.json
├── __init__.py
├── plugin.py                      # NeoChatterPlugin：注册 4 个 prompt 模板
├── session.py                     # ConversationSession：809 行状态机
├── components/
│   ├── chatter.py                 # NeoChatter：纯 forward yield/asend
│   ├── config.py                  # NeoChatterConfig：扩展为 4 个子节
│   ├── service.py                 # NeoChatterService：create_session
│   ├── actions/
│   │   ├── send_text.py           # SendTextAction（打字延迟 + reply_to + at）
│   │   └── control.py             # PassAndWaitAction + StopConversationAction
│   └── event_handlers/            # 【新增】设计文档未列出的子目录
│       ├── probability_bypass.py  # ProbabilityBypassHandler (weight=100)
│       └── sub_agent_decision.py  # SubAgentDecisionHandler (weight=50)
└── utils/
    ├── preprocess.py              # run_preprocess + PreprocessDecision
    ├── multimodal.py              # extract_images + inline_images
    ├── prompt_builder.py          # NeoChatterPromptBuilder（4 套模板）
    ├── tool_flow.py               # process_tool_calls + SUSPEND 闭合
    └── prompts.py                 # system / user / sub_agent_* 模板原文
```

### 3.2 ConversationSession 状态机

实际实现的会话状态机由 `_Phase` 枚举驱动，与设计文档 §5.1 的「8 步线性流程」相比更结构化：

```
WAIT_USER ──(收到未读/恢复事件)──▶ MODEL_TURN ──(LLM 响应)──▶ TOOL_EXEC
   ▲                                  │                          │
   │                                  │                          ▼
   │                                  │                  ┌─ pass_and_wait ─▶ Wait()
   │                                  │                  ├─ stop_conversation ─▶ Stop() 终态
   │                                  │                  ├─ 纯 action 回合 ─▶ Wait()（受 enable_action_suspend）
   │                                  │                  └─ 有待消化工具结果 ─▶ FOLLOW_UP ─┐
   │                                  │                                                  │
   └────────── Wait() ────────────────┴──────── FOLLOW_UP ──(二次 LLM 请求)──────────────┘
```

关键阶段行为：

- **WAIT_USER**：拉未读；无未读且无 resume → `yield Wait()`。有未读时跑 `run_preprocess`，根据决策 `proceed / force_stop_minutes` 决定 Stop / Wait / 进入 MODEL_TURN。
- **MODEL_TURN / FOLLOW_UP**：`response.send(stream=False)` + `await response`；失败时 `yield Failure(...)` 回到 WAIT_USER。
- **TOOL_EXEC**：渲染 Actor 决策面板 → `process_tool_calls` 拦截控制流 → 根据 `ToolCallOutcome` 走 Wait / Stop / FOLLOW_UP / WAIT_USER。
- **FOLLOW_UP**：带着上一轮 TOOL_RESULT 再发一次 LLM 请求。

### 3.3 恢复事件处理

`WaitResumeEvent.source` 的三种来源已在 `session.py` 显式处理：

- `"message"`：把 resume 文本「吃掉」，新消息走未读路径；
- `"timer"`：注入 `_build_timer_resume_prompt`，告知 LLM「等待已结束，无新消息，请自决」；
- 其他（sub_agent / internal_context 等）：优先用 `event.extra["resume_prompt"]`，否则按 source 标签生成默认提示。

## 4. 组件实现状态

### 4.1 注册组件清单

`manifest.json` 与 `plugin.get_components()` 一致，共注册 7 个组件：

| 组件 | 类型 | name | 实现文件 | 设计章节 |
| --- | --- | --- | --- | --- |
| `NeoChatter` | Chatter | `neo_default_chatter` | `components/chatter.py` | §12.1 |
| `NeoChatterService` | Service | `chat_core` | `components/service.py` | §13 |
| `SendTextAction` | Action | `send_text` | `components/actions/send_text.py` | §9.1 |
| `PassAndWaitAction` | Action | `pass_and_wait` | `components/actions/control.py` | §9.2 |
| `StopConversationAction` | Action | `stop_conversation` | `components/actions/control.py` | §8.1 / §9.3 |
| `ProbabilityBypassHandler` | EventHandler | `probability_bypass` | `components/event_handlers/probability_bypass.py` | **设计未列出** |
| `SubAgentDecisionHandler` | EventHandler | `sub_agent_decision` | `components/event_handlers/sub_agent_decision.py` | **设计未列出** |

> Config（`NeoChatterConfig`）通过 `plugin.configs = [NeoChatterConfig]` 注册，未在 `include` 里登记，符合规范。

### 4.2 NeoChatterConfig 配置现状

实现版本相对设计 §6 的字段表做了以下调整：

| 字段 | 设计默认 | 实现默认 | 备注 |
| --- | --- | --- | --- |
| `enabled` | `true` | **`false`** | 默认不启用，需用户显式开启 |
| `native_multimodal` | `false` | `false` | 一致 |
| `image_placeholder_template` | `[图片-{idx}]` | `[图片-{idx}]` | 一致 |
| `enable_stop_direct_message_wake` | `false` | `false` | 一致 |
| `stop_direct_message_wake_probability` | `0.5` | `0.5` | 一致 |
| `reinforce_negative_behaviors` | `true` | `true` | 一致 |
| `default_stop_minutes` | `5.0` | `5.0` | 一致 |
| `actor_task_name` | `"actor"` | `"actor"` | 一致 |
| `theme_guide.private` / `theme_guide.group` | 长文案默认值 | 长文案默认值 | 一致 |

新增字段（设计文档未提及）：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `enable_cooldown` | `true` | 是否启用 stop 冷却；关闭时冷却时间归零，避免 LLM 设过长冷却 |
| `enable_action_suspend` | `true` | 纯 Action 回合是否注入 `__SUSPEND__` 占位挂起 |

新增子节（设计文档未提及）：

- `preprocess_probability_bypass`：5 字段（`enabled`、`base_bypass_probability=0.1`、`name_mention_bonus=0.7`、`alias_mention_bonus=0.4`、`unread_message_bonus=0.05`）。
- `preprocess_sub_agent`：6 字段（`enabled`、`task_name="actor"`、`request_name`、`max_context_messages=8`、`max_unread_messages=10`、`decision_temperature=0.2`）。

### 4.3 Action 实现细节

- **`SendTextAction`**（`actions/send_text.py:28`）：
  - 已实现打字延迟（`_TYPING_DELAY_PER_CHAR=0.5`、上限 10 秒），通过 `chat_stream.context._neo_default_chatter_last_send_text_time` 跨轮记录。
  - 支持三种模式：普通文本、引用回复（`reply_to`）、@ 用户（`at`）。
  - `content` 中混入的 `reason:` 前缀与 `@xxx` 前缀会被自动剥离，避免污染输出。
- **`PassAndWaitAction`**（`actions/control.py:8`）：纯登记器，返回 `tuple[bool, str]`；真正的 `Wait` 生成由 `process_tool_calls` + `session.py` 完成。
- **`StopConversationAction`**（`actions/control.py:35`）：同上，minutes 缺省时由 `tool_flow.process_tool_calls` 退回 `default_stop_minutes`。

### 4.4 ConversationSession 关键能力

`session.py:316` 实现了以下设计文档未充分展开的能力：

1. **延迟创建私有 NeoChatter**（`session.py:338`）：`_runtime` 属性按需构造 `NeoChatter(self.stream_id, self.plugin)`，仅用于复用 `fetch_unreads / flush_unreads / create_request / inject_usables / run_tool_call`，**不调用其 `execute()`**，避免与驱动方形成循环。
2. **配置不可用回退**（`session.py:352`）：`_cfg()` 在 `NeoChatterConfig` 缺失或类型不匹配时返回全默认 `_ConfigView`，保证第三方插件传错 plugin 也能继续运行。
3. **`pass_and_wait` 前二次拉取未读**（`session.py:782`）：进入 Wait 之前再 `fetch_unreads` 一次，若期间已有新消息直接跳过 Wait，避免消息被 Wait 吞掉。
4. **`actor_round` 通知事件**：`_consume_step_data` 收集本回合使用的工具列表，作为 `Wait/Stop.step_data` 上报，供下游订阅者感知「这一轮做了什么」。
5. **Actor 决策面板**（`session.py:266`）：在控制台以 Rich panel 形式渲染「思考 / 独白 / 工具调用」，便于调试。

## 5. 与设计文档的偏差

### 5.1 设计目标已变更

设计 §14 明确写道：

> NFC 不取代 default_chatter … 子代理协作：**本期不内建**，预留 EventHandler 扩展点。

实际实现**已内建**两个预处理 EventHandler：

- `ProbabilityBypassHandler`（移植自 `default_chatter` 的 `probability_gate`）：基础概率 + 强/弱提及加成 + 未读数加成，命中放行概率即 `STOP` 阻断后续处理器。
- `SubAgentDecisionHandler`：发起一次轻量 LLM 单轮判定（独立 `actor` 任务 + `decision_temperature=0.2`），让模型决定本轮是否值得主 chatter 立即回复；解析策略为「JSON 优先 → 关键词回退（RESPOND/SKIP）→ fail-open 放行」。

这意味着 NDFC 在 v0.1.0 阶段已经不是「更瘦可复用骨架」，而是「带默认预处理策略的中台」。第三方插件仍可订阅 `neo_default_chatter:preprocess` 写自己的处理器，但默认行为已与 `default_chatter` 高度相似。

### 5.2 设计文档需要更新的点

- §3 架构图：缺少 `event_handlers/` 子目录与「两个内置预处理处理器」的描述。
- §4 组件清单：缺少 `ProbabilityBypassHandler` / `SubAgentDecisionHandler` 两行。
- §6 配置：缺少 `enable_cooldown` / `enable_action_suspend` / `preprocess_probability_bypass` / `preprocess_sub_agent`。
- §10 目录结构：缺少 `components/event_handlers/`。
- §11 manifest：实际 `include` 长度为 7，不是 5。
- §14 对比表：NDFC 不再是「本期不内建 sub-agent」，而是「内建 sub_agent + probability_bypass，但以 EventHandler 形式可插拔」。

## 6. utils 模块实现现状

| 模块 | 行数 | 核心职责 | 与设计文档对应章节 |
| --- | --- | --- | --- |
| `utils/preprocess.py` | 187 | 发布 `neo_default_chatter:preprocess` 事件并合并决策 | §5.2 |
| `utils/multimodal.py` | 133 | 提取图片 + 占位符内联，构造多模态 Content 列表 | §7 |
| `utils/prompt_builder.py` | 210 | 渲染 system / user / sub_agent_system / sub_agent_user 四套模板 | §6 / §9 |
| `utils/tool_flow.py` | 316 | 控制流拦截（pass/stop）+ 去重 + SUSPEND 注入 | §9.4 |
| `utils/prompts.py` | 178 | 4 套提示词模板原文（注册到 prompt_manager） | §6 / §9 |

`utils/preprocess.py` 关键设计：

- 事件发布前预填 `proceed / reason / mutations / force_stop_minutes` 四个决策字段为默认值，处理器**只能修改字段值**不能新增/删除 key（EventBus 协议要求 key 集合一致）。
- 异常时 fail-open（`proceed=True, published=False`），避免预处理失败把会话卡死。
- `published` 字段仅在「至少一个处理器改写了任一决策字段」时为 True，用于减少无谓日志噪音。

`utils/tool_flow.py` 关键设计：

- `process_tool_calls` 按模型输出顺序逐条处理，遇到控制流边界前会先把累积的普通调用批量 `flush` 给 `run_tool_call`，保证顺序。
- 跨轮去重通过 `seen_signatures: set[str]` 维护，签名形如 `"<call_name>:<json args>"`，`reason` 字段在签名计算前剥离。
- `append_suspend_payload_if_tool_result_tail`：在 `pass_and_wait` 进入 Wait 前补一条 `ASSISTANT __SUSPEND__`，闭合裸 `TOOL_RESULT` 尾巴，避免下一轮 LLM 把工具回执当成自己的发言续写。

## 7. Service 对外契约（与设计严格一致）

`NeoChatterService.create_session(*, stream_id, plugin=None)`：

- 签名与设计 §13 完全一致；
- `plugin=None` 时回退到 `self.plugin`（NFC 自己的插件实例）；
- 返回的 `ConversationSession` 与 `NeoChatter` 自驱动的 session 行为完全一致；
- 第三方插件复用范式（设计 §13 示例）已验证可行：

```python
service = get_service("neo_default_chatter:service:chat_core")  # 每次新建实例
session = service.create_session(stream_id=self.stream_id, plugin=self.plugin)
resume = None
async for result in session.execute():
    resume = yield result
```

## 8. 当前未完成项

对照设计 §15 的实现路线：

| 路线 | 状态 | 备注 |
| --- | --- | --- |
| 1. 骨架 | ✅ 完成 | 可加载、可空跑 `execute()` 返回 `Wait()` |
| 2. 默认动作 | ✅ 完成 | 3 个 Action 全部实现，`chatter_allow=["neo_default_chatter"]` |
| 3. 预处理事件 | ✅ 完成 | 含 2 个内置处理器 |
| 4. 原生多模态 | ✅ 完成 | `native_multimodal` 开关 + 占位符内联 |
| 5. stop 直接唤醒 | ✅ 完成 | `_apply_stop_wake` 注入到 `Stop` 结果 |
| 6. prompt 模板 | ✅ 完成 | 4 套模板注册到 `prompt_manager` |
| 7. Service 对外契约 | ✅ 完成 | `create_session` 已可被第三方驱动 |
| 8. 单元测试 | ❌ 未完成 | 当前仓库内未发现针对 NDFC 的单测 |

设计 §16「生成前自检」中以下项在实现中已落实：

- ✅ 公开入口使用 `src.app.plugin_system.base/api/types`
- ✅ `@register_plugin` + `plugin_name/description/version`
- ✅ `manifest.name == plugin_name`
- ✅ Service 不依赖单例语义（`get_service` 每次新建）
- ✅ `create_request(with_reminder="actor")` 显式启用 system reminder
- ✅ 相对导入（规范 §2.5）

设计 §16 未覆盖但实现中需关注的项：

- ⚠️ `Stop / Failure` 终态之后未在 chatter 层做最终清理（依赖框架自动处理）。
- ⚠️ `pass_and_wait(seconds=...)` 的定时器恢复依赖框架 `WaitResumeEvent.source == "timer"`，NDFC 自身不维护定时器。
- ⚠️ 表情包图片不进原生多模态的过滤逻辑**未实现**（`utils/multimodal.py:29` 的 `extract_images_from_messages` 会把所有 `type=image` 的媒体都纳入，未区分表情包）—— 与设计 §7.4 的描述不一致，需后续补齐。

## 9. 后续工作建议

按优先级从高到低：

1. **补单元测试**：覆盖 `run_preprocess` 决策合并、`process_tool_calls` 控制流拦截、`inline_images_into_text` 占位符唯一性、`_apply_stop_wake` 概率边界、第三方驱动 `asend` 转发一致性。
2. **表情包图片过滤**：在 `extract_images_from_messages` 增加按 `extra.emoji / media_kind` 过滤，避免表情包图片被无谓塞进 LLM payload（设计 §7.4 明确要求）。
3. **同步更新设计文档**：把 §5.1 列出的偏差反向同步到 `nfc-neo-default-chatter-design.md`，或者把该文档标记为「历史设计稿」、本文档作为「事实现状」的唯一来源。
4. **`enable_cooldown=false` 时的语义验证**：当前实现把 `Stop.time` 直接置 0，未在 chatter 层做特殊处理，需确认框架对 `Stop(time=0)` 的处理是否符合预期。
5. **`actor_round` step_data 消费方落地**：`_consume_step_data` 已上报 `used_tools` 列表，但目前没有内置订阅者；可考虑提供一个默认的 `actor_round` EventHandler 供统计 / 审计使用。
6. **设计 §7.4 占位符引用回写**：模型回复里写 `[图片-1]` 时由 `send_text` 自动改发原图片——本期未实现，作为后续扩展项跟踪。
