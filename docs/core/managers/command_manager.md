# command_manager 模块

对应源码：src/core/managers/command_manager.py

## 概述

CommandManager 负责命令识别、命令组件匹配和执行路由，支持前缀与参数解析。

## 核心职责

- 管理命令前缀配置（默认 /）。
- 判断输入是否命令并提取命令路径。
- 按组件匹配规则定位 Command 组件。
- 执行命令并返回结果。

## 关键入口

- set_prefixes
- is_command / match_command
- execute_command
- get_command_manager
