# Shameimaru Memory

Shameimaru Memory 是 Neo-MoFox 的四层记忆系统插件，为 Bot 提供长期记忆能力。

四层结构：

- **摘要层（summary）**：周期性从群聊聊天流生成摘要，按群持久化。
- **新闻层（news）**：周期性从群聊摘要中整理出总结性的记忆条目。
- **知识层（knowledge）**：Dreaming 周期将值得长期保留的新闻知识化，写入 `booku_memory_store` 知识库。
- **人物层（persona）**：新闻条目被删除（上限淘汰 / 知识化）时，增量维护人物背景信息。

此外，每次回复前会根据当前对话中出现的人物，将相关新闻与人物背景以 system reminder 的形式注入提示词。

## 目录结构

```text
plugins/shameimaru_memory/
├── __init__.py
├── config.py          # 配置模型
├── event_handler.py   # 回复前记忆注入器（on_prompt_build）
├── job.py             # 三个周期任务：摘要 / 新闻 / Dreaming + 人物层更新
├── manifest.json
├── models.py          # 数据模型（摘要条目 / 新闻条目 / 人物引用 / 群摘要记录）
├── plugin.py          # 插件入口与周期事件注册
├── prompts.py         # 子 agent 提示词模板
├── service.py         # 对外服务（摘要 / 新闻 / 人物 / 知识读取）
├── store.py           # JSON 持久化存储层（共享单例 + 文件监视）
├── sub_agent.py       # 内部子 agent 调用与 JSON 解析
├── tool.py            # read_knowledge 工具
└── utils.py           # 公共工具函数
```

## 工作流程

### 摘要层（summary）

1. 周期事件（默认每 30 分钟）遍历**摘要文件中已注册的群聊**，逐个发起子 agent 调用；
2. 每个群读取最近 `max_messages_per_run` 条消息，格式化为聊天记录交给子 agent 生成摘要；
3. 摘要无实质内容（返回 `NO_MEANINGFUL_CONTENT`）时跳过；
4. 生成的摘要按群追加持久化，条目数超过上限时删除最早的一条。

> 新群聊在发生对话时由回复前注入器自动注册进摘要文件（仅元信息），
> 因此摘要任务只会对真正活跃的群聊发起 LLM 调用，不会扫描数据库中全部（含长期不活跃）群。

### 新闻层（news）

1. 周期事件（默认每 2 小时）遍历全部群摘要，**每个群在独立的上下文窗口中**调用一次子 agent（各群互不混合）；
2. 只消费**未废弃**的摘要条目，子 agent 从这些摘要中整理出值得长期记住的条目（JSON 数组），人物 ID 必须从该群摘要中出现过的人物清单中选择，防止编造；
3. 新条目按时间淘汰最旧的新闻（超出 `max_entries`）；
4. 参与处理的摘要会被**标记为废弃**（而非删除）——新闻层不会再次读取废弃摘要（防止生成重复新闻），但知识层（Dreaming）仍会读取它们了解群聊主题；
5. **子 agent 调用失败时摘要不做任何标记**，等待下轮重试，避免摘要数据丢失；
6. 被淘汰的新闻触发人物层增量更新；
7. 废弃摘要随摘要条目的数量上限（`summary.max_entries_per_group`）按时间淘汰删除。

### 知识层（knowledge / Dreaming）

1. 周期事件（默认每 24 小时）对每个群聊单独执行知识整理；
2. 子 agent 阅读该群摘要（了解主题，**含已被新闻层标记为废弃的摘要**）与当前全部新闻，选择值得长期持久化的知识（身份关系、重要事实、人物偏好等）；
3. 写入 `booku_memory_store`（`bucket="knowledge"`，`memory_type="knowledge"`，记录参与人物、时间与来源），同一响应中重复的新闻 ID 只处理一次；
4. 成功知识化的新闻从新闻库删除，并触发人物层增量更新；
5. 知识库服务不可用时跳过并告警。

### 人物层（persona）

