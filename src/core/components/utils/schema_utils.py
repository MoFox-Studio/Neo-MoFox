"""Schema 生成工具函数。

本模块提供 LLM Tool Schema 生成的通用工具函数。
供 Action 和 Tool 组件共享使用，避免非法依赖。
"""

import inspect
import types
from typing import Any, Callable, get_args, get_origin, get_type_hints


# Python 类型到 JSON Schema 类型的映射
_TYPE_MAPPING: dict[type, str] = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _unwrap_optional_type(type_hint: Any) -> Any:
    """解包 ``Optional[T]`` 或 ``T | None``，返回首个非 ``None`` 的类型。 

    当注解不是可空联合类型时，原样返回。

    Args:
        type_hint: 待处理的类型注解。

    Returns:
        解包后的类型注解。
    """
    origin = get_origin(type_hint)
    if origin not in (types.UnionType, getattr(types, "UnionType", object)):
        # typing.Union 在 Python 3.11 下 origin 为 typing.Union（非 types.UnionType）
        from typing import Union

        if origin is not Union:
            return type_hint

    args = get_args(type_hint)
    if not args:
        return type_hint

    non_none_args = [arg for arg in args if arg is not type(None)]
    if non_none_args and len(non_none_args) < len(args):
        return non_none_args[0]
    return type_hint


def build_type_schema(type_hint: Any) -> dict[str, Any]:
    """将 Python 类型注解转换为 JSON Schema 片段。"""
    annotated_origin = getattr(type_hint, "__origin__", None)
    annotated_meta = getattr(type_hint, "__metadata__", None)
    if annotated_origin is not None and annotated_meta is not None:
        return build_type_schema(annotated_origin)

    normalized = _unwrap_optional_type(type_hint)

    if isinstance(normalized, str):
        type_name = map_type_to_json(normalized)
        return {"type": type_name}

    if normalized is type(None):
        return {"type": "null"}

    origin = get_origin(normalized)
    if origin in (list, set, tuple):
        args = get_args(normalized)
        item_type = args[0] if args else Any
        item_schema = build_type_schema(item_type) if item_type is not Any else {"type": "string"}
        return {
            "type": "array",
            "items": item_schema,
        }

    if origin is dict:
        args = get_args(normalized)
        value_type = args[1] if len(args) > 1 else Any
        value_schema = (
            build_type_schema(value_type)
            if value_type is not Any
            else {"type": "string"}
        )
        return {
            "type": "object",
            "additionalProperties": value_schema,
        }

    return {"type": map_type_to_json(normalized)}


def map_type_to_json(type_hint: Any) -> str:
    """将 Python 类型注解映射到 JSON Schema 类型。

    Args:
        type_hint: Python 类型注解

    Returns:
        str: JSON Schema 类型字符串

    Examples:
        >>> map_type_to_json(int)
        'integer'
        >>> map_type_to_json(list[int])
        'array'
    """
    # 处理 Annotated 类型（不同 Python 版本 get_origin 行为不同）
    annotated_origin = getattr(type_hint, "__origin__", None)
    annotated_meta = getattr(type_hint, "__metadata__", None)
    if annotated_origin is not None and annotated_meta is not None:
        return map_type_to_json(annotated_origin)

    # 处理 Optional/联合类型（含 PEP 604: T | None）
    type_hint = _unwrap_optional_type(type_hint)

    # 处理 None 类型
    if type_hint is type(None):
        return "null"

    origin = get_origin(type_hint)
    if origin is not None:
        # 如果是 list[T] 或其他泛型
        for container_type in (list, dict, set, tuple):
            if origin is container_type:
                return _TYPE_MAPPING.get(container_type, "object")

    # 处理字符串类型的类型提示（如 "int"）
    # 这里禁止使用 eval，避免执行任意代码。
    if isinstance(type_hint, str):
        safe_name_map: dict[str, Any] = {
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "none": type(None),
            "nonetype": type(None),
        }
        resolved = safe_name_map.get(type_hint.strip().lower())
        if resolved is None:
            return "string"
        type_hint = resolved

    if type_hint is type(None):
        return "null"

    # 直接类型映射
    return _TYPE_MAPPING.get(type_hint, "string")


