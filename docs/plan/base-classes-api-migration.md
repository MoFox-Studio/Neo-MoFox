# 基类绕过 `*_api` 修复指南

## 1. 背景

项目为插件开发者提供了一套扁平化的 **Plugin API 层**，位于 `src/app/plugin_system/api/`，共 20 个模块（`send_api` / `stream_api` / `llm_api` / `plugin_api` 等）。每个模块声明 `API_VERSION`，是插件访问 manager / transport / kernel 的**唯一稳定入口**。

但当前各**插件基类**（`src/core/components/base/*.py`）中有多个辅助方法**绕过 `*_api`**，直接 import `src.core.managers.*` / `src.core.transport.*` / `src.kernel.llm`。这导致：

1. **能力丢失**：基类辅助方法重新实现了 `*_api` 已封装的功能，但失去了 `*_api` 提供的强化能力（如 `send_api` 的 stream_manager 推断、私聊 user_id 查询、`adapter_signature` 透传、`extra_media` 自定义段、批量发送）。
2. **私有属性依赖**：`chatter.py` 直接读 `stream_manager._streams` 私有字典，一旦内部实现变化即崩坏。
3. **示范不良实践**：基类是插件开发者的样板，绕过 `*_api` 等于诱导插件开发者同样绕过。
4. **抽象层空洞**：`*_api` 抽象对核心内部代码失效，失去版本化与一致性收益。

## 2. 违规清单（按优先级排序）

### P0 高收益 / 低风险（应优先修复）

| # | 文件:方法 | 行号 | 违规模式 | 应改用 |
|---|---|---|---|---|
| P0.1 | `action.py:_send_to_stream` | L368–L467 | 直接 `get_message_sender()` + `get_adapter_manager()` + 自构 `Message(...)` | `send_api.send_text` / `send_api.send_message` |
| P0.2 | `chatter.py:fetch_unreads` | L691–L723 | `get_stream_manager()._streams.get(...)` 私有字典访问 | `stream_api.get_stream(stream_id=...)` |
| P0.3 | `chatter.py:flush_unreads` | L725–L774 | 同上私有字典访问 | `stream_api.get_stream(stream_id=...)` |
| P0.4 | `chatter.py:_resolve_component_plugin` | L404–L421 | `get_plugin_manager().get_plugin(...)` | `plugin_api.get_plugin(plugin_name)` |
| P0.5 | `agent.py:execute_local_usable` | L305 | `from src.core.utils.llm_tool_call import exec_llm_usable` | `llm_api.exec_llm_usable` |
| P0.6 | `chatter.py:exec_llm_usable` | L465 | `from src.core.utils.llm_tool_call import exec_llm_usable` | `llm_api.exec_llm_usable` |
| P0.7 | `chatter.py:run_tool_call` | L605 | `from src.core.utils.llm_tool_call import run_tool_call` | `llm_api.run_tool_call` |

### P1 中等收益 / 中等风险

| # | 文件:方法 | 行号 | 违规模式 | 应改用 | 状态 |
|---|---|---|---|---|---|
| P1.1 | `chatter.py:modify_llm_usables` | L266–L402 | `get_stream_manager().get_or_create_stream(...)` | `stream_api.get_or_create_stream(stream_id=...)` | 未实施 |
| P1.2 | `action.py:_llm_judge_activation` | L276–L365 | `get_model_config().get_task("utils_small")` + `LLMRequest(...)` | `llm_api.get_model_set_by_task` + `llm_api.create_llm_request` | 未实施 |
| P1.3 | `agent.py:create_llm_request` | L220–L266 | 重复实现 `with_reminder` 互斥校验 + `LLMRequest(...)` | `llm_api.create_llm_request(model_set, request_name, with_reminder=...)` | ✅ 已实施（见 §3.P1.3） |

### P2 可选优化 / 设计缺口

| # | 文件:方法 | 行号 | 问题 | 状态 |
|---|---|---|---|---|
| P2.1 | `chatter.py:create_request` | L476–L553 | `llm_api.create_llm_request` **没有 `meta_data` 入口**，且 `with_reminder` 只登记全局 bucket 不登记流私有 bucket。基类被迫绕过。需先扩 `llm_api.create_llm_request`（增 `meta_data` 参数 + `with_stream_reminder` 选项）。 | ✅ 已实施（见 §3.P2.1） |
| P2.2 | `chatter.py:inject_usables` | L555–L579 | 可选用 `llm_api.create_tool_registry(tools=usables)` 替代手写 `ToolRegistry()` + 循环 `register`；非必须。 | ✅ 已实施 |

## 3. P0 修复策略（本阶段执行）

### P0.1 `BaseAction._send_to_stream`

按 `content` 类型分流：`Message` 实例走 `send_api.send_message`，其余走 `send_api.send_text`。删除自建 `Message(...)` 与 `get_message_sender` / `get_adapter_manager` 调用。

