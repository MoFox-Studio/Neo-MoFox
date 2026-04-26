# core_config 模块

对应源码：src/core/config/core_config.py

## 概述

CoreConfig 定义应用运行期核心配置，包括 bot 生命周期参数、数据库、权限、HTTP 路由和插件依赖安装策略。

## 关键配置节

- bot
职责：UI、日志、watchdog、tick 与消息缓冲、关闭超时、预检开关。

- chat
职责：默认聊天模式与上下文上限。

- personality
职责：bot 人设、风格和安全约束。

- database
职责：sqlite/postgresql 配置、SSL、连接池参数。

- permissions
职责：owner 列表、默认权限、命令覆盖和缓存策略。

- http_router
职责：HTTP 服务开关、监听地址和 API key。

- advanced
职责：force_sync_http、trust_env 等全局请求策略。

- plugin_deps
职责：插件 Python 依赖自动安装行为。

## 全局实例管理

- _global_config: CoreConfig | None
- get_core_config: 未初始化时抛 RuntimeError
- init_core_config: 负责创建默认文件并加载

## init_core_config 行为

1. 检查 config_path 是否存在。
2. 不存在时自动创建父目录并写入默认 TOML。
3. 调用 CoreConfig.load(config_path, auto_update=True) 加载并回写签名变更。

## 运行关联说明

- bot.tick_interval 影响 transport 的流驱动心跳间隔；stream 阈值为秒级精确值，默认 150 秒警告、300 秒重启。
- plugin_deps 配置直接影响 app/runtime 初始化阶段的依赖安装步骤。
- advanced 配置会被 model_config 合并到 ModelSet extra_params。
