# Engram / Booku → Mnemosyne 数据迁移

在项目根目录执行 `migrate_legacy.py`。脚本将来源数据库和当前 Mnemosyne 库合并成一个**新的 SQLite 文件**，不修改来源、不覆盖现有库。SQLite backup API 会包含已经提交但仍位于 WAL 中的数据。

正式迁移时先停止机器人，再运行脚本和切换数据库。机器人运行时可以生成副本用于预演，但快照之后的新写入不会进入该副本；预演副本不能直接作为最新生产库启用。

```powershell
.venv\Scripts\python.exe plugins\Mnemosyne_memory\scripts\migrate_legacy.py `
  --engram E:\trusted-backup\engram-vnext.db `
  --booku data\booku_memory\metadata.db `
  --base data\mnemosyne_memory\vnext.db `
  --output data\mnemosyne_memory\merged-vnext.db
```

`--engram`、`--booku` 可以分别使用，也可以一起使用。省略 `--base` 会创建全新的 Mnemosyne 库。相对路径以当前命令行目录为准。输出文件及其 `.migration.json` 报告必须尚不存在。不要把测试数据生成的库替换为生产库。

迁移范围：

- Engram Schema v5、v6、v7：保留正式记忆、全部历史版本、主体/参与者、证据和消息快照、关联、候选、整理操作、人物印象审计、游标等表。内部 ID 和 JSON 中的关联 ID 保持不变。来源的数据库表前缀转换为 Mnemosyne 前缀，输出为当前 v8。
- Booku：迁移所有存储桶，包含 `knowledge`；知识内容按正式 FACT 记忆保存。正文原文、创建时间、停用状态和确定的人物 ID 保留；全部旧字段、标签类型及原始记录保存在 LEGACY_RECORD 来源中。标签同时生成可检索入口，明确的关联生成 RELATED_TO 边；无法解析的关联保留在旧记录中，不补造对象。
- Booku 临时备忘与孤立标签完整保存在 `mnemosyne_migration_archive`。临时备忘的过期语义无法等同于永久记忆，因此不会被重新激活。
- 旧向量清单、旧 Outbox、模型 checkpoint 路径及旧核心检索入口归档保存。核心入口按当前 Revision 重新生成，非核心标签和检索提示保留。脚本不复制外部 Chroma 文件或模型权重。

现有目标库的向量清单保留。若有 ACTIVE 清单，新入口会排入当前嵌入模型的 Outbox；若没有清单，首次启用输出库时由运行时建立清单和索引。迁移成功表示 SQL 内容、关系及检索入口已经就绪，语义向量需等待运行时的嵌入任务完成。迁移测试不调用收费的模型 API。

同一来源重复导入不会复制相同记录。如果相同 ID 的正式数据不同、来源格式未知、正文/标签回读不一致、版本归属错误或外键损坏，脚本拒绝发布输出，保留来源和当前库。不会用 `INSERT OR IGNORE` 隐藏正式数据冲突。

成功后查看 `merged-vnext.db.migration.json`，其中有每表数量、重复数量、来源快照 SHA-256、完整性和外键检查结果；报告不包含私人正文。保持机器人停止，将 `storage.vnext_db_path` 指向正式迁移的输出库后再启动。已有生产库保留用于回退；切换后等待向量 Outbox 处理完成。

测试来源：

- [Engram 指定分支](https://github.com/MoFox-Studio/Neo-MoFox/tree/feat/engram-memory-trial/plugins/engram_memory)，提交 `50da3262f01e3df8292677491e0b04dc940d4ff3`，Schema v5。
- [Booku dev 分支](https://github.com/MoFox-Studio/Neo-MoFox/tree/dev/plugins/booku_memory)，提交 `0483c39ebcaf5c0b155ceb3a54af66190bed75ed`。
- `test/fixtures/engram_v5.sql` 和 `booku.sql` 是从上述提交的原始 SQLAlchemy 模型生成的 SQLite DDL。仓库没有提交实际 `.db` 文件；Engram 测试使用该 DDL 创建的合成记忆，不能据此声称已验证某份真实 Engram 数据库。

独立测试命令：

```powershell
.venv\Scripts\python.exe -m pytest plugins\Mnemosyne_memory\test\test_legacy_migration.py -q --no-cov
```

## 2026-10-16 复核结果

- 插件完整回归：297 项通过、1 项跳过。跳过项用于缺失 PyTorch 的环境，当前机器已安装该依赖；另有专门用例模拟依赖缺失。启动测试检查被捕获后记录的初始化错误，覆盖模型关闭、仅推理、仅训练、同时启用及缺少依赖；真实宿主分别完成文件夹和 MFP 插件加载、初始化、卸载。
- 迁移专项 18 项通过，覆盖正文、历史、证据、标签、知识内容、停用状态、关联、临时备忘归档、WAL、重复导入、冲突拒绝、外键和修订归属检查，以及已有输出或侧车文件的保护。
- 从上述 GitHub 提交重新加载原始 ORM 并生成结构，Engram 的 28 张表、Booku 的 3 张表及约束和索引与测试 DDL 一致。Engram 仍只使用合成记忆验证，没有读取本地不可信库。
- 本机真实 Booku 的 787 条原始记录和 5,645 条标签再次逐条核对一致；当前 Mnemosyne 快照中的每一行都保留。重复迁移后所有表内容相同，源 Booku 文件未改变，完整性与外键检查通过，实际仓储读取、证据读取和词条检索通过。
- 迁移输出分别在有、无 ACTIVE 清单的情况下交给实际向量 Outbox 服务处理，测试内存后端接收的入口与全部有效检索入口一致。测试不代表外部嵌入服务已完成语义向量计算，也不切换生产数据库。