```python
async def _send_to_stream(
    self, content: Message | str, stream_id: str | None = None
) -> bool:
    from src.app.plugin_system.api import send_api

    try:
        if isinstance(content, Message):
            return await send_api.send_message(content)

        target_stream_id = stream_id or self.chat_stream.stream_id
        content_str = content if isinstance(content, str) else str(content)
        return await send_api.send_text(content_str, target_stream_id)
    except Exception as e:
        from src.kernel.logger import get_logger
        logger = get_logger("action")
        logger.error(f"Action {self.name} 发送消息失败: {e}", exc_info=True)
        return False
```

**收益**：恢复 `adapter_signature` 透传、`extra_media` 自定义段、私聊 user_id 经 `user_query_helper` 查询、批量发送能力。

**风险**：`send_api` 内部从 `stream_info` 取 `group_id` / `person_id`，原实现从 `chat_stream.context` 消息取 `extra.group_id`。需新增群聊与私聊回复的集成测试，验证目标 id 一致。

### P0.2 / P0.3 `BaseChatter.fetch_unreads` / `flush_unreads`

仅替换获取流的部分，后续 `context.unread_messages` / `context.add_history_message` 操作保持不变（`stream_api` 未提供等价细粒度接口）。

```python
from src.app.plugin_system.api import stream_api
chat_stream = await stream_api.get_stream(stream_id=self.stream_id)
```

**收益**：消除对 `stream_manager._streams` 私有字典的依赖，行为零变化。

### P0.4 `BaseChatter._resolve_component_plugin`

```python
from src.app.plugin_system.api import plugin_api
target_plugin = plugin_api.get_plugin(plugin_name)
```

### P0.5 / P0.6 / P0.7 `llm_tool_call` 三处

`BaseAgent.execute_local_usable`、`BaseChatter.exec_llm_usable`、`BaseChatter.run_tool_call`：

```python
# 替换：from src.core.utils.llm_tool_call import exec_llm_usable
# 为：from src.app.plugin_system.api import llm_api
# 调用改为 llm_api.exec_llm_usable(...) / llm_api.run_tool_call(...)
```

### P1.3 `BaseAgent.create_llm_request`（已实施）

迁移到 `llm_api.create_llm_request`，并借机对齐 chatter 的流私有桶能力：传入 `stream_id=self.stream_id`，使 `with_reminder` 非空时由 `llm_api` 自动登记**全局桶 + 流私有桶**两个 source（流私有桶形如 `stream:{stream_id}:{bucket}`）。同时 `stream_id` 非空时 `llm_api` 自动注入 `meta_data["stream_id"]`，使 agent 的 LLM 统计也能按流聚合（见 `get_llm_stats_by_stream`）。

`with_usables` 注入 TOOL payload 的逻辑保留在基类侧（`llm_api` 不提供此参数）。

```python
def create_llm_request(self, model_set, request_name="", context_manager=None,
                       with_usables=False, with_reminder=None) -> LLMRequest:
    from src.app.plugin_system.api import llm_api
    request = llm_api.create_llm_request(
        model_set=model_set,
        request_name=request_name,
        context_manager=context_manager,
        with_reminder=with_reminder,
        stream_id=self.stream_id,
    )
    if with_usables:
        request.add_payload(LLMPayload(ROLE.TOOL, cast(list[Any], self._get_all_usables())))
    return request
```

**收益**：消除重复的 `with_reminder` 互斥校验与 `LLMRequest` 构造；agent 获得"流私有 reminder + 按流统计"两项增强，与 chatter 行为对齐。

**风险**：agent 原本 `with_reminder` 只登记全局桶，现在多登记流私有桶。若插件此前依赖"agent 不拾取流私有 reminder"的行为，需注意。实际场景中 agent 拾取流私有 reminder 是更合理的默认（与 chatter 一致），属预期改进。

### P2.1 扩展 `llm_api.create_llm_request` + 迁移 `BaseChatter.create_request`（已实施）

分两步：

**步骤 1：扩展 `llm_api.create_llm_request`**

新增 `stream_id: str | None = None` 与 `meta_data: dict | None = None` 两个可选参数：

- `stream_id` 非空时自动注入 `meta_data["stream_id"] = stream_id`（供 `default_chat_context_compression_handler` 设置压缩标志、供 LLM 统计按流聚合）
- `stream_id` 非空 **且** `with_reminder` 非空 **且** 未显式传入 `context_manager` 时，自动追加流私有 reminder source（`stream:{stream_id}:{bucket}`），与全局桶并存
- `meta_data` 参数允许调用方追加额外 meta 字段，与自动注入的 `stream_id` 合并（调用方同名键优先）

