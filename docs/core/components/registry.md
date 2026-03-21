# registry 模块

对应源码：src/core/components/registry.py

## 概述

ComponentRegistry 提供组件注册、查询和依赖关系跟踪，是组件发现的中心索引。

## 核心职责

- 按签名注册组件类并校验重复。
- 支持按插件、按组件类型查询。
- 维护组件依赖图并支持依赖检查。
- 计算反向依赖与级联禁用列表。

## 核心索引结构

- _components: signature -> class
- _dependencies: signature -> list of dependencies
- _by_plugin: plugin -> type -> name -> class
- _by_type: type -> plugin -> name -> class

## 关键能力

- register
- get / get_by_plugin / get_by_type
- check_dependencies
- get_dependents
- get_cascade_disable_list

## 维护建议

- 新增组件签名规则时，需要同步 parse_signature 约束。
- 依赖关系错误会直接影响级联启停行为，需优先修复。
