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

### P1 中等收益 / 中等风险（本阶段不实施）

| # | 文件:方法 | 行号 | 违规模式 | 应改用 |
|---|---|---|---|---|
| P1.1 | `chatter.py:modify_llm_usables` | L266–L402 | `get_stream_manager().get_or_create_stream(...)` | `stream_api.get_or_create_stream(stream_id=...)` |
| P1.2 | `action.py:_llm_judge_activation` | L276–L365 | `get_model_config().get_task("utils_small")` + `LLMRequest(...)` | `llm_api.get_model_set_by_task` + `llm_api.create_llm_request` |
| P1.3 | `agent.py:create_llm_request` | L220–L266 | 重复实现 `with_reminder` 互斥校验 + `LLMRequest(...)` | `llm_api.create_llm_request(model_set, request_name, with_reminder=...)` |

### P2 可选优化 / 设计缺口（本阶段不实施）

| # | 文件:方法 | 行号 | 问题 |
|---|---|---|---|
| P2.1 | `chatter.py:create_request` | L476–L553 | `llm_api.create_llm_request` **没有 `meta_data` 入口**，且 `with_reminder` 只登记全局 bucket 不登记流私有 bucket。基类被迫绕过。需先扩 `llm_api.create_llm_request`（增 `meta_data` 参数 + `with_stream_reminder` 选项）。 |
| P2.2 | `chatter.py:inject_usables` | L555–L579 | 可选用 `llm_api.create_tool_registry(tools=usables)` 替代手写 `ToolRegistry()` + 循环 `register`；非必须。 |

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

## 6. 执行顺序（本阶段 = P0）

- **commit 1**：文档落地（本文件）
- **commit 2**：P0.1 `BaseAction._send_to_stream` → `send_api`
- **commit 3**：P0.2 + P0.3 `BaseChatter.fetch_unreads` / `flush_unreads` → `stream_api`
- **commit 4**：P0.4 `BaseChatter._resolve_component_plugin` → `plugin_api`
- **commit 5**：P0.5 + P0.6 + P0.7 `llm_tool_call` → `llm_api`

后续 P1 / P2 阶段在另一次 PR 中执行。