- 新闻条目因达到上限被淘汰、或因被知识化而删除时，按人物分组；
- 将**旧背景文本 + 新内容**一并交给子 agent，由 LLM 融合生成新的背景文本（非机械拼接）；
- 提示词中约束输出总长度，写入前按 `max_text_length` 硬截断兜底；
- 更新失败记录 warning 日志（该次增量信息永久丢失，不做重试）。

### 回复前注入

1. 订阅 `on_prompt_build`，命中配置的模板（默认 `neo_default_chatter_user_prompt`）时执行；
2. 收集当前聊天流 unread message 中出现的人物 ID；
3. 按人物过滤新闻与人物背景，写入流私有 system reminder（`dynamic` 注入到对话尾部，`forever` 消费）；
4. 无相关内容时删除对应 reminder，避免过期内容被继续注入。

## 数据存储

所有数据为本地 JSON 文件，目录由 `storage.data_dir` 配置（默认 `data/shameimaru_memory`）：

| 文件 | 内容 |
|------|------|
| `summaries.json` | 摘要层：按群聊分组的摘要条目（新闻层消费后标记为废弃，供知识层读取，按数量上限淘汰） |
| `news.json` | 新闻层：全局新闻条目列表 |
| `personas.json` | 人物层：`person_id -> 人物背景文本` |

知识层数据存储在 `booku_memory_store`（SQLite + 向量库），不在此目录。

并发与一致性：

- 插件内所有读写共用**同一个 store 单例**，读、改、写均在同一个锁内完成，多个周期任务重叠运行时不会互相覆盖；
- store 后台监视三个 JSON 文件的变更（默认每 2 秒轮询 mtime），外部修改本地文件时自动同步内存缓存。

## 配置项

配置模型：`ShameimaruMemoryConfig`（`plugins/shameimaru_memory/config.py`），
默认配置文件：`config/plugins/shameimaru_memory/config.toml`。

### `[plugin]` 插件设置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用插件 |
| `run_on_start` | bool | `false` | 插件加载后立即执行一次摘要/新闻/Dreaming 任务（会产生 LLM 调用，建议按需开启） |

### `[storage]` 存储配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `data_dir` | str | `"data/shameimaru_memory"` | 记忆数据目录（三个 JSON 数据库均存放于此） |

### `[llm]` 子 agent 模型任务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `summary_task` | str | `"actor"` | 摘要层子 agent 使用的模型任务名 |
| `news_task` | str | `"actor"` | 新闻层子 agent 使用的模型任务名 |
| `knowledge_task` | str | `"actor"` | 知识层子 agent 使用的模型任务名 |
| `persona_task` | str | `"actor"` | 人物层子 agent 使用的模型任务名 |

> 任务名需在 `config/model.toml` 的 `[model_tasks]` 中已配置（示例部署配置使用 `"utils"`）。

### `[summary]` 摘要层配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `interval_minutes` | int | `30` | 摘要生成间隔（分钟） |
| `max_entries_per_group` | int | `50` | 每个群聊的摘要条目数上限，达到上限时删除最早的一条 |
| `max_messages_per_run` | int | `300` | 每次为单个群聊读取的最大消息条数（取最近的消息） |

### `[news]` 新闻层配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `interval_minutes` | int | `120` | 新闻整理间隔（分钟） |
| `max_entries` | int | `50` | 新闻条目总数上限，达到上限时删除时间最早的一条 |
| `max_input_summaries` | int | `100` | 单个群聊单次整理最多读取的摘要条数（按时间取最新的，不含已废弃条目）；参与处理的摘要会在整理后标记为废弃 |

### `[knowledge]` 知识层配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `dream_interval_hours` | int | `24` | Dreaming 知识整理间隔（小时），理论应小于「摘要条目上限 × 摘要更新间隔」 |
| `service_signature` | str | `"booku_memory_store:service:booku_memory_store"` | 知识库服务组件签名 |
| `folder_id` | str | `"default"` | 知识条目写入 booku_memory_store 的文件夹 ID |
| `max_group_summaries_input` | int | `20` | Dreaming 时观察单个群聊的最近摘要条数 |

