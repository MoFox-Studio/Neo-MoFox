# mcp_config 模块

对应源码：src/core/config/mcp_config.py

## 概述

MCPConfig 定义 MCP 功能开关及服务端点配置，供 tool_manager/mcp_manager 初始化连接时使用。

## 配置结构

- mcp.enabled
是否启用 MCP。

- mcp.stdio_servers
字典结构，key 为服务名，value 通常包含 command、args、env。

- mcp.sse_servers
字典结构，key 为服务名，value 为 URL。

## 全局实例管理

- _global_mcp_config: MCPConfig | None
- get_mcp_config: 未初始化时抛 RuntimeError
- init_mcp_config: 自动创建默认文件并加载

## init_mcp_config 行为

1. 配置文件不存在时创建目录并写入默认配置。
2. 使用 MCPConfig.load(config_path) 加载。
3. 返回全局单例。

## 与 MCPManager 协作

MCPManager.initialize 会读取 get_mcp_config：

- enabled=False 时直接跳过。
- stdio_servers 逐项连接。
- sse_servers 当前实现仅告警未实现。
