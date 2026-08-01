"""Booku Memory 命令工具帮助手册（字符串常量）。"""

BOOKU_MEMORY_COMMAND_MANUAL = """# Booku 记忆工具使用说明


## 什么是记忆

记忆是一个分语义桶的伪长期记忆，你可以通过该工具来记录、回忆、更新信息。
这可以帮助你长期记住所有人的信息，并维系良好的关系。

该工具通过命令式调用，支持多重记忆存储桶和标签系统，允许构建语义化的知识图谱。
每次 AI 发送的信息中都必须以行动的形式自动调用该工具，比如"（调用记忆工具……中）"。

## 基础命令

- `help` — 显示记忆工具的使用说明。
- `search` — 搜索记忆。
- `read` — 读取记忆全文。
- `create` — 创建新的记忆条目。
- `update` — 更新已有的记忆条目。
- `delete` — 删除记忆条目。

你可以使用 `&&` 串联多个命令，例如：`search -query 张三 && create -title 张三 -content ...`。

## 1. help 命令

```
help
```

显示本使用说明。

## 2. search 命令

```
search [-query <关键词>] [-topn <N>] [-type <类型>] [-status <状态>] [-person_id <ID>]
       [-include_archived true/false] [-include_knowledge true/false]
       [-core_tags <标签>] [-diffusion_tags <标签>] [-opposing_tags <标签>]
       [-triple_tags "核心|扩散|对立"]
```

搜索记忆。至少需要提供 `-query` 或标签三件套之一。

### 参数

- `-query` / `-q` — 搜索关键词（可选，但推荐提供）。
- `-topn` / `-top_n` / `-n` — 最大返回条数（默认 10）。
- `-type` / `-memory_type` — 记忆类型过滤：person / event / knowledge / place / asset / procedure。
- `-status` — 状态过滤：active / archived / expired。
- `-person_id` — 人物 ID 过滤（格式 platform:id）。
- `-include_archived` — 是否包含归档记忆（默认 false）。
- `-include_knowledge` — 是否包含知识库（默认 true）。
- `-core_tags` / `-core` — 核心标签，逗号分隔。
- `-diffusion_tags` / `-diffusion` — 扩散标签，逗号分隔。
- `-opposing_tags` / `-opposing` — 对立标签，逗号分隔。
- `-triple_tags` / `-triplet` — 一次性传入三组标签，格式：`"核心|扩散|对立"`。

标签三件套必须同时提供或同时缺省，不能只传一组或两组。

### 示例

```
search -query 张三的生日
search -type person -core_tags 朋友 -diffusion_tags 同事 -opposing_tags 陌生人
search -query 会议 -triple_tags "工作,项目|讨论,决策|休息,摸鱼"
```

## 3. read 命令

```
read -id <记忆ID> [-id <记忆ID2> ...]
```

按 ID 读取一条或多条记忆的完整内容。

### 参数

- `-id` / `-ids` / `-memory_id` / `-memory_ids` — 要读取的记忆 ID，支持逗号分隔或多个 -id。

### 示例

```
read -id mem-abc123
read -ids mem-abc123,mem-def456
```

## 4. create 命令

```
create -title <标题> -content <内容>
       [-type <记忆类型>] [-status <状态>]
       [-person_id <人物ID>]
       -core_tags <标签> -diffusion_tags <标签> -opposing_tags <标签>
       [-triple_tags "核心|扩散|对立"]
       [-relation_ids <ID列表>] [-relation_aliases <别名列表>]
       [-related_people <人物列表>]
```

创建一条新记忆。`-title`、`-content` 和标签三件套为必填项。
写入时会自动检查重复，若与已有记忆高度相似则自动合并。

### 参数

- `-title` — 记忆标题（必填）。
- `-content` / `-body` — 记忆正文（必填）。
- `-type` / `-memory_type` — 记忆类型：person / event / knowledge / place / asset / procedure。
- `-status` — 状态：active / archived / expired（默认 active）。
- `-person_id` — 人物记忆专用，格式 platform:id。类型为 person 时必填。
- `-core_tags` / `-core` — 核心标签（必填，逗号分隔）。
- `-diffusion_tags` / `-diffusion` — 扩散标签（必填，逗号分隔）。
- `-opposing_tags` / `-opposing` — 对立标签（必填，逗号分隔）。
- `-triple_tags` / `-triplet` — 一次性传入三组，格式：`"核心|扩散|对立"`。
- `-relation_ids` / `-relation_memory_ids` — 关联记忆 ID，逗号分隔。
- `-relation_aliases` — 关联别名，逗号分隔。
- `-related_people` — 相关人物名，逗号分隔。
- `-event_start_at` / `-start_at` — 事件开始时间戳。
- `-event_end_at` / `-end_at` — 事件结束时间戳。
- `-knowledge_type` — 知识类型：concept / model / quote / counterintuitive。
- `-address_or_coord` / `-address` — 地点地址或坐标。
- `-place_type` — 地点类型。
- `-asset_type` — 物品类型。
- `-disposition_status` — 处置状态：in_use / idle / disposed。
- `-procedure_type` — 流程类别。

### 示例

```
create -title 张三的生日 -content 张三的生日是1990年1月15日 -core_tags 个人信息 -diffusion_tags 社交 -opposing_tags 工作
create -title 会议纪要 -content 讨论了年度计划 -type event -triple_tags "工作,规划|讨论|琐事"
create -title 张三 -content 张三，男，30岁... -type person -person_id qq:123456 -triple_tags "朋友|同事|陌生人"
```

## 5. update 命令

```
update -id <记忆ID>
       [-title <新标题>] [-content <新内容>]
       [-core_tags <新标签>] [-diffusion_tags <新标签>] [-opposing_tags <新标签>]
       [-triple_tags "核心|扩散|对立"]
       ...
```

按记忆 ID 更新一条或多条已有记忆。只更新传入的字段，其余保持不变。
标签参数一旦出现就必须同时提供完整的三件套。

### 参数

所有参数与 create 相同，但都不是必填项。只有 -id 是必填的。

### 示例

```
update -id mem-abc123 -content 张三的生日是1990年1月15日，今年34岁
update -id mem-abc123 -triple_tags "挚友,密友|同事,熟人|陌生人"
```

## 6. delete 命令

```
delete -id <记忆ID> [-id <记忆ID2> ...] [-hard true/false]
```

按 ID 删除一条或多条记忆。默认软删除（可恢复），使用 -hard true 可永久删除。

### 参数

- `-id` / `-ids` / `-memory_id` / `-memory_ids` — 要删除的记忆 ID，支持逗号分隔或多个 -id。
- `-hard` — 是否硬删除（默认 false，即软删除）。

### 示例

```
delete -id mem-abc123
delete -ids mem-abc123,mem-def456 -hard true
```

## 标签系统说明

每条记忆有三个维度的标签：
- **核心标签（core_tags）**：定义记忆的核心语义，检索时权重最高。
- **扩散标签（diffusion_tags）**：扇形扩展语义，帮助发现相关但不直接匹配的记忆。
- **对立标签（opposing_tags）**：定义与记忆对立的语义，用于去噪和过滤。

三组标签共同形成一个语义三角，用于 EPA 向量重塑检索。提供标签时三组必须齐全。"""

BOOKU_TEMPORARY_MEMO_MANUAL = """# 临时备忘录使用说明

临时备忘录是一个短期记忆工具，用于记录当前聊天流的关键信息。
与长期记忆不同，备忘录会自动过期，过期时间默认 2 小时。

## 何时使用

- 有人在对话中透露了偏好、需求、计划等信息
- 你需要在跨聊天流保持重要信息的连续性
- 需要临时记住某个决策或约定

## 参数

- `content` — 要记录的内容（必填）
- `expire_hours` — 过期间隔（小时），默认 2.0
"""
