"""Config 模块类型定义

提供配置模块使用的类型别名和辅助类型。
"""

from typing import Any, TypeAlias


# 配置数据字典类型
ConfigData: TypeAlias = dict[str, dict[str, Any]]

# 配置节数据类型
SectionData: TypeAlias = dict[str, Any]

# TOML 原始数据类型
TOMLData: TypeAlias = dict[str, Any]


__all__ = [
    "ConfigData",
    "SectionData",
    "TOMLData",
]
