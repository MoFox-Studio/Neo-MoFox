"""Booku Memory 共享命令手册文本。"""

BOOKU_MEMORY_COMMAND_MANUAL: str = """# Booku Memory 命令手册

Booku Memory 当前提供两个工具：

- `memory_command(command)`：长期记忆工具，用于检索、读取、创建、更新、删除长期记忆。
- `temporary_memo(content, expire_hours=2.0)`：临时备忘录工具，不是长期记忆，只用于记录短期关键便签，并会自动过期。

## 工具边界

- `memory_command` 适合沉淀长期可复用的记忆。
- `temporary_memo` 适合跨群并行聊天、短时间任务协作等场景下的短期提醒。
- 不要把临时便签当作长期记忆；需要长期保留的信息应写入 `memory_command`。

## temporary_memo

- 参数：
  - `content`：要记录的短期关键内容。
  - `expire_hours`：相对过期时间，单位小时，默认 `2.0`。
- 特性：
  - 不进入长期记忆检索。
  - 会通过 system reminder 进行短期播报。
  - 会自动过期。

## memory_command 风格

- 使用 CLI 风格参数。
- 支持主命令：`help`、`search`、`read`、`create`、`update`、`delete`
- 支持 `&&` 串联多条命令，按顺序执行，遇到失败会短路。

## 三元标签组

> 重要：三元标签组是 `memory_command` 语义检索的关键驱动力。

只要使用标签参数，就必须同时提供完整且非空的三组：

- `-core_tags`
- `-diffusion_tags`
- `-opposing_tags`

也可以使用简写：

```text
-triple_tags "核心1,核心2|扩散1,扩散2|对立1,对立2"
```

## 字段约定

- 记忆类型：`person` / `event` / `knowledge` / `place` / `asset` / `procedure`
- 状态：`active` / `archived` / `expired`

## 主命令

### 1. `search`

- 作用：返回 TopN 记忆条目的 `id` / `title` / `metadata`
- 常用参数：
  - `-topn 10`
  - `-query "关键词"`
  - `-core_tags ...`
  - `-diffusion_tags ...`
  - `-opposing_tags ...`
  - `-triple_tags "核心|扩散|对立"`
  - `-type person`
  - `-person_id qq:10001`
  - `-status active`
  - `-include_related true`

### 2. `read`

- 作用：按 `id` 读取全文
- 常用参数：`-id mem-xxx` 或 `-ids mem-1,mem-2`

### 3. `create`

- 作用：创建长期记忆条目
- 必填参数：`-title`、`-content`
- 必填约束：必须同时提供完整三元标签组
- 关键约束：`-type person` 时必须提供 `-person_id platform:id`

### 4. `update`

- 作用：按 `id` 更新长期记忆
- 必填参数：`-id`
- 若更新标签，必须整组三元标签一起传

### 5. `delete`

- 作用：删除长期记忆
- 参数：`-id` 或 `-ids`
- 可选：`-hard true`

## 类型专属字段

- `person`：`-person_id platform:id`
- `event`：`-event_start_at`、`-event_end_at`、`-related_people`
- `knowledge`：`-knowledge_type concept|model|quote|counterintuitive`
- `place`：`-address_or_coord`、`-place_type`
- `asset`：`-asset_type`、`-disposition_status`
- `procedure`：`-procedure_type process|tech|deploy|cooking`

## 示例

```text
# 临时备忘录
temporary_memo(content="A 群正在讨论发布窗口，等 B 群确认后统一回复")
temporary_memo(content="今晚 10 点前记得回收跨群结论", expire_hours=4.0)

# 语义检索
memory_command("search -core_tags 年会,团建 -diffusion_tags 公司,同事 -opposing_tags 请假,缺席 -topn 5")

# 读取全文
memory_command("read -ids mem-a,mem-b")

# 创建长期记忆
memory_command("create -type person -person_id qq:10001 -title 张三 -content 用户同学 -triple_tags \"朋友,同学|学校,班级|陌生人,路人\"")

# 更新长期记忆
memory_command("update -id mem-a -status archived -core_tags 已归档 -diffusion_tags 历史,存档 -opposing_tags 活跃,当前")

# 串联
memory_command("search -core_tags 项目,复盘 -diffusion_tags 会议,团队 -opposing_tags 闲聊,跑题 -topn 5 && read -id mem-x")
```
"""


__all__ = ["BOOKU_MEMORY_COMMAND_MANUAL"]
