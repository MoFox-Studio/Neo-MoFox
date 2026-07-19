"""插件组件统一基类。

本模块提供 BaseComponent 类，作为所有插件组件（Action/Tool/Chatter 等）的共同祖先。
统一定义组件的基本属性、签名生成和内置检查行为。
"""

from __future__ import annotations

from abc import ABC


class BaseComponent(ABC):
    """所有插件组件的统一基类。

    每个组件子类必须声明 ``component_type``，可声明 ``name`` / ``description`` / ``dependencies``。
    ``get_signature()`` 由本基类统一实现。

    Class Attributes:
        component_type: 组件类型标识，对应 ComponentType 枚举值（如 "action", "tool"）
        name: 组件名称
        description: 组件描述
        dependencies: 组件级依赖，格式为 ["plugin_name:type:name"]
        _plugin_: 所属插件名称（由插件管理器注入）
        _signature_: 组件签名（由插件管理器注入）
    """

    component_type: str = ""
    name: str = ""
    description: str = ""
    dependencies: list[str] = []

    _plugin_: str = ""
    _signature_: str = ""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ == BaseComponent.__module__:
            return

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