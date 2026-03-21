# loader 模块

对应源码：src/core/components/loader.py

## 概述

loader 模块承担两层职责：插件类注册与插件宏观加载计划。

## 核心职责

- 提供 register_plugin 装饰器与插件类注册表。
- 提供 get_plugin_class 等注册表查询函数。
- 解析 PluginManifest 与 ComponentInclude。
- 从目录、zip、mfp 读取 manifest。
- 校验版本与依赖，计算插件加载顺序。
- 将单插件实际加载委托给 PluginManager。

## 关键数据结构

- PluginManifest
- ComponentInclude

## 关键边界

- 宏观规划由 loader 负责。
- 单插件导入和生命周期执行由 managers/plugin_manager 负责。

## 注意事项

- 插件入口点由 manifest.entry_point 指定。
- 压缩包支持根级或一级目录下的 manifest.json。
