# utils 子模块

对应源码目录：src/core/components/utils

## 概述

utils 子模块提供组件系统复用工具，覆盖 schema 生成、依赖安装和调用参数处理。

## 子文件职责

- schema_utils.py
作用：将 Python 注解转换为 JSON Schema，解析函数签名和 docstring。

- deps_installer.py
作用：检测插件依赖是否满足，并按配置执行批量安装。

- invoke_utils.py
作用：处理 execute 调用前的自动参数兼容逻辑。

## 关键导出

- map_type_to_json
- parse_function_signature
- extract_description_from_docstring
- should_strip_auto_reason_argument
- DependencyInstaller
- PluginDepSpec

## 维护建议

- schema 规则变更时同步校验 Action/Tool 的 to_schema 行为。
- 依赖安装策略变更时同步检查 runtime 初始化阶段。
