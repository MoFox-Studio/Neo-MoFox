"""组件工具函数包。

本包提供组件系统的通用工具函数，包括 JSON Schema 生成与插件依赖安装能力。
"""

from src.core.components.utils.deps_installer import DependencyInstaller, PluginDepSpec
from src.core.components.utils.invoke_utils import should_strip_auto_reason_argument
from src.core.components.utils.schema_utils import (
    extract_description_from_docstring,
    map_type_to_json,
    parse_function_signature,
)

__all__ = [
    "map_type_to_json",
    "parse_function_signature",
    "extract_description_from_docstring",
    "should_strip_auto_reason_argument",
    "DependencyInstaller",
    "PluginDepSpec",
]
