# LLMUsable 执行分层设计

## 目标

统一 `Tool`、`Action`、`Agent` 的执行路径，形成清晰的三层结构：

```text
API
  -> Manager
      -> 底层 LLMUsable 执行封装
```

底层只负责执行机制，Manager 负责组件业务，API 负责提供稳定的调用入口。

## 三层职责

### 底层执行封装

建议放在：

```text
src/kernel/llm/usable_execution.py
```

底层执行封装负责：

- 调用已经创建好的 `LLMUsable` 实例；
- 统一支持 coroutine 和 async generator；
- 处理 `LLMUsableExecution` 的 `_WORKING`、`_READY`、`_DONE` 状态；
- 批量执行时提供并发准备和顺序门控；
- 统一等待、异常传递和结果获取。

底层不负责：

- 查找组件；
- 创建 Tool、Action、Agent 实例；
- 发布 `before_tool_call` 或 `before_action_call`；
- 工具缓存、历史记录和 telemetry；
- LLM tool call 的业务控制流。

建议接口：

```python
async def execute(
    usable: LLMUsable,
    **kwargs: Any,
) -> tuple[bool, Any]:
    ...

async def execute_many(
    executions: Sequence[LLMUsableExecution | None],
) -> None:
    ...
```

底层接收组件实例，组件实例的创建和上下文绑定由 Manager 完成。

### Manager 层

每类组件由对应 Manager 负责完整的执行流程。

#### ToolManager

负责：

- 根据签名查找并创建 Tool；
- 绑定 plugin、message 和 stream 上下文；
- 发布 `before_tool_call`；
- 应用事件处理器返回的参数修改；
- 调用底层执行封装；
- 处理缓存、ToolHistory、结果格式化和 telemetry。

```text
ToolManager.execute_tool
  -> 创建 Tool
  -> before_tool_call
  -> usable_execution.execute(tool)
  -> history / cache / telemetry
```

#### ActionManager

负责：

- 创建或恢复 `ChatStream`；
- 创建 Action 并绑定运行上下文；
- 发布 `before_action_call`；
- 应用参数和 message 修改；
- 调用底层执行封装；
- 发布执行后的事件。

```text
ActionManager.execute_action
  -> 创建 Action
  -> before_action_call
  -> usable_execution.execute(action)
  -> after_action_call
```

#### AgentManager

负责：

- 查找并创建 Agent；
- 绑定 `stream_id` 和 plugin；
- 调用底层执行封装；
- 管理 Agent 的私有 usable；
- 处理 Agent 的日志和错误语义。

### API 层

API 不直接实例化组件，也不直接调用 `_wrap_execute()`，只转发给对应 Manager。

```text
llm_api.execute_tool
  -> ToolManager.execute_tool

llm_api.execute_action
  -> ActionManager.execute_action

llm_api.execute_agent
  -> AgentManager.execute_agent
```

LLM 返回的一批 `ToolCall` 可以由批处理 API 负责解析和路由：

```text
llm_api.dispatch_llm_tool_calls
  -> ToolManager / ActionManager / AgentManager
      -> usable_execution
```

批处理 API 负责保持结果顺序；具体组件的执行前后业务仍由对应 Manager 负责。

## PR #171 的关联

PR #171 暴露了当前路径的问题：

```text
session.run_tool_call
  -> llm_api.dispatch_llm_tool_calls
      -> create_llm_usable_execution
          -> instance._wrap_execute()
```

这条路径绕过了 `ActionManager` 和 `ToolManager`，因此不会触发：

- `before_action_call`；
- `before_tool_call`。

按新的分层设计，LLM tool call 应该路由到对应 Manager：

```text
LLM tool call
  -> ActionManager.execute_action
      -> before_action_call
      -> 底层执行封装
```

```text
LLM tool call
  -> ToolManager.execute_tool
      -> before_tool_call
      -> 底层执行封装
```

这样事件属于组件管理业务，不需要在底层执行封装中重复补发。

## `llm_tool_call.py` 的迁移

`src/core/utils/llm_tool_call.py` 当前混合了三类职责：

| 当前职责 | 迁移位置 |
|---|---|
| `LLMUsableExecution` 调度 | `src/kernel/llm/usable_execution.py` |
| Tool / Action / Agent 实例化 | 对应 Manager |
| Tool call 批量解析和路由 | `llm_api` 或独立 dispatcher |
| `before_*` 事件 | `ToolManager` / `ActionManager` |
| TOOL_RESULT 顺序写回 | tool call dispatcher |

建议按以下顺序迁移：

1. 提取通用 execution 状态机和批量调度逻辑；
2. 为 Tool、Action、Agent 补齐 Manager 执行入口；
3. 将 LLM tool call 从直接 `_wrap_execute()` 改为 Manager 路由；
4. 将 `before_tool_call` 和 `before_action_call` 放回对应 Manager；
5. 让 `llm_api` 只调用 Manager；
6. 迁移测试和文档引用；
7. 删除 `src/core/utils/llm_tool_call.py`。

## 最终调用链

普通组件调用：

```text
API
  -> 对应 Manager
      -> 创建并准备组件
      -> 执行前事件
      -> 底层 LLMUsable 执行封装
      -> 执行后业务处理
```

LLM tool call：

```text
LLM response
  -> tool call dispatcher
      -> ToolManager / ActionManager / AgentManager
          -> 底层 LLMUsable 执行封装
```

核心原则：

> 底层负责“怎么执行”，Manager 负责“执行哪个组件以及执行前后的业务流程”，API 负责“对外提供调用入口”。
