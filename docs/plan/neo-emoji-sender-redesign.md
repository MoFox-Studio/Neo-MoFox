# Neo Emoji Sender 重构设计

> 状态：设计定稿，待实现
> 目标插件：`plugins/neo_emoji_sender/`
> 替代插件：`plugins/emoji_sender/`（保留目录，默认禁用）

本文描述新版表情包插件的目标、架构、数据模型、事件契约、向量索引、对账机制与迁移方案。

---

## 1. 背景

现有 `emoji_sender` 已经具备完整能力：

- 定时从 `data/media_cache/emojis` 或手动目录抽取表情包
- 调用 VLM 按人设决定是否收藏，并生成描述与情感标签
- 复制文件到插件目录，把描述 embedding 写入插件自有 Chroma
- 提供 direct / picker 两种发送模式
- 支持按完整 `meme_id` 删除

但当前实现存在几个结构性问题：

| 问题 | 说明 |
|---|---|
| Service 非单例 | `get_service()` 每次创建新实例，`_usage_history` 等实例级状态实际失效 |
| 路径耦合 | 向量 metadata 保存绝对路径，文件移动或数据目录迁移后索引即失效 |
| 状态只在内存 | VLM 拒收、用户删除集合重启即丢失，同一张图会被反复评估/重新收录 |
| 无元数据库 | 描述、标签、来源、时间等信息全部散落在向量 metadata，查询与编辑都依赖扫向量库 |
| 入库与向量耦合 | 文件复制、VLM、embedding、向量写入挤在同一条流程里，难以单独维护索引 |
| 删除逻辑复杂 | 需要扫全量向量库、交叉校验记录身份；在 DB 成为事实源后可以大幅简化 |

新版把插件从“表情包发送器”升级为“表情包领域服务”，并保留两种发送模式的行为兼容。

---

## 2. 目标与非目标

### 2.1 目标

1. 独立数据目录：图片、SQLite 元数据、向量库、迁移脚本互不混杂。
2. `EmojiService` 成为唯一读写入口：注册、获取、更新、删除、检索都走服务。
3. 注册来源可插拔：`EmojiSource` 基类统一“定时 + 入库编排”，子类只提供候选。
4. 事件驱动向量化：注册/更新/删除/检索各发一个事件，向量化组件订阅处理。
5. 严格事件链检索：服务不直接访问向量库，也不做数据库回退。
6. 同步等待 + 失败不阻断 + 定时对账：正常路径立即可搜，异常由 revision 对账修复。
7. 两种发送模式保持兼容：组件名、参数、文案、提示词注入不变。

### 2.2 非目标

- 本期不做管理端 UI / Router（`neo_emoji_sender_admin` 仅保留目录）。
- 不引入新的向量数据库后端，继续使用 Chroma。
- 不改变情感标签预设与 VLM 标注口径。
- 不改变媒体管理器本身的识别流程。

---

## 3. 关键决策

| 决策点 | 结论 |
|---|---|
| 插件落地 | 新建 `neo_emoji_sender`，旧 `emoji_sender` 保留并禁用 |
| 检索实现 | 严格事件链：只消费 `emoji_search` 事件回填的结果 |
| 一致性 | 同步 await 事件、失败不阻断、定时对账修复 |
| 媒体来源 | 查询主库 `Images` 表（`type='emoji'`） |
| 向量粒度 | 沿用现状：每个 `(emoji_id, tag)` 一条向量 |
| 索引状态 | 不引入 `vector_status` / 软删墓碑，使用 `revision` 对账 |
| 管理端 | 本期不做，只保证服务接口可复用 |

---

## 4. 总体架构

### 4.1 分层

```text
┌─────────────────────────────────────────────────────┐
│ Action / Tool                                       │
│  send_emoji_meme / search_emoji_memes / ...         │
└───────────────────────┬─────────────────────────────┘
                        │ 调用
┌───────────────────────▼─────────────────────────────┐
│ EmojiService（BaseService，无状态门面）              │
│  register / get / list / update / delete / search    │
└───────┬───────────────────────────────┬─────────────┘
        │ 读写                          │ 发布事件
┌───────▼─────────────┐         ┌───────▼─────────────┐
│ EmojiRuntime        │         │ EventBus             │
│  Repository(SQLite) │         │  emoji_registered    │
│  Store(files)       │         │  emoji_updated       │
│  VectorIndex(Chroma)│         │  emoji_deleted       │
│  UsageHistory       │         │  emoji_search        │
└───────▲─────────────┘         └───────┬─────────────┘
        │ 操作                          │ 订阅
┌───────┴─────────────┐         ┌───────▼─────────────┐
│ EmojiSource          │         │ EmojiVectorizer      │
│  manual_dir          │         │  (BaseEventHandler)  │
│  media_cache         │         └───────┬─────────────┘
└─────────────────────┘                 │
                                ┌───────▼─────────────┐
                                │ EmojiVectorIndex     │
                                └─────────────────────┘
```

