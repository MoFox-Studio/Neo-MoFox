"""associated_types 校验工具。"""

from __future__ import annotations


def validate_associated_types(
    component_cls: type,
    *,
    component_kind: str,
    component_name_attr: str,
) -> list[str]:
    """校验组件声明的 associated_types。

    Args:
        component_cls: 待校验的组件类。
        component_kind: 组件类型描述，用于错误提示。
        component_name_attr: 组件名称属性名，如 ``action_name`` / ``agent_name``。

    Returns:
        list[str]: 规范化后的 associated_types 列表。

    Raises:
        ValueError: 当 associated_types 未定义、不是列表、为空，或包含空字符串时抛出。
    """

    raw_types = getattr(component_cls, "associated_types", None)
    component_name = getattr(component_cls, component_name_attr, "") or component_cls.__name__

    if not isinstance(raw_types, list):
        raise ValueError(
            f"{component_kind} '{component_name}' 的 associated_types 必须是非空 list[str]"
        )

    normalized_types = [str(item).strip() for item in raw_types]
    if not normalized_types or any(not item for item in normalized_types):
        raise ValueError(
            f"{component_kind} '{component_name}' 的 associated_types 必须是非空 list[str]"
        )

    return normalized_types
