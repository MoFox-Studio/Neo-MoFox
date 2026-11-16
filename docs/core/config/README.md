# Config 模块文档

本目录对应 src/core/config，负责 core 层运行配置、模型配置与 MCP 配置的定义、加载和全局访问。

## 模块职责

- 定义 core 运行所需配置模型
- 提供配置加载与初始化入口
- 管理模型配置与 MCP 配置等子配置

## 文档导航

- [core_config](./core_config.md): 主运行配置结构与关键配置项。
- [model_config](./model_config.md): 模型提供商、模型清单、任务映射与 ModelSet 构建。
- [mcp_config](./mcp_config.md): MCP 连接配置结构与初始化行为。
- [loading_flow](./loading_flow.md): 配置初始化顺序、全局单例与调用约束。
- [troubleshooting](./troubleshooting.md): 配置加载与运行时配置问题排查。

## 当前代码结构

- core_config.py: 核心配置模型
- model_config.py: 模型配置定义与加载
- mcp_config.py: MCP 相关配置
- __init__.py: 聚合导出

## 导出入口

src/core/config/__init__.py 对外聚合导出三类能力：

- Core 配置：CoreConfig、init_core_config、get_core_config
- Model 配置：ModelConfig、init_model_config、get_model_config
- MCP 配置：MCPConfig、init_mcp_config、get_mcp_config

## 初始化原则

- 所有 get_xxx_config 在 init_xxx_config 前调用都会抛 RuntimeError。
- init_xxx_config 在配置文件不存在时会自动生成默认文件。
- core_config 与 model_config 默认采用 auto_update=True 加载，便于配置签名自动回写。

## 依赖关系

- 依赖底层能力：src/kernel/config
- 服务对象：src/core/managers、src/app/runtime

## 建议加载顺序

1. init_core_config
2. init_model_config
3. init_mcp_config（按需）

该顺序可确保 model_config 在构建 ModelSet 时能读取 core_config 的 advanced 参数。
