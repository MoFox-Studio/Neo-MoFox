# Config 排障手册

## 启动时报配置未初始化

症状：调用 get_core_config/get_model_config/get_mcp_config 抛 RuntimeError。

排查：

1. 确认启动流程已调用对应 init_xxx_config。
2. 确认 import 顺序没有在模块导入期提前调用 get_xxx_config。
3. 检查是否存在测试环境中 reset 后未重新 init 的情况。

## 配置文件未生成或生成失败

排查：

1. 检查 config 目录是否有写权限。
2. 检查路径是否写错（尤其是 model.toml 与 models.toml 混用）。
3. 检查磁盘只读或容器挂载路径权限。

## ModelSet 构建失败

症状：get_task/get_model_set_by_name 抛 KeyError/ValueError。

排查：

1. model_tasks 中的 model_list 是否引用了不存在的模型名。
2. 模型的 api_provider 是否在 api_providers 中存在。
3. task 名称是否拼写正确。

## API Key 轮询异常

症状：provider.get_api_key 抛密钥列表为空。

排查：

1. api_key 是否为空列表。
2. 是否误把变量替换为空字符串。
3. 部署时环境变量注入是否成功。

## MCP 配置加载后不生效

排查：

1. mcp.enabled 是否为 true。
2. stdio_servers 的 command/args 是否可执行。
3. 当前 SSE 连接尚未实现，配置后只会告警。

## auto_update 导致配置文件改写

说明：

- core_config 与 model_config 使用 load(auto_update=True)，字段签名变化会触发回写。
- 若不希望自动改写，应在调用层明确策略并统一约束。