def _parse_google_style_args(doc: str) -> dict[str, str]:
    """解析 Google 风格 docstring 的 Args 段。

    仅解析形如：

    Args:
        name: desc...

    返回 {"name": "desc..."}。
    """
    if not doc:
        return {}

    lines = doc.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() in {"Args:", "Arguments:"}:
            start = idx + 1
            break
    if start is None:
        return {}

    result: dict[str, str] = {}
    current_name: str | None = None
    current_desc_parts: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_desc_parts
        if current_name is None:
            return
        desc = " ".join(part.strip() for part in current_desc_parts if part.strip())
        result[current_name] = desc.strip()
        current_name = None
        current_desc_parts = []

    for raw in lines[start:]:
        stripped = raw.strip()
        if not stripped:
            # 空行：视为可能的段落分隔，但不立即终止
            if current_name is not None:
                current_desc_parts.append("")
            continue

        # 遇到新的 section（如 Returns: / Raises: / Examples:）则结束
        if not raw.startswith(" ") and stripped.endswith(":"):
            break

        # 解析 "name: desc" 行
        if ":" in stripped and not stripped.startswith(":"):
            name_part, desc_part = stripped.split(":", 1)
            candidate = name_part.strip()
            if candidate and " " not in candidate and "\t" not in candidate:
                flush()
                current_name = candidate
                current_desc_parts = [desc_part.strip()]
                continue

        # 续行：归并到上一个参数描述
        if current_name is not None:
            current_desc_parts.append(stripped)

    flush()
    return result


def _extract_annotated_description(type_hint: Any) -> tuple[Any, str | None]:
    annotated_origin = getattr(type_hint, "__origin__", None)
    annotated_meta = getattr(type_hint, "__metadata__", None)
    if annotated_origin is None or annotated_meta is None:
        return type_hint, None

    for meta in annotated_meta:
        if isinstance(meta, str) and meta.strip():
            return annotated_origin, meta.strip()

    return annotated_origin, None


def parse_function_signature(
    func: Callable,
    component_name: str,
    component_description: str,
) -> dict[str, Any]:
    """解析函数签名并生成 LLM Tool Schema。

    Args:
        func: 要解析的函数（通常是 execute 方法）
        component_name: 组件名称
        component_description: 组件描述

    Returns:
        dict[str, Any]: OpenAI Tool 格式的 schema

    Examples:
        >>> schema = parse_function_signature(
        ...     my_action.execute,
        ...     "send_message",
        ...     "发送消息到用户"
        ... )
    """
    sig = inspect.signature(func)
    parameters: dict[str, Any] = {}
    doc = inspect.getdoc(func) or ""
    arg_desc = _parse_google_style_args(doc)

    # 支持 `from __future__ import annotations`：此时注解可能是字符串
    # 使用 get_type_hints 解析（保留 Annotated 元数据）
    try:
        resolved_hints = get_type_hints(func, include_extras=True)
    except Exception:
        resolved_hints = {}

    # 遍历函数参数
    for param_name, param in sig.parameters.items():
        # 跳过 self
        if param_name == "self":
            continue

        # 跳过 *args / **kwargs（schema 需要显式参数）
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        annotation = resolved_hints.get(param_name, param.annotation)
        base_type, annotated_desc = _extract_annotated_description(annotation)

        param_info: dict[str, Any] = build_type_schema(base_type)
        param_info["description"] = (
            annotated_desc
            or arg_desc.get(param_name)
            or f"{param_name} 参数"
        )

        # 处理默认值
        if (
            param.default != inspect.Parameter.empty
            and param.default is not None
        ):
            param_info["default"] = param.default

        # 添加参数描述（可以从 docstring 解析）
        parameters[param_name] = param_info

    return {
        "type": "function",
        "function": {
            "name": component_name,
            "description": component_description,
            "parameters": {
                "type": "object",
                "properties": parameters,
                "required": [
                    name
                    for name, param in sig.parameters.items()
                    if name != "self"
                    and param.kind
                    not in (
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    )
                    and param.default == inspect.Parameter.empty
                ],
            },
        },
    }


def extract_description_from_docstring(func: Callable) -> str:
    """从函数的 docstring 提取描述。

    Args:
        func: 函数对象

    Returns:
        str: 提取的描述，如果没有 docstring 则返回空字符串

    Examples:
        >>> def example_func():
        ...     \"\"\"这是一个示例函数。\"\"\"
        ...     pass
        >>> extract_description_from_docstring(example_func)
        '这是一个示例函数。'
    """
    if func.__doc__:
        return func.__doc__.strip().split("\n")[0]
    return ""
