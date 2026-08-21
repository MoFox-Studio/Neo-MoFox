# tool_manager 子模块

对应源码目录：src/core/managers/tool_manager

## 概述

tool_manager 负责 Tool 组件调用、调用历史与缓存，并提供 MCP 工具接入能力。

## 子文件职责

- tool_use.py
作用：执行 Tool 组件，记录 ToolHistory，支持缓存命中。

- manager.py
作用：ToolManager，负责 Tool 组件的查询与筛选（筛选前发布 `BEFORE_TOOL_FILTER` 事件）。

- tool_history.py
作用：维护调用记录、缓存结果、统计信息和 prompt 格式化。

- mcp_manager.py
作用：管理 MCP 连接、工具发现、动态 Tool 注册与调用。

- mcp_adapter.py
作用：将 MCP Tool 适配为标准 Tool schema 和执行结果。

- __init__.py
作用：导出 ToolUse、MCPManager、ToolManager 及其单例入口。

## 关键入口

- get_tool_use
- get_mcp_manager
- get_tool_manager

## 工作流简述

1. ToolUse 从全局注册表定位 Tool 类并执行。
2. ToolManager 提供 Tool 组件的查询与筛选（filter_tools）。
3. ToolHistory 记录执行轨迹并可选缓存结果。
4. MCPManager 连接外部 MCP 服务并动态注册工具。
5. MCPToolAdapter 负责参数与结果格式转换。

## 注意事项

- 动态注册 MCP 工具签名格式为 mcp_provider:tool:mcp-server-tool，LLM tool name 使用短横线格式。
- 当前 MCP 接入是客户端模式：支持使用外部 Stdio、SSE、Streamable HTTP MCP 服务，不将 Neo-MoFox 反向暴露为 MCP 服务端。
- 工具缓存策略应结合业务时效性设置 TTL。
