# Neo Emoji Sender

> 设计文档：[docs/plan/neo-emoji-sender-redesign.md](../../docs/plan/neo-emoji-sender-redesign.md)

新版表情包领域插件，替代 `plugins/emoji_sender/`。保留原有 direct / picker 两种发送模式，但把“抽取、标注、存储、索引、检索、发送”拆成独立层次。

## 目标

- 独立数据目录：图片、SQLite 元数据、Chroma 向量库互不混杂。
- `EmojiService` 作为唯一读写入口，统一注册、获取、更新、删除、检索。
- 注册来源可插拔：手动目录、主库 `Images` 媒体来源。
- 事件驱动向量化：注册 / 更新 / 删除 / 检索各发布一个事件。
- 严格事件链检索：服务不直接访问向量库，也不做数据库回退。
- 同步索引 + 失败不阻断 + 定时对账：无 `vector_status` 状态机。

## 目录

```text
plugins/neo_emoji_sender/
├── manifest.json
├── plugin.py         # 插件入口、模式选择、调度注册、reminder 注入
├── config.py         # 配置节：plugin / prompt / storage / vector / sources / reconcile / picker / dedup
├── manager/          # runtime、service、repository、store、vector_index、reconciler、events、image_utils
├── sources/          # EmojiSource 基类与两个来源
├── event_handlers/   # EmojiVectorizer
├── actions/          # direct / picker 动作
├── tools/            # picker 检索工具
└── migrations/       # 从旧 emoji_sender 迁移
```

## 数据布局

```text
data/neo_emoji_sender/
├── memes/           # <sha256>.<ext>
├── emoji.db         # records / tags / suppressions
├── vector/          # Chroma
├── tmp/             # 原子写入
├── manual_memes/    # 手动目录来源默认扫描位置
└── index_meta.json  # embedding 模型指纹
```

## 组件

| 组件 | 类型 | 启用条件 |
|---|---|---|
| `emoji` | service | 始终 |
| `emoji_vectorizer` | event_handler | 始终 |
| `send_emoji_meme` | action | direct 模式 |
| `search_emoji_memes` | tool | picker 模式 |
| `send_emoji_meme_by_id` | action | picker 模式 |
| `delete_emoji_meme_by_id` | action | picker 模式 |

`manifest.json` 的 `include` 声明全部组件；实际注册集合由 `get_components()` 按 `plugin.interaction_mode` 决定。

## 核心事件

| 事件 | 用途 |
|---|---|
| `neo_emoji_sender:emoji_registered` | 新表情写入向量索引 |
| `neo_emoji_sender:emoji_updated` | 描述或标签变化后重建向量 |
| `neo_emoji_sender:emoji_deleted` | 删除对应向量 |
| `neo_emoji_sender:emoji_search` | 回填向量检索结果 |

## 兼容性

- Action / Tool 名称保持不变：`send_emoji_meme`、`send_emoji_meme_by_id`、`delete_emoji_meme_by_id`、`search_emoji_memes`。
- 配置项沿用旧插件命名：`interaction_mode`、`inject_system_prompt`、`picker.page_size`、`dedup.*`、`vector.*`。
- 旧插件与新插件不能同时启用（Action 同名）。

## 迁移

```bash
# 预览
python -m plugins.neo_emoji_sender.migrations.migrate_emoji_sender --dry-run

# 正式迁移（向量交由启动后的对账补齐）
python -m plugins.neo_emoji_sender.migrations.migrate_emoji_sender

# 迁移后立即重建向量（需要可用的 embedding 任务模型）
python -m plugins.neo_emoji_sender.migrations.migrate_emoji_sender --vector
```

迁移只读取旧数据，不修改旧目录；脚本可重复执行，已存在的记录会被跳过。迁移完成后在旧插件配置中禁用：

```toml
[plugin]
enabled = false
```
