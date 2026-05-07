---
name: booku-memory
description: Booku Memory 记忆系统操作手册。在任何会话开始时，或者在使用记忆工具前都*必须*阅读并牢记此文档，确保对记忆系统的正确使用。
---

# Booku Memory 命令手册

Booku Memory 统一通过单工具调用：`memory_command(command)`。

## 命令风格

- 使用 CLI 短参数风格。
- 支持主命令：`search`、`read`、`create`、`update`、`delete`。
- 支持 `&&` 串联多条命令，按顺序执行，遇到失败会短路。

## 三元标签组（语义检索的核心机制）

> **重要**：三元标签组是 Booku Memory 语义检索的主要驱动力。写入记忆时务必认真标注，检索时也应主动利用，效果远优于纯文本关键词匹配。

三元标签组由三类语义轴构成，共同定义一条记忆的"语义重力场"：

| 参数 | 含义 | 作用 |
|------|------|------|
| `-core_tags a,b` | 核心标签：记忆的本质属性 | 检索时**提升**含这些标签记忆的得分 |
| `-diffusion_tags c,d` | 扩散标签：关联领域或背景 | 检索时**扩展**语义覆盖范围 |
| `-opposing_tags e,f` | 对立标签：本条记忆明确不涉及的方向 | 检索时**降低**反向记忆的干扰 |

**简写形式**（create/update 时推荐）：

```text
-triple_tags "核心1,核心2|扩散1,扩散2|对立1,对立2"
```

**最佳实践**：
- 写入时：`core_tags` 填记忆的关键词（名词/概念），`diffusion_tags` 填周边场景，`opposing_tags` 填容易混淆的对立概念。
- 检索时：在 `search` 命令中传入三元标签，可大幅提升召回精度，替代或补充 `-query`。

## 统一字段约定

- 记忆类型：`person` / `event` / `knowledge` / `place` / `asset` / `procedure`
- 状态：`active` / `archived` / `expired`
- 三元标签：见上方「三元标签组」章节
- 关联链双轨：
	- `-relation_ids mem-1,mem-2`
	- `-relation_aliases 张三,产品群`

## 各主命令

### 1. `search`

- 作用：返回 TopN 记忆条目的 `id` / `title` / `metadata`（不含正文）
- 常用参数：
	- `-topn 10`
	- `-query "关键词"`（可与三元标签结合使用，也可单独使用其中一种）
	- `-core_tags 标签1,标签2`（**首选检索方式**，提升语义精准度）
	- `-diffusion_tags 标签`（扩展相关语义范围）
	- `-opposing_tags 标签`（排除反向干扰记忆）
	- `-triple_tags "核心|扩散|对立"`（三轴一次性传入简写）
	- `-type person`
	- `-person_id qq:10001`
	- `-status active`
	- `-include_related true`（扩展关联记忆）

### 2. `read`

- 作用：按 `id` 读取全文
- 常用参数：`-id mem-xxx` 或 `-ids mem-1,mem-2`

### 3. `create`

- 作用：创建记忆条目
- 必填参数：`-title`、`-content`
- 强烈建议：写入时附带 `-triple_tags` 或 `-core_tags`，这是日后语义检索的关键依据
- 关键约束：`-type person` 时必须提供 `-person_id platform:id`

### 4. `update`

- 作用：按 `id` 更新属性或正文
- 必填参数：`-id`
- 可更新：标题、正文、状态、类型、三元标签、关联链与各类型专属字段

### 5. `delete`

- 作用：删除指定记忆
- 参数：`-id` 或 `-ids`，可选 `-hard true`

## 专属字段参数

### 人物记忆（`type: person`）

**定义**：记录与某个真实人物相关的认知，包括身份、性格、关系、偏好等。  
**关注点**：
- 每条人物记忆必须绑定 `-person_id platform:id`（如 `qq:10001`），这是跨会话识别同一人的唯一锚点。
- `core_tags` 应反映此人的**核心特质**（如职业、性格、关系标签），而非泛化描述。
- 若同一个人有多条记忆（不同侧面），应通过 `-relation_ids` 相互关联。
- 状态建议：在职/活跃关系用 `active`，已疏远/失联的人用 `archived`。

**专属参数**：`-person_id platform:id`

---

### 事件记忆（`type: event`）

