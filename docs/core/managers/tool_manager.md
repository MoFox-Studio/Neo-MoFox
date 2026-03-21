# tool_manager 子模块

对应源码目录：src/core/managers/tool_manager

## 概述

tool_manager 负责 Tool 组件调用、调用历史与缓存，并提供 MCP 工具接入能力。

## 子文件职责

- tool_use.py
作用：执行 Tool 组件，记录 ToolHistory，支持缓存命中。

- tool_history.py
作用：维护调用记录、缓存结果、统计信息和 prompt 格式化。

- mcp_manager.py
作用：管理 MCP 连接、工具发现、动态 Tool 注册与调用。

- mcp_adapter.py
作用：将 MCP Tool 适配为标准 Tool schema 和执行结果。

- __init__.py
作用：导出 ToolUse、MCPManager 及其单例入口。

## 关键入口

- get_tool_use
- get_mcp_manager

## 工作流简述

1. ToolUse 从全局注册表定位 Tool 类并执行。
2. ToolHistory 记录执行轨迹并可选缓存结果。
3. MCPManager 连接外部 MCP 服务并动态注册工具。
4. MCPToolAdapter 负责参数与结果格式转换。

## 注意事项

- 动态注册 MCP 工具签名格式为 mcp_provider:tool:tool_name。
- 工具缓存策略应结合业务时效性设置 TTL。
