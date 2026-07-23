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
- 版本兼容性采用 **AND 同等判断**：`api_version` 与 `min_core_version` 各司其职，声明了哪一项就校验哪一项，只要任一项声明且不满足即拒绝注册；两者都未声明才回退到「警告但加载」。
  - `api_version`：声明插件 API 模块版本，按 20 个 `*_api` 模块逐一校验。支持字符串（等价于对所有模块应用同一要求）与 dict（仅校验声明模块）两种形式。详见 [plugin-api-versioning](./plugin-api-versioning.md)。
  - `min_core_version`：声明核心能力版本（如新事件、新内核接口），基于 `CORE_VERSION` 做 `>=` 比较。
