# Project Guidelines

## Code Style
- Python 版本要求 `>=3.11`，依赖与运行统一使用 `uv`（见 `pyproject.toml`、`README.md`）。
- 遵循 PEP 8，并保持与现有模块一致的命名/分层风格（参考 `src/kernel/`、`src/core/`、`src/app/`）。
- 所有函数/方法参数与返回值必须有类型注解（见 `代码规范.md`）。
- 所有函数、类、文件需包含文档字符串/文件简介（见 `代码规范.md`）。
- 新增 `src/` 代码时，同步补齐 `test/` 中对应单测，目标覆盖 `src`（见 `pyproject.toml`、`代码规范.md`）。

## Architecture
- 严格遵守三层架构：`src/kernel`（技术能力）→ `src/core`（领域逻辑）→ `src/app`（装配与运行时）。
- 应用入口在 `main.py`，运行时主流程在 `src/app/runtime/bot.py`。
- Kernel 能力按子域组织（如 `db/`, `llm/`, `config/`, `concurrency/`, `storage/`），Core 通过 manager/component 编排。
- 插件相关实现位于 `src/core/components/` 与 `src/core/managers/plugin_manager.py`。

## Build and Test
- 安装依赖：`uv sync`
- 启动应用：`uv run main.py`
- 运行测试：`pytest`
- 针对单文件测试：`pytest test/path/to/test_file.py`
- 覆盖率检查：`pytest --cov=src`
- 代码检查：`ruff check src/`（自动修复：`ruff check --fix src/`）

## Project Conventions
- 禁止滥用 fallback 机制，优先修复根因（见 `代码规范.md`）。
- 异步任务统一通过 `task_manager`（`src/kernel/concurrency`）管理，不直接使用 `asyncio.create_task()`。
- 组件签名格式固定为：`plugin_name:component_type:component_name`。
- 配置系统优先使用 `ConfigBase`/`SectionBase` + `config_section` 模式（见 `src/kernel/config/`）。
- 数据访问优先使用 `CRUDBase` 与 `QueryBuilder`（见 `src/kernel/db/`）。
- 新模块完成后在 `examples/` 下提供可运行示例（见 `代码规范.md`）。

## Integration Points
- 外部核心依赖：`openai`, `chromadb`, `fastapi`, `mcp`, `mofox-wire`, `sqlalchemy`（见 `pyproject.toml`）。
- 模型与平台配置主要来自 `config/model.toml`、`config/core.toml`。
- 传输链路围绕 Adapter/Sink/Receiver 组织（见 `src/core/transport/` 与 `src/core/components/base/adapter.py`）。
- 插件支持目录、zip、`.mfp` 形式加载（见 `src/core/components/loader.py`、`src/core/managers/plugin_manager.py`）。

## Security
- 不要在提交中包含明文密钥、令牌、数据库密码（重点检查 `config/model.toml`、`config/core.toml`）。
- 涉及外部 API（LLM/平台）调用时，最小化日志中的敏感字段输出。
- 处理插件包（zip/`.mfp`）时保持来源可信，避免引入未审查代码。
- 持久化数据默认位于 `data/`（如 `data/chroma_db`, `data/json_storage`）；变更存储行为时评估隐私与路径安全。