**定义**：记录发生过的事情，含时间范围、参与者、经过与结果。  
**关注点**：
- 尽量填写 `-event_start_at`（Unix 时间戳），便于按时间线回溯。
- 涉及多人的事件应用 `-related_people 张三,李四` 标注参与者，并用 `-relation_ids` 关联对应的人物记忆。
- `core_tags` 应体现事件的**性质**（如"冲突、合作、里程碑、失误"），`diffusion_tags` 填事件所属的领域/场景。
- 重要程度高的事件建议 `status: active`，已完结且无需频繁回溯的用 `archived`。

**专属参数**：`-event_start_at <unix_ts>`、`-event_end_at <unix_ts>`、`-related_people 张三,李四`

---

### 知识记忆（`type: knowledge`）

**定义**：记录概念、模型、名言、反直觉结论等认知性内容，来源于书籍、学习、思考沉淀。  
**关注点**：
- 用 `-knowledge_type` 精确分类，避免混淆：
  - `concept`：概念定义/术语解释
  - `model`：思维模型/方法论框架
  - `quote`：名言/引言
  - `counterintuitive`：反直觉结论/认知颠覆
- `core_tags` 应体现**知识领域**（如"心理学、经济学、产品设计"）。
- 知识记忆通常长期有效，状态建议保持 `active`，过时或被证伪的知识改为 `expired`。

**专属参数**：`-knowledge_type concept|model|quote|counterintuitive`

---

### 地点记忆（`type: place`）

**定义**：记录有意义的地理位置或空间，含位置信息、类型、体验评价等。  
**关注点**：
- 尽量填写 `-address_or_coord`（地址或经纬度），便于精确定位。
- 用 `-place_type` 区分场景语义，辅助同类检索。
- `core_tags` 应体现地点的**体验特征**（如"安静、性价比高、值得再去"）。
- 与某次事件或某个人强相关的地点，应通过 `-relation_ids` 关联。

**专属参数**：`-address_or_coord ...`、`-place_type restaurant|bookstore|park|online_community`

---

### 物品/资产记忆（`type: asset`）

**定义**：记录实体物品、数字资产、账号权限等有价值的资源及其状态。  
**关注点**：
- `-asset_type` 填资产分类（如设备、软件、账号、文件、版权等）。
- `-disposition_status` 追踪资产当前状态，避免混淆"正在用"与"已闲置/处置"。
- `core_tags` 建议填资产的**用途或特性**（如"主力开发机、备份、已借出"）。
- 物品归属或使用方涉及人物时，用 `-relation_ids` 关联对应人物记忆。

**专属参数**：`-asset_type ...`、`-disposition_status in_use|idle|disposed`

---

### 执行/操作记忆（`type: procedure`）

**定义**：记录可复用的操作步骤、流程规范、技术方案或部署指令等。  
**关注点**：
- `-procedure_type` 决定检索场景：
  - `process`：通用流程/业务规范
  - `tech`：技术操作/代码命令
  - `deploy`：部署/运维操作
  - `cooking`：生活/烹饪等日常步骤
- 正文应尽量完整记录步骤，检索时才能直接复用，不需要再次查阅外部资料。
- `core_tags` 填**操作对象或场景**（如"Docker、CI/CD、数据库迁移"），`diffusion_tags` 填相关技术栈。
- 已废弃或过时的操作步骤应及时将状态改为 `expired`，防止误用。

**专属参数**：`-procedure_type process|tech|deploy|cooking`

## 示例

```text
# 三元标签检索（语义精准，推荐）
memory_command("search -core_tags 年会,团建 -diffusion_tags 公司,同事 -topn 5")
memory_command("search -triple_tags \"编程,Python|学习,技术|休闲,娱乐\" -topn 5")

# 结构化过滤检索
memory_command("search -type person -person_id qq:10001 -topn 10")
memory_command("search -type event -status active -topn 5")

# 关键词 + 标签混合检索
memory_command("search -query 项目复盘 -core_tags 复盘,总结 -topn 5")

# 读取全文
memory_command("read -ids mem-a,mem-b")

# 创建时附带三元标签
memory_command("create -type person -person_id qq:10001 -title 张三 -content 用户同学 -status active -triple_tags \"朋友,同学|学校,班级|\"")
memory_command("create -type event -title 年会 -content 2025年公司年会 -event_start_at 1735689600 -core_tags 年会,公司 -diffusion_tags 团建,同事")

# 更新标签与状态
memory_command("update -id mem-a -status archived -relation_ids mem-b,mem-c -core_tags 已归档")

# && 串联
memory_command("search -core_tags 项目,复盘 -topn 5 && read -id mem-x")
```