### `[persona]` 人物层配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_text_length` | int | `2000` | 单个人物背景信息文本的最大长度（提示词限长 + 写入前硬截断） |

### `[injection]` 回复前注入配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt_name` | str | `"neo_default_chatter_user_prompt"` | 注入目标模板名（需与启用的 chatter 模板名一致） |
| `bucket` | str | `"actor"` | system reminder 写入的 bucket 名（需与 chatter 的 `with_reminder` 一致） |
| `inject_news` | bool | `true` | 是否在回复前注入涉及当前人物的新闻记忆 |
| `inject_personas` | bool | `true` | 是否在回复前注入涉及当前人物的人物背景信息 |
| `news_max_inject` | int | `5` | 单次回复最多注入的新闻条数 |
| `persona_max_inject` | int | `5` | 单次回复最多注入的人物背景条数 |
| `person_scan_history_limit` | int | `20` | 收集当前对话人物时扫描的最近历史消息条数（unread 被 flush 进 history 后兜底，避免漏掉当前对话人物） |

### 配置示例

```toml
[plugin]
enabled = true
run_on_start = false

[storage]
data_dir = "data/shameimaru_memory"

[llm]
summary_task = "utils"
news_task = "utils"
knowledge_task = "utils"
persona_task = "utils"

[summary]
interval_minutes = 30
max_entries_per_group = 50
max_messages_per_run = 300

[news]
interval_minutes = 120
max_entries = 50
max_input_summaries = 100

[knowledge]
dream_interval_hours = 24
service_signature = "booku_memory_store:service:booku_memory_store"
folder_id = "default"
max_group_summaries_input = 20

[persona]
max_text_length = 2000

[injection]
prompt_name = "neo_default_chatter_user_prompt"
bucket = "actor"
inject_news = true
inject_personas = true
news_max_inject = 5
persona_max_inject = 5
person_scan_history_limit = 20
```

## 对外组件

### 服务：`shameimaru_memory`

| 方法 | 说明 |
|------|------|
| `get_group_summaries()` | 获取全部群聊摘要（含群元信息与条目列表） |
| `get_news_entries()` | 获取全部新闻条目 |
| `get_persona(person_id)` | 获取指定人物的背景信息 |
| `get_all_personas()` | 获取全部人物背景信息 |
| `read_knowledge(query, top_k)` | 从知识层（booku_memory_store）检索知识 |

### 工具：`read_knowledge`

供 LLM 回忆已持久化的长期知识记忆（身份关系、重要事实、人物偏好等）。

参数：

- `query: str` 检索关键词或问题，如「小梅和小紫是什么关系」
- `top_k: int = 5` 返回条目数上限

返回：

- 成功：`(True, {"ok": True, "query": ..., "total": ..., "items": [...]})`
- 知识库不可用 / 检索失败：`(True, {"ok": False, "error": ...})`

### 事件处理器：`shameimaru_prompt_injector`

订阅 `on_prompt_build`，实现回复前记忆注入（见上文「回复前注入」）。

## 注意事项

- 依赖插件：`booku_memory_store`（知识层服务），未启用时 Dreaming 与 `read_knowledge` 会跳过并告警，其余层不受影响。
- 摘要层按「最近 `max_messages_per_run` 条消息」生成摘要，不做水位去重：低流量群在相邻轮次可能对同一批消息重复摘要，属预期行为（换取实现简单与不遗漏）。
- 人物背景文本有长度上限（`max_text_length`），新闻与摘要条目上限请勿设置过小，以免记忆过早被淘汰。
- 注入模板名与 bucket 必须与当前启用的 chatter 插件一致（默认对应 `neo_default_chatter` 的 `with_reminder="actor"`）。

## 版本

- Plugin: `1.0.0`
- Manifest: `plugins/shameimaru_memory/manifest.json`（`0.1.0`）
- 最低核心版本：`1.2.0`
