"""插件组件统一基类。

本模块提供 BaseComponent 类，作为所有插件组件（Action/Tool/Chatter 等）的共同祖先。
统一定义组件的基本属性、签名生成和内置检查行为。
同时提供旧属性名到新属性名的自动桥接，保证向后兼容。
"""

from __future__ import annotations

import warnings
from abc import ABC


def _bridge_legacy_attrs(cls: type) -> None:
    """将旧命名属性（如 tool_name）自动桥接到统一属性（name）。

    若子类显式声明了旧属性但未声明新属性，则将旧值写入新属性，
    并发出 DeprecationWarning。

    中间基类通过 ``_legacy_name_attr`` / ``_legacy_desc_attr``
    声明自己所对应的旧属性名。
    """
    cls_vars = vars(cls)
    legacy_name_attr = getattr(cls, "_legacy_name_attr", "") or ""
    legacy_desc_attr = getattr(cls, "_legacy_desc_attr", "") or ""

    bridge_name = (
        legacy_name_attr
        and legacy_name_attr in cls_vars
        and cls_vars[legacy_name_attr]
        and ("name" not in cls_vars or not cls_vars.get("name"))
    )
    bridge_desc = (
        legacy_desc_attr
        and legacy_desc_attr in cls_vars
        and cls_vars[legacy_desc_attr]
        and ("description" not in cls_vars or not cls_vars.get("description"))
    )

    if bridge_name:
        cls.name = cls_vars[legacy_name_attr]
        warnings.warn(
            f"{cls.__name__}: 类属性 '{legacy_name_attr}' 已弃用，请改用 'name'",
            DeprecationWarning,
            stacklevel=3,
        )

    if bridge_desc:
        cls.description = cls_vars[legacy_desc_attr]
        warnings.warn(
            f"{cls.__name__}: 类属性 '{legacy_desc_attr}' 已弃用，请改用 'description'",
            DeprecationWarning,
            stacklevel=3,
        )


class BaseComponent(ABC):
    """所有插件组件的统一基类。

    每个组件子类必须声明 ``component_type``，可声明 ``name`` / ``description`` / ``dependencies``。
    ``get_signature()`` 由本基类统一实现。

    向后兼容：若子类使用了旧属性名（如 ``tool_name``）而未使用新属性名（``name``），
    框架会通过 ``__init_subclass__`` 自动桥接并发出 ``DeprecationWarning``。

    Class Attributes:
        component_type: 组件类型标识，对应 ComponentType 枚举值（如 "action", "tool"）
        name: 组件名称
        description: 组件描述
        dependencies: 组件级依赖，格式为 ["plugin_name:type:name"]
        _legacy_name_attr: 旧名称属性名（中间基类覆盖，如 "action_name"）
        _legacy_desc_attr: 旧描述属性名（中间基类覆盖，如 "action_description"）
        _plugin_: 所属插件名称（由插件管理器注入）
        _signature_: 组件签名（由插件管理器注入）
    """

    component_type: str = ""
    name: str = ""
    description: str = ""
    dependencies: list[str] = []

    _legacy_name_attr: str = ""
    _legacy_desc_attr: str = ""

    _plugin_: str = ""
    _signature_: str = ""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ == BaseComponent.__module__:
            return
        _bridge_legacy_attrs(cls)

    @classmethod
    def get_signature(cls) -> str | None:
        """获取组件的唯一签名。

        格式为 "plugin_name:component_type:name"。
        若 _signature_ 已由插件管理器注入则直接返回缓存值。

        Returns:
            str | None: 组件签名；若 _plugin_ 或 name 为空则返回 None
        """
        if cls._signature_:
            return cls._signature_
        if cls._plugin_ and cls.name:
            return f"{cls._plugin_}:{cls.component_type}:{cls.name}"
        return None


__all__ = ["BaseComponent"]