依赖方向单向：Action/Tool/Source → Service → EventBus → Vectorizer → VectorIndex。Service 不 import Vectorizer，只认识事件名。

### 4.2 目录结构

```text
plugins/neo_emoji_sender/
├── manifest.json
├── plugin.py
├── config.py
├── __init__.py
├── manager/
│   ├── __init__.py
│   ├── runtime.py          # EmojiRuntime：跨 Service 实例共享的运行时
│   ├── service.py          # EmojiService（BaseService）
│   ├── models.py           # SQLAlchemy 模型
│   ├── repository.py       # SQLite 仓储
│   ├── store.py            # 图片文件存取
│   ├── vector_index.py     # Chroma + embedding
│   ├── reconciler.py       # DB ↔ 文件 ↔ 向量对账
│   └── events.py           # 事件名与 payload 模板
├── sources/
│   ├── __init__.py
│   ├── base.py             # EmojiSource 基类
│   ├── manual_dir.py       # 手动目录来源
│   └── media_cache.py      # 主库 Images 来源
├── event_handlers/
│   ├── __init__.py
│   └── vectorizer.py       # EmojiVectorizer
├── actions/
│   ├── __init__.py
│   └── send_emoji.py       # direct / picker 动作
├── tools/
│   ├── __init__.py
│   └── search_emoji.py     # picker 检索工具
└── migrations/
    └── __init__.py

scripts/
└── migrate_neo_emoji_sender.py   # CLI 外壳，复用 manager/legacy_migration.py
```

### 4.3 数据目录

```text
data/neo_emoji_sender/
├── memes/          # <sha256>.<ext>
├── emoji.db        # SQLite：records / tags / suppressions
├── vector/         # Chroma 持久化目录
├── tmp/            # 原子写入临时文件
└── state.json      # embedding 指纹与迁移标记
```

---

## 5. 数据模型

### 5.1 emoji_records

| 字段 | 类型 | 说明 |
|---|---|---|
| `emoji_id` | TEXT PK | 文件内容 sha256，64 位十六进制 |
| `rel_path` | TEXT NOT NULL | 相对数据根目录的路径，如 `memes/<hash>.png` |
| `mime` | TEXT NOT NULL | `image/png` / `image/jpeg` / `image/gif` / `image/webp` |
| `size_bytes` | INTEGER NOT NULL | 原始文件大小 |
| `description` | TEXT NOT NULL | VLM 生成的描述 |
| `revision` | TEXT NOT NULL | 描述 + 标签的内容指纹，见 5.3 |
| `source_kind` | TEXT NOT NULL | `manual_dir` / `media_cache` |
| `source_ref` | TEXT NOT NULL DEFAULT '' | 原文件路径或主库 `image_id` |
| `created_at` | REAL NOT NULL | |
| `updated_at` | REAL NOT NULL | |

索引建议：

- `idx_emoji_records_updated_at(updated_at)`
- `idx_emoji_records_source(source_kind, source_ref)`

### 5.2 emoji_tags

| 字段 | 类型 | 说明 |
|---|---|---|
| `emoji_id` | TEXT NOT NULL | 关联 `emoji_records.emoji_id` |
| `tag` | TEXT NOT NULL | 情感标签 |

- 联合主键 `(emoji_id, tag)`
- 索引 `idx_emoji_tags_tag(tag)`
- 注册/更新时与主表同事务重建

### 5.3 revision

`revision` 是内容指纹，不是索引状态：

```python
def compute_revision(description: str, tags: list[str]) -> str:
    payload = description + "\x00" + "\n".join(sorted(set(tags)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

它覆盖所有会影响 embedding 语义的内容：

- 描述变化 → revision 变化
- 标签集合变化 → revision 变化
- 路径、来源、时间变化 → revision 不变

### 5.4 emoji_suppressions

| 字段 | 类型 | 说明 |
|---|---|---|
| `content_hash` | TEXT PK | 文件内容 sha256 |
| `reason` | TEXT NOT NULL | `vlm_rejected` / `user_deleted` |
| `created_at` | REAL NOT NULL | |
| `note` | TEXT NOT NULL DEFAULT '' | 可选说明 |

用途：

- `vlm_rejected`：VLM 明确判定不收藏，避免每轮重复评估
- `user_deleted`：用户主动删除，避免源文件仍在时被重新收录

`register(force=True)` 可绕过或清除对应抑制记录。

### 5.5 为什么没有 vector_status

本设计明确**不引入** `vector_status` 或软删墓碑。索引漂移全部由对账推导：

| 漂移 | 检出方式 |
|---|---|
| 注册后没索引 | 该 `emoji_id` 在向量库无记录 |
| 部分 tag 没写进去 | 向量 tag 集合 ≠ DB tag 集合 |
| 更新后没重建 | 向量 `revision` ≠ DB `revision` |
| 删除后向量残留 | 向量 `emoji_id` 不在 DB |
| 文件被手动删除 | DB 记录找不到文件 |
| 文件是崩溃残留 | 文件不在 DB |

对账算法见第 10 节。

---

## 6. 服务接口

### 6.1 非单例约束

框架的 `service_api.get_service()` 每次都会创建新的 Service 实例。因此：

- `EmojiService` 必须是无状态门面
- 所有共享状态放在 `plugin.runtime`（`EmojiRuntime`）
- 不能把锁、缓存、DB 连接挂在 Service 实例字段上

```python
class EmojiService(BaseService):
    name = "emoji"
    description = "表情包注册、检索、更新与删除服务"

    @property
    def runtime(self) -> EmojiRuntime:
        return self.plugin.runtime
