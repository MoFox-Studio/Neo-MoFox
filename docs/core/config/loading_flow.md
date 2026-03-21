# Config 加载流程

本文件描述 core/config 三套配置的推荐初始化顺序与调用约束。

## 推荐顺序

1. init_core_config(config/core.toml)
2. init_model_config(config/model.toml)
3. init_mcp_config(config/mcp.toml)（按需）

## 原因

- model_config 在构建 ModelSet 时会尝试读取 core_config.advanced。
- app/runtime 初始化流程默认先 core 再 model，符合运行期依赖关系。

## 通用行为

每个 init_xxx_config 的共同步骤：

1. 检查文件是否存在。
2. 不存在则写入默认配置。
3. 调用 load() 解析并校验。
4. 缓存到模块级全局变量。

## 获取阶段约束

- get_core_config 只能在 init_core_config 后调用。
- get_model_config 只能在 init_model_config 后调用。
- get_mcp_config 只能在 init_mcp_config 后调用。

未初始化即获取会抛 RuntimeError。

## 与 runtime 的关联

main.py 和 app/runtime/bot.py 在启动阶段依赖这些配置：

- core 配置控制日志、数据库、watchdog、http router。
- model 配置控制 LLM provider 与任务模型。
- mcp 配置控制外部 MCP 服务接入。