**不自动注入 `default_chat_context_compression_handler`**：该 handler 是 chatter 专属的上下文压缩策略，agent 不应默认获得。调用方（如 chatter）需自行构造带 `context_compression_handler` 的 `context_manager` 后传入。

**步骤 2：迁移 `BaseChatter.create_request`**

`LLMRequest` 构造改用 `llm_api.create_llm_request`，传入 `stream_id=self.stream_id`。`context_manager`（含 `default_chat_context_compression_handler` + 双 reminder source）仍由 chatter 自行构造后传入。`model_set` 统一通过 `llm_api.get_model_set_by_task(task)` 获取（不再直调 `get_model_config()`），相应地测试中 mock 路径从 `src.core.config.get_model_config` 改为 `src.app.plugin_system.api.llm_api.get_model_config`。

**收益**：chatter 不再直接构造 `LLMRequest(...)`，`meta_data` 注入与流私有桶逻辑统一收敛到 `llm_api`；行为与原实现完全等价。

## 4. 不修改项

- `adapter.py`、`tool.py`、`command.py`、`router.py`、`service.py`、`event_handler.py`、`config.py`、`component.py`、`plugin.py`、`__init__.py`：未发现违规。
- `src/core/managers/` 之间互相调用：manager 之间通过 `*_api` 调用会形成循环依赖，保持现状合理。
- `src/core/components/loader.py` 调用 `plugin_manager`：loader 本身是 `plugin_api` 的上游，合理。
- `chatter.py` 后续对 `context.unread_messages` / `context.add_history_message` 的细粒度操作：`stream_api` 未提供等价接口，属基类合理职责。

## 5. 风险与测试

1. **`send_api` 数据源差异**：P0.1 的 `group_id` / `user_id` 推断路径变了，需补群聊回复 + 私聊回复集成测试。
2. **`stream_api.get_stream` 是 async**：原 `fetch_unreads` / `flush_unreads` 已是 async，无需改签名。
3. P0.5 / P0.6 / P0.7 是纯 import 路径替换，`llm_api` 是透传，零行为变化。
4. P0.4 同上，`plugin_api.get_plugin` 内部就是 `_get_plugin_manager().get_plugin(plugin_name)`。
5. **P1.3 agent 流私有桶**：agent `with_reminder` 现在多登记流私有 source，行为从"仅全局桶"变为"全局 + 流私有"。属预期改进（与 chatter 对齐），但若有插件依赖旧行为需注意。
6. **P2.1 chatter mock 路径**：chatter 改走 `llm_api.get_model_set_by_task`，测试中 `patch("src.core.config.get_model_config")` 需同步改为 `patch("src.app.plugin_system.api.llm_api.get_model_config")`（因 `from X import Y` 在模块加载时拷贝引用，patch 源模块不影响 `llm_api` 内已绑定的引用）。
7. **P2.1 compression_handler 不进 llm_api**：`default_chat_context_compression_handler` 是 chatter 专属策略，`llm_api.create_llm_request` 不自动注入；chatter 自行构造 `context_manager` 后传入。

## 6. 执行顺序（本阶段 = P0）

- **commit 1**：文档落地（本文件）
- **commit 2**：P0.1 `BaseAction._send_to_stream` → `send_api`
- **commit 3**：P0.2 + P0.3 `BaseChatter.fetch_unreads` / `flush_unreads` → `stream_api`
- **commit 4**：P0.4 `BaseChatter._resolve_component_plugin` → `plugin_api`
- **commit 5**：P0.5 + P0.6 + P0.7 `llm_tool_call` → `llm_api`

后续 P1 / P2 阶段在另一次 PR 中执行。

## 7. P1.3 / P2.1 实施记录（追加 PR）

本 PR 在 P0 之外追加实施了 P1.3 与 P2.1，原因是 P2.1 的"扩 `llm_api.create_llm_request`"是 P1.3 迁移的前置依赖，二者自然合并。

- **commit A**：扩展 `llm_api.create_llm_request`（增 `stream_id` + `meta_data` 参数，stream_id 非空时注入 meta_data + 自动追加流私有 reminder source）
- **commit B**：P2.1 `BaseChatter.create_request` → `llm_api.create_llm_request`（保留 chatter 自构造 context_manager + compression_handler；保留 `get_model_config().get_task` 直调以兼容测试 mock）
- **commit C**：P1.3 `BaseAgent.create_llm_request` → `llm_api.create_llm_request`（传 `stream_id=self.stream_id`，启用流私有桶 + 按流统计；`with_usables` 保留基类侧）

剩余未实施：P1.1、P1.2。

### P2.2 `BaseChatter.inject_usables`（已实施）

用 `llm_api.create_tool_registry(tools=usables)` 替代手写 `ToolRegistry()` + 循环 `register`。行为等价，`create_tool_registry` 内部就是同样的循环注册逻辑。