```

服务签名为 `neo_emoji_sender:service:emoji`。

### 6.2 接口

```python
class EmojiService(BaseService):
    async def register(
        self,
        *,
        data: bytes,
        mime: str,
        description: str,
        tags: list[str],
        source_kind: str,
        source_ref: str = "",
        force: bool = False,
    ) -> EmojiRecord | None: ...

    async def get(self, emoji_id: str) -> EmojiRecord | None: ...

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        tag: str | None = None,
        source_kind: str | None = None,
    ) -> list[EmojiRecord]: ...

    async def update(
        self,
        emoji_id: str,
        *,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> EmojiRecord | None: ...

    async def delete(
        self,
        emoji_id: str,
        *,
        reason: str = "user_deleted",
    ) -> bool: ...

    async def search(
        self,
        query: str,
        *,
        tags: list[str] | None = None,
        limit: int = 8,
        exclude_ids: list[str] | None = None,
    ) -> list[EmojiHit]: ...
```

### 6.3 领域对象

```python
@dataclass(frozen=True, slots=True)
class EmojiRecord:
    emoji_id: str
    rel_path: str
    mime: str
    size_bytes: int
    description: str
    revision: str
    tags: tuple[str, ...]
    source_kind: str
    source_ref: str
    created_at: float
    updated_at: float

@dataclass(frozen=True, slots=True)
class EmojiHit:
    record: EmojiRecord
    tag: str
    distance: float
```

### 6.4 选择策略

`search` 只负责“检索 + 回表”，阈值、温度采样、历史去重、分页属于服务的选择策略：

- `search_best`：过滤 `distance <= max_distance`；无满足项且提供了标签时回退全量候选，再按温度采样
- `search_candidates`：去重后按聊天流历史过滤，再分页
- `send_best_detailed` / `send_by_id`：读取文件并调用 `send_api.send_emoji`

`processed_plain_text` 保持现有格式：`[表情包:标签:描述]` 或 `[表情包:标签]`。

---

## 7. 事件契约

### 7.1 事件名

统一前缀 `neo_emoji_sender:`：

| 事件 | 触发时机 |
|---|---|
| `neo_emoji_sender:emoji_registered` | 新表情注册成功 |
| `neo_emoji_sender:emoji_updated` | 描述或标签更新 |
| `neo_emoji_sender:emoji_deleted` | 表情被删除 |
| `neo_emoji_sender:emoji_search` | 服务执行检索 |

### 7.2 payload 模板

EventBus 要求同一事件链的 key 集合固定，因此统一使用模板：

```python
REGISTERED_PAYLOAD = {
    "emoji_id": "",
    "description": "",
    "tags": [],
    "revision": "",
    "source_kind": "",
    "ok": False,
    "error": "",
}

UPDATED_PAYLOAD = {
    "emoji_id": "",
    "description": "",
    "tags": [],
    "revision": "",
    "source_kind": "",
    "ok": False,
    "error": "",
}

DELETED_PAYLOAD = {
    "emoji_id": "",
    "ok": False,
    "error": "",
}

SEARCH_PAYLOAD = {
    "query": "",
    "tags": [],
    "limit": 8,
    "exclude_ids": [],
    "results": [],
    "ok": False,
    "error": "",
}
```

`results` 元素：

```python
{
    "emoji_id": "…",
    "tag": "开心",
    "distance": 0.123,
}
```

### 7.3 EmojiVectorizer

```python
class EmojiVectorizer(BaseEventHandler):
    name = "emoji_vectorizer"
    description = "订阅表情包领域事件，维护向量索引并响应检索"
    weight = 100
    timeout = 0
    init_subscribe = [
        "neo_emoji_sender:emoji_registered",
        "neo_emoji_sender:emoji_updated",
        "neo_emoji_sender:emoji_deleted",
        "neo_emoji_sender:emoji_search",
    ]
```

约束：

- `timeout = 0`：embedding 是网络调用，避免被默认 30 秒超时截断
- `execute` 内部必须完整捕获异常，写入 `ok=False / error` 后返回 `SUCCESS`
- 不能把异常抛给 EventBus，否则会被转成 `PASS`，参数变更丢失
- 检索事件使用高权重，保证先于其它订阅者执行

### 7.4 订阅时序

事件订阅映射在 `ON_ALL_PLUGIN_LOADED` 时构建。因此：

- `on_plugin_loaded` 中**不要**执行依赖事件链的逻辑
- 首次入库、首次对账统一走 scheduler 的 `delay_seconds=0` 任务
- scheduler 在插件加载完成后才启动，任务执行时订阅已就绪

### 7.5 严格事件链

`EmojiService.search` 不直接访问向量库，也不做数据库回退：

```text
service.search → publish emoji_search → vectorizer 回填 results → service 回表 → 返回
```

这意味着：

- 向量化组件必须常驻，不能做成可关闭组件
- 向量化失败或无订阅者时，检索返回空列表
- 检索结果仍以 DB 为准：向量返回的 `emoji_id` 不存在、文件不存在都会被跳过

---

## 8. 向量索引

### 8.1 记录粒度

沿用现有 `emoji_sender`：

- 每个 `(emoji_id, tag)` 一条向量
- 同一表情的所有 tag 共用同一份 description embedding
- 向量 ID：`f"{emoji_id}:{tag}"`

### 8.2 metadata

```python
{
    "emoji_id": emoji_id,
    "tag": tag,
    "description": description,   # 仅调试，服务不信任
    "revision": revision,
    "updated_at": updated_at,
}
```

**不保存 path**。发送时从 DB 读取 `rel_path`，避免文件移动导致索引失效。

### 8.3 upsert

```python
async def upsert(
    self,
    *,
    emoji_id: str,
    description: str,
    tags: list[str],
    revision: str,
    updated_at: float,
) -> bool:
    embedding = await self.embed(description)
    if embedding is None:
        return False

    await self.vdb.delete(
        collection_name=self.collection,
        where={"emoji_id": emoji_id},
    )

    ids = [f"{emoji_id}:{tag}" for tag in tags]
    await self.vdb.add(
        collection_name=self.collection,
        ids=ids,
        embeddings=[embedding for _ in tags],
        documents=[description for _ in tags],
        metadatas=[
            {
                "emoji_id": emoji_id,
                "tag": tag,
                "description": description,
                "revision": revision,
                "updated_at": float(updated_at),
            }
            for tag in tags
        ],
    )
    return True
```

说明：

- 先删后加用于处理标签变更；Chroma 包装层没有 `upsert`
- delete 与 add 之间存在短暂空窗，更新期间可能搜不到该表情，可接受
- 若将来需要消除空窗，可在 `src/kernel/vector_db` 增加 `upsert`

### 8.4 delete

```python
async def delete(self, emoji_id: str) -> bool:
    await self.vdb.delete(
        collection_name=self.collection,
        where={"emoji_id": emoji_id},
    )
    data = await self.vdb.get(
        collection_name=self.collection,
        where={"emoji_id": emoji_id},
        limit=1,
        include=["metadatas"],
    )
    return not bool(data.get("ids"))
```

### 8.5 search

```python
async def search(
    self,
    *,
    query: str,
    tags: list[str] | None,
    limit: int,
    exclude_ids: set[str],
) -> list[VectorHit]:
    embedding = await self.embed_query(query)
    if embedding is None:
        return []

    fetch_n = min(max((limit + len(exclude_ids)) * 3, 20), 200)
    where = {"tag": tags} if tags else None
    result = await self.vdb.query(
        collection_name=self.collection,
        query_embeddings=[embedding],
        n_results=fetch_n,
        where=where,
    )

    best_by_emoji: dict[str, VectorHit] = {}
    # 解析 ids / distances / metadatas，过滤 exclude_ids
    # 每个 emoji 只保留最小 distance
    # 按 distance 升序，截断到 limit
    return ordered
```

`*3` 是因为一个表情有多个 tag 向量，需要过采样后再去重。

### 8.6 直接复用清单

| 现有实现 | 新位置 | 改动 |
|---|---|---|
| `_embed_query` | `vector_index.embed_query` | 原样 |
| `_query_candidates` | `vector_index.search` | 不返回 path |
| `search_best` 阈值 + 温度采样 | `EmojiService.search_best` | 原样 |
| `search_candidates` 去重 + 分页 | `EmojiService.search_candidates` | 去重下移 |
| `_already_ingested` | `EmojiRepository.get_by_id` | 改查 DB |
| `send_by_id` 扫全库匹配前缀 | `EmojiRepository.find_by_short_id` | 改 SQL 前缀查询 |
| `delete_by_meme_id` 扫全库收集记录 | `vector_index.delete(emoji_id)` | 直接按 emoji_id 删 |
| `_align_data_dir_with_db` | `EmojiReconciler` | 升级为三方对账 |

### 8.7 完整照搬的部分

- 情感标签预设 `EMOTION_TAG_PRESET`
- 距离阈值 + 温度采样公式
- picker 候选去重与分页思路
- 完整 64 位十六进制 ID 校验
- 文件路径遍历与符号链接防护
- GIF 抽帧拼图交给 VLM

---

## 9. 业务流程

### 9.1 register

```text
1. 规范化 description / tags（tag 必须属于预设集合）
2. hash = sha256(data)
3. 查 suppressions；命中且 force=False → 返回 None
4. 查 records：
   - 已存在且 revision 相同 → 返回现有记录
   - 已存在但 revision 不同 → 默认返回现有记录（不覆盖）
5. store.save(data) → rel_path
6. 计算 revision
7. 单事务：insert emoji_records + insert emoji_tags
8. await publish_event(REGISTERED, payload)
9. 若 params["ok"] 为 False → 记录 warning，注册仍返回成功
10. 返回记录
```

异常处理：

- 文件保存失败 → 无副作用，返回失败
- DB 写入失败 → 尽力删除刚写的文件，返回失败
- 事件失败 → 不回滚 DB，由对账补索引

### 9.2 update

```text
1. 从 DB 取记录；不存在 → None
2. 合并 description / tags，计算新 revision
3. 与旧 revision 相同 → 直接返回，不触发 embedding
4. 单事务更新主表 + 重建 tags
5. await publish_event(UPDATED, payload)
6. 返回更新后的记录
```

索引失败时旧向量仍在，只是 revision 过期；对账会重建，不会出现“更新后彻底搜不到”。

### 9.3 delete

```text
1. 从 DB 取记录；不存在也继续（可能只剩文件或向量）
2. 写入 suppression(reason=user_deleted)
3. 单事务删除 emoji_tags + emoji_records
4. 删除文件（已知 rel_path；未知则按 hash 扫 memes/）
5. await publish_event(DELETED, payload)
6. 返回删除结果
```

- 向量删除失败不影响返回：DB 已无记录，检索回表会跳过孤儿向量
- 文件删除失败不影响返回：对账会清理
- 不再需要扫全量向量库、交叉校验记录身份

### 9.4 search

```text
1. 校验 query 非空；tags 过滤到预设集合
2. 组装 SEARCH_PAYLOAD
3. result = await publish_event(SEARCH, payload)
4. params["ok"] 为 False → 返回空列表
5. 遍历 params["results"]：
   - repository.get(emoji_id)，不存在则跳过
   - 文件不存在则跳过
   - 组装 EmojiHit
6. 返回
```

---

## 10. 来源基类

### 10.1 EmojiSource

`EmojiSource` 不是插件系统基类，只负责归一化“定时 + 入库编排”。

```python
@dataclass(frozen=True, slots=True)
class EmojiCandidate:
    source_kind: str
    source_ref: str          # 稳定标识：文件路径或主库 image_id
    mime: str
    hint: str = ""
    known_hash: str | None = None  # 来源已知内容哈希时填写，如 Images.image_id

class EmojiSource(ABC):
    source_name: str

    async def start(self) -> None:
        """注册一次性 + 周期任务。"""

    async def stop(self) -> None:
        """移除 schedule。"""

    async def ingest_once(self) -> None:
        """模板方法：加锁 → 取候选 → 去重 → 读取 → VLM → register → 后处理。"""

    @abstractmethod
    async def collect_candidates(self, *, limit: int) -> list[EmojiCandidate]:
        """返回至多 limit 个候选。

        实现必须推进内部游标：不能每次都返回同一批头部候选，
        否则当头部候选全部已注册/已抑制时，来源会永远无法前进。
        """

    @abstractmethod
    async def load(self, candidate: EmojiCandidate) -> bytes:
        """按候选读取文件内容；失败时抛异常，由基类记录并跳过。"""

    async def after_register(self, candidate: EmojiCandidate, record: EmojiRecord) -> None:
        """注册成功后的可选后处理。"""
```

候选对象只携带元数据，不携带 `bytes`。原因：

- 基类可以先按 `known_hash` / `source_ref` 去重，再决定是否读取文件，避免每轮把已注册图片重新读进内存。
- GIF / 大图可能有数 MB，`list[EmojiCandidate]` 不应持有整批二进制。
- 未来远程来源可以在 `load()` 里做下载，而不是在发现阶段同步拉取。

`collect_candidates` 的游标约定：

- 每次调用返回“接下来”的至多 `limit` 个候选，并推进游标。
- 允许返回已注册 / 已抑制的候选，基类负责过滤。
- 不允许每次都从同一批头部开始；否则头部全是重复项时，扫描会饿死。
- `ingest_once` 使用 `scan_batch_size = max(batch_size * 4, 8)` 取候选，再从中挑出至多 `batch_size` 个真正入库的项。

`ingest_once` 的过滤顺序：

1. `candidate.known_hash` 命中 `emoji_records` 或 `emoji_suppressions` → 跳过，不读文件。
2. `candidate.source_ref` 已存在于当前来源的 `emoji_records` → 跳过，不读文件。
3. `await self.load(candidate)` 读取字节，计算 sha256。
4. 哈希命中 `emoji_records` 或 `emoji_suppressions` → 跳过；必要时把 `source_ref` 记入来源内存忽略集。
5. VLM 判定 `keep=false` → 写 `vlm_rejected` 抑制，并把 `source_ref` 记入忽略集。
6. 成功注册 → `after_register`。

基类统一处理：

- schedule 注册/移除（直接 `await`，不需要轮询等待 scheduler）
- 每来源独立锁，避免重叠执行
- `max_memes` 上限
- 按内容哈希去重
- 抑制表检查
- VLM 收藏决策与情感标签
- GIF 抽帧压缩
- 异常记录与下轮重试

### 10.2 ManualDirectorySource

- 配置目录：`data/neo_emoji_sender/manual_memes`
- 按文件名排序，内部维护一个游标索引；每次返回接下来的 `limit` 个候选
- `known_hash = None`，由基类 `load()` 后计算
- `load()` 直接读取候选路径
- `after_register` 支持 `keep / move / delete`，默认 `keep`（非破坏性）

### 10.3 MediaCacheSource

- 数据来源：主库 `Images` 表，条件 `type='emoji'`
- 按 `timestamp` / 主键分页扫描，内部维护游标
- 字段映射：
  - `known_hash ← image_id`（若 `image_id` 确为内容 sha256）
  - `source_ref ← image_id`
  - `hint ← description`
- `load()` 按 `Images.path` 读取文件
- 跳过：
  - `path` 为空或文件不存在
  - `image_id` 已存在于 `emoji_records`
  - `image_id` 已存在于 `emoji_suppressions`
- `description` 作为 VLM hint；VLM 仍负责 keep 决策与 tags

> 注意：如果 `Images.image_id` 不是内容哈希，`known_hash` 必须留空，由基类读取文件后计算 sha256，否则去重会失效。

实现建议：在 `media_api` 增加只读 `list_media(media_type, limit, offset)`，避免插件直接依赖主库模型与内部 `QueryBuilder`。若暂不改 core，可回退为插件内查询 `Images`。

### 10.4 VLM 标注

VLM 逻辑从现有 `_vlm_decide_and_label` 迁移：

- 人格提示词来自 `get_core_config().personality`
- 情感标签来自 `EMOTION_TAG_PRESET`
- GIF 先抽帧拼图再识别
- 输出严格 JSON：`keep / description / emotion_tags`
- `keep=false` → 写入 `vlm_rejected` 抑制
- 解析失败、模型失败 → 不写抑制，下轮重试

---

## 11. 对账

### 11.1 触发

- 启动后一次：scheduler `delay_seconds=0`
- 周期执行：`reconcile.interval_seconds`

### 11.2 算法

```text
输入：DB 全量记录 + tags、memes/ 文件集合、向量库全量 metadata

1. 文件缺失的 DB 记录
   → 删除记录 + tags
   → 发 DELETED 事件
   → 计入 db_missing_files

2. 无 DB 记录的孤儿文件
   → 跳过 runtime.pending_paths 中的文件
   → 跳过 mtime 在 grace_seconds 内的文件
   → 删除
   → 计入 orphan_files

3. 向量索引检查（按 emoji_id 分组）
   for record in db_records:
       vectors = vector_map.get(record.emoji_id, [])
       if not vectors:
           reindex(record)
       elif {v.tag for v in vectors} != set(record.tags):
           reindex(record)
       elif any(v.revision != record.revision for v in vectors):
           reindex(record)

4. 孤儿向量
   for emoji_id in vector_map:
       if emoji_id not in db_ids:
           vector_index.delete(emoji_id)

5. 输出汇总日志
```

`reindex(record)` = 生成 embedding → 删除旧向量 → 按 tags 重写。

### 11.3 失败场景与恢复

| 场景 | 结果 | 恢复 |
|---|---|---|
| 文件写完、DB 插入失败 | 孤儿文件 | 服务尽力删；对账兜底 |
| DB 写完、embedding 失败 | 记录存在、无向量 | 对账步骤 3 |
| embedding 成功、部分 tag 写入失败 | 向量不完整 | tag 集合比对后重建 |
| 更新 DB 成功、embedding 失败 | 旧向量、旧 revision | revision 比对后重建 |
| 更新时删旧向量成功、写新失败 | 无向量 | 缺失检查后重建 |
| 删除 DB 成功、向量删除失败 | 孤儿向量 | 对账步骤 4；检索回表跳过 |
| 删除 DB 成功、文件删除失败 | 孤儿文件 | 对账步骤 2 |
| 进程在注册中途崩溃 | 文件或 DB 单边存在 | 对账双向清理 |
| embedding 模型维度变化 | 旧向量全部不可用 | 校验 `state.json` 后全量重建 |

### 11.4 state.json

```json
{
  "index": {
    "embedding_model": "…",
    "collection": "neo_emoji_sender"
  },
  "migration": {
    "marker": "legacy_emoji_sender_v1",
    "migrated": 200,
    "skipped": 0,
    "finished_at": 1730000000.0
  }
}
```

顶层按用途分组，各写入方只替换自己那一组，互不覆盖。业务数据不放这里。

启动时与当前 embedding 配置比对；不一致则删除 collection 并全量重建。这是唯一需要全量重建的场景。

---

## 12. 并发与生命周期

### 12.1 锁

所有共享状态放在 `EmojiRuntime`：

| 名称 | 作用 |
|---|---|
| `mutation_lock` | 保护 register/update/delete 的“文件 + DB”短临界区 |
| `pending_paths` | 注册中文件，对账跳过 |
| `index_locks` | 按 `emoji_id` 串行 embedding/向量写 |
| `usage_history` | 每聊天流最近发送记录 |

对账不在全局锁内做 embedding：先取快照，再逐个 emoji 加 `index_lock` 处理。

### 12.2 插件生命周期

`on_plugin_loaded`：

```text
1. 初始化 EmojiRuntime
   - 建目录
   - 初始化 SQLite
   - 初始化 Chroma
   - 校验 state.json
2. 同步 actor reminder
3. 追加自定义场景说明到组件描述
4. 构造启用的 EmojiSource 与 Reconciler
5. 直接注册来源与对账 schedule（`await source.start()`）
   - `create_schedule` 只在 scheduler 停止时才抛错，加载阶段可直接调用，
     无需轮询等待
   - 首轮入库用 `delay_seconds: 0` 的一次性任务延后到事件订阅绑定之后
   - 所有 schedule ID 统一登记到 `runtime.schedule_ids`
```

`on_plugin_unloaded`：

```text
1. 按 runtime.schedule_ids 移除全部 schedule（唯一撤销路径）
2. 关闭 SQLite / Chroma
3. 清理 actor reminder
```

### 12.3 使用历史去重

- 放在 `EmojiRuntime.usage_history`，跨 Service 实例生效
- 每聊天流一个 `deque(maxlen=window)`
- 检索时通过 `exclude_ids` 传给向量化组件
- 全被过滤时回退全量，避免无表情可发
- 本期仍为内存态，重启清空；后续可落库

---

## 13. Action 与 Tool 兼容

### 13.1 direct

- Action：`send_emoji_meme`
- 参数：`description`、`emotion_tags`
- 内部：`EmojiService.search_best` → `send_emoji`

### 13.2 picker

- Tool：`search_emoji_memes`
- Action：`send_emoji_meme_by_id`
- Action：`delete_emoji_meme_by_id`

行为、文案、返回格式与现有实现一致。

### 13.3 注册集合

`get_components()` 按 `plugin.interaction_mode` 返回：

| 模式 | 注册组件 |
|---|---|
| direct | `emoji`、`emoji_vectorizer`、`send_emoji_meme` |
| picker | `emoji`、`emoji_vectorizer`、`search_emoji_memes`、`send_emoji_meme_by_id`、`delete_emoji_meme_by_id` |

`manifest.include` 声明全部六个组件，作为必须维护的组件清单；真正注册以 `get_components()` 为准。

### 13.4 配置兼容

- `plugin.interaction_mode`
- `plugin.inject_system_prompt`
- `prompt.custom_instructions`
- `picker.page_size`
- `dedup.enabled` / `dedup.window`
- `vector.top_n` / `max_distance` / `temperature`

组件名保持不变，避免影响现有提示词与其他插件引用。

---

## 14. 迁移

### 14.1 前提

- 旧插件与新插件不能同时启用（Action 同名）
- 迁移完成前旧数据只读

### 14.2 触发条件

迁移实现在 `plugins/neo_emoji_sender/manager/legacy_migration.py`，插件加载时自动调用，
同时满足以下两条才执行：

1. 调度器中不存在旧插件任务 `emoji_sender_ingest_once` / `emoji_sender_ingest_recurring`
2. `state.json` 中没有迁移标记

旧任务仍在时只记日志跳过、不写标记，待旧插件停用后的下次加载重试。

### 14.3 脚本

`scripts/migrate_neo_emoji_sender.py` 只是套在上述实现上的命令行外壳：

```text
1. 读取旧 Chroma collection（emoji_sender）全量 metadata
2. 按 meme_id 分组，得到 description、tags、created_at、source_cache_path
3. 校验 data/emoji_sender/memes/<meme_id>.<ext> 的 sha256 与文件名一致
4. 复制文件到 data/neo_emoji_sender/memes/
5. 写入 emoji_records + emoji_tags，并写入迁移标记
6. 向量二选一：
   a. 搬运旧 embedding（ID 仍为 {id}:{tag}，补 revision metadata）
   b. 不搬向量，迁移后由对账全量重建
7. 输出统计：记录数、文件数、向量数、跳过项
```

要求：

- `--dry-run`
- 幂等，可重复执行
- 单条失败不中断整体，最后汇总失败列表
- 迁移完成后把旧插件 `config/plugins/emoji_sender/config.toml` 的 `plugin.enabled` 置 `false`

### 14.4 旧数据现状参考

- `data/emoji_sender/memes/`：约 200 个文件
- 旧向量库：约 444 条记录（每表情多 tag）
- 旧插件拒收/删除集合为内存态，无持久化数据可迁移

---

## 15. 配置草案

```toml
[plugin]
enabled = true
interaction_mode = "direct"   # direct / picker
inject_system_prompt = true

[storage]
data_dir = "data/neo_emoji_sender"
max_memes = 200

[vector]
collection_name = "neo_emoji_sender"
top_n = 8
max_distance = 0.35
temperature = 0.3

[reconcile]
interval_seconds = 1800
startup_delay_seconds = 0
grace_seconds = 600

[picker]
page_size = 6

[dedup]
enabled = true
window = 6

[sources.manual]
enabled = true
dir = "data/neo_emoji_sender/manual_memes"
interval_seconds = 120
batch_size = 1
after_register = "keep"   # keep / move / delete

[sources.media]
enabled = true
interval_seconds = 120
batch_size = 1
vlm_enabled = true
```

---

## 16. 测试清单

### 16.1 服务与数据

- 注册后立刻可搜到
- 同内容重复注册只产生一条记录、一个文件、一组向量
- 获取、列表、按 tag / 来源过滤正确
- 更新描述后 revision 变化
- 更新标签后标签集合正确
- 删除后 DB、文件、向量均消失
- 删除后同一图片不会自动重新入库
- `force=True` 可绕过抑制

### 16.2 事件与索引

- 注册事件触发向量写入
- 更新事件替换旧向量
- 删除事件清理向量
- 检索事件回填 `results`
- 向量化组件缺失时检索返回空（严格事件链预期）
- 向量化处理器异常时返回 `ok=False`，不破坏事件链

### 16.3 对账

- mock embedding 失败：注册成功、搜索为空、对账后恢复
- mock 部分 tag 写入失败：对账按 tag 集合补齐
- mock 删除向量失败：对账清理孤儿向量
- 文件手工删除：对账删除 DB 记录并清向量
- 手工放入无 DB 文件：按 grace 规则清理
- 对账与注册并发：`pending_paths` 文件不被误删
- embedding 模型变化：检测到指纹不一致后全量重建

### 16.4 模式与迁移

- direct 模式发送正常
- picker 模式检索、发送、删除正常
- 使用历史去重生效
- 迁移后记录数、文件数、向量数一致
- 迁移后抽查可搜、可发、可删

---

## 17. 实施顺序

| 阶段 | 内容 |
|---|---|
| 1 | `models.py` + `repository.py` + `store.py` + `runtime.py` |
| 2 | `vector_index.py`（从 emoji_sender 迁移） |
| 3 | `service.py`（register/get/list/update/delete/search） |
| 4 | `events.py` + `vectorizer.py`（事件链闭环） |
| 5 | `reconciler.py` + 对账 schedule |
| 6 | `sources/base.py` + `manual_dir.py` + `media_cache.py` |
| 7 | `actions` + `tools` + reminder |
| 8 | 迁移脚本 + 禁用旧插件 |
| 9 | 回归测试与文档补充 |

---

## 18. 后续可选增强

- 管理端 `neo_emoji_sender_admin`：列表、编辑描述、删除、重新收录
- 使用历史落库，重启保持去重
- 向量包装层增加 `upsert`，消除更新空窗
- 批量 embedding，降低对账重建成本
- 导出 / 导入表情包库
- 手动触发全量重建索引的管理命令

---

## 19. 变更记录

| 日期 | 说明 |
|---|---|
| 2026-11-08 | 初版设计：确定事件链、revision 对账、无 vector_status、主库 Images 来源 |
