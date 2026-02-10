"""src.kernel.config.core

实现层：承载 config 模块的全部逻辑实现。

`src.kernel.config.__init__` 应保持轻量，只负责对外导出与文档。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, TypeVar, Self, get_args, get_origin

import tomllib
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_core import PydanticUndefined

from .types import ConfigData, TOMLData


SectionT = TypeVar("SectionT", bound="SectionBase")

__all__ = [
    "ConfigBase",
    "SectionBase",
    "config_section",
    "Field",
]


def config_section(name: str) -> Callable[[type[SectionT]], type[SectionT]]:
    """配置节装饰器。

    重要：该装饰器使用泛型返回类型，确保 IDE/Pylance 能保留被装饰类的具体类型，
    避免把 `SectionB` 降级成 `SectionBase`，从而导致字段（如 `value_b`）无法被识别。
    """

    def decorator(cls: type[SectionT]) -> type[SectionT]:
        cls.__config_section_name__ = name  # type: ignore[attr-defined]
        return cls

    return decorator


class SectionBase(BaseModel):
    """配置节基类。

    配置节是一组相关的配置选项。它们会被 ConfigBase 自动收集并映射到 TOML 节。
    """

    model_config = ConfigDict(extra="forbid")


class ConfigBase(BaseModel):
    """配置基类（静态可见）。

    配置类本身是一个 Pydantic 模型，所有配置节都应作为字段显式声明。
    这能让 IDE/Pylance 在访问 `config.xxx.yyy` 时正确进行类型推断。
    """

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_dict(cls, data: ConfigData) -> Self:
        """从字典加载配置。"""

        return cls.model_validate(data)

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        auto_update: bool = False,
    ) -> Self:
        """从 TOML 文件加载配置。

        当 ``auto_update=True`` 时，会将配置文件内容与模型定义的“签名”进行比对：
        - 配置节/字段是否存在
        - 注释文档（section docstring + Field.description）
        - 字段类型（由类型注解推导）
        - 默认值（由 Field.default / default_factory 推导）

        若签名不一致，将自动回写 TOML 文件使其更新到模型定义的版本；同时尽可能保留
        文件中已有且能通过类型校验的用户值。
        """

        path = Path(path)
        # 确保文件存在
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        original_text = path.read_text(encoding="utf-8")
        with path.open("rb") as f:
            raw = tomllib.load(f)

        if not auto_update:
            return cls.from_dict(raw)

        merged = _merge_with_model_defaults(cls, raw)
        new_text = _render_toml_with_signature(cls, merged)

        if _normalize_newlines(original_text) != _normalize_newlines(new_text):
            path.write_text(new_text, encoding="utf-8")

        return cls.from_dict(merged)

    @classmethod
    def default(cls) -> ConfigData:
        """生成默认配置字典。"""

        return cls().model_dump()  # type: ignore[return-value]


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _type_repr(annotation: Any) -> str:
    try:
        return getattr(annotation, "__name__", None) or str(annotation)
    except Exception:
        return "unknown"


def _toml_escape_string(value: str) -> str:
    # TOML 基本字符串，最小转义
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_format_key(key: str) -> str:
    # 尽量使用裸键；否则退回到字符串键
    if key and all(ch.isalnum() or ch in {"_", "-"} for ch in key):
        return key
    return _toml_escape_string(key)


def _toml_format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if "\n" in value:
            # 多行字符串使用三引号
            escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
            return f'"""\n{escaped}"""'
        return _toml_escape_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_format_value(v) for v in value) + "]"
    if isinstance(value, dict):
        items: list[str] = []
        for k in sorted(value.keys(), key=lambda x: str(x)):
            if not isinstance(k, str):
                continue
            items.append(f"{_toml_format_key(k)} = {_toml_format_value(value[k])}")
        return "{ " + ", ".join(items) + " }"
    if value is None:
        # TOML 不支持 null；用空字符串占位
        return _toml_escape_string("")
    return _toml_escape_string(str(value))


def _get_section_name(section_model: type[SectionBase], fallback: str) -> str:
    name = getattr(section_model, "__config_section_name__", None)
    return str(name) if name else fallback


def _eval_default_factory(factory: Any) -> Any:
    """兼容 Pydantic v2 的 default_factory 形态（可能需要 validated_data）。"""

    try:
        return factory()
    except TypeError:
        return factory({})


def _iter_sections(config_model: type[ConfigBase]) -> list[_SectionInfo]:
    sections: list[_SectionInfo] = []
    for field_name, model_field in config_model.model_fields.items():
        annotation = model_field.annotation
        section_model, is_list = _get_section_model_from_annotation(annotation)
        if section_model is not None:
            sections.append(
                _SectionInfo(
                    name=_get_section_name(section_model, field_name),
                    model=section_model,
                    is_list=is_list,
                )
            )
    # 移除 sections.sort(key=lambda x: x.name)
    return sections


@dataclass(frozen=True)
class _SectionInfo:
    name: str
    model: type[SectionBase]
    is_list: bool


def _get_section_model_from_annotation(
    annotation: Any,
) -> tuple[type[SectionBase] | None, bool]:
    if isinstance(annotation, type) and issubclass(annotation, SectionBase):
        return annotation, False

    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        if args:
            item = args[0]
            if isinstance(item, type) and issubclass(item, SectionBase):
                return item, True

    return None, False


def _merge_with_model_defaults(
    config_model: type[ConfigBase],
    raw: TOMLData,
) -> ConfigData:
    """将 raw 与模型默认值合并，并移除模型未定义的节/字段。

    保留 raw 中能通过字段类型校验的值；不合法的值回退到默认值/占位值。
    """

    merged: dict[str, Any] = {}
    for section in _iter_sections(config_model):
        raw_section = raw.get(section.name)

        if section.is_list:
            items_out: list[dict[str, Any]] = []
            if isinstance(raw_section, list):
                for item in raw_section:
                    if not isinstance(item, dict):
                        continue
                    items_out.append(_merge_section_fields(section.model, item))
            merged[section.name] = items_out
            continue

        if not isinstance(raw_section, dict):
            raw_section = {}

        merged[section.name] = _merge_section_fields(section.model, raw_section)

    return merged


def _merge_section_fields(
    section_model: type[SectionBase],
    raw_section: dict[str, Any],
) -> dict[str, Any]:
    section_out: dict[str, Any] = {}
    for key, field in section_model.model_fields.items():
        annotation = field.annotation
        nested_model, is_list = _get_section_model_from_annotation(annotation)

        if nested_model is not None:
            if is_list:
                raw_list = raw_section.get(key)
                items_out: list[dict[str, Any]] = []
                if isinstance(raw_list, list):
                    for item in raw_list:
                        if not isinstance(item, dict):
                            continue
                        items_out.append(_merge_section_fields(nested_model, item))
                section_out[key] = items_out
            else:
                raw_nested = raw_section.get(key)
                if not isinstance(raw_nested, dict):
                    raw_nested = {}
                section_out[key] = _merge_section_fields(nested_model, raw_nested)
            continue

        default_value = (
            field.default
            if field.default is not None
            and field.default is not ...
            and field.default is not PydanticUndefined
            else None
        )
        if field.default_factory is not None:
            try:
                default_value = _eval_default_factory(field.default_factory)
            except Exception:
                default_value = None

        if key in raw_section:
            candidate = raw_section[key]
            try:
                section_out[key] = TypeAdapter(annotation).validate_python(candidate)
                continue
            except Exception:
                pass

        if default_value is not None:
            section_out[key] = default_value
        else:
            section_out[key] = _placeholder_for_type(annotation)

    return section_out


def _placeholder_for_type(annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is list:
        return []
    if origin is dict:
        return {}

    if args and type(None) in args:
        return None

    if annotation in (str,):
        return ""
    if annotation in (int,):
        return 0
    if annotation in (float,):
        return 0.0
    if annotation in (bool,):
        return False
    if annotation is list:
        return []
    if annotation is dict:
        return {}
    return ""


def _render_toml_with_signature(
    config_model: type[ConfigBase],
    data: ConfigData,
) -> str:
    """按模型签名生成带注释的 TOML（确定性输出）。"""

    lines: list[str] = []
    sections = _iter_sections(config_model)

    for idx, section in enumerate(sections):
        lines.append("")
        # 获取 Section 的 docstring 作为分类标题
        section_doc = inspect.getdoc(section.model) or ""
        title = section_doc.splitlines()[0].strip() if section_doc else section.name
        
        # 构造装饰线，使标题居中
        line_width = 80
        padding = (line_width - len(title) - 4) // 2
        header_line = "# " + "=" * padding + " " + title + " " + "=" * (line_width - padding - len(title) - 4)
        
        lines.append(header_line)
        lines.append("")

        section_data = data.get(section.name)
        if section.is_list:
            items: list[dict[str, Any]] = []
            if isinstance(section_data, list):
                items = [item for item in section_data if isinstance(item, dict)]
            if not items:
                items = [_merge_section_fields(section.model, {})]

            for item_idx, item in enumerate(items):
                _render_section_block(
                    lines,
                    section.name,
                    section.model,
                    item,
                    is_list=True,
                    include_doc=(item_idx == 0),
                    show_inline_comments=(item_idx == 0),
                )
                if item_idx != len(items) - 1:
                    lines.append("")
            continue

        if not isinstance(section_data, dict):
            section_data = {}

        _render_section_block(
            lines,
            section.name,
            section.model,
            section_data,
            is_list=False,
            include_doc=True,
            show_inline_comments=True,
        )

    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines).rstrip() + "\n"


def _render_section_block(
    lines: list[str],
    section_name: str,
    section_model: type[SectionBase],
    section_data: dict[str, Any],
    *,
    is_list: bool,
    include_doc: bool,
    section_comment: str = "",
    show_inline_comments: bool = True,
) -> None:
    # 移除 Section 顶部的 docstring 注释

    if is_list:
        lines.append(f"[[{section_name}]]{section_comment if show_inline_comments else ''}")
    else:
        lines.append(f"[{section_name}]{section_comment if show_inline_comments else ''}")

    for field_name, field in section_model.model_fields.items():
        value = section_data.get(field_name)
        # 如果值为 None 且不是嵌套模型，则跳过渲染（实现可选字段不显示）
        annotation = field.annotation
        nested_model, nested_is_list = _get_section_model_from_annotation(annotation)
        
        if value is None and nested_model is None:
            continue

        # 提取 description 第一行作为行内注释
        description = field.description or ""
        inline_comment = ""
        if description and show_inline_comments:
            first_line = description.splitlines()[0].strip()
            if first_line:
                inline_comment = f" # {first_line}"

        if nested_model is not None:
            nested_data = section_data.get(field_name)
            if nested_is_list:
                nested_items: list[dict[str, Any]] = []
                if isinstance(nested_data, list):
                    nested_items = [item for item in nested_data if isinstance(item, dict)]
                if not nested_items:
                    nested_items = [_merge_section_fields(nested_model, {})]

                for nested_idx, item in enumerate(nested_items):
                    lines.append("")
                    _render_section_block(
                        lines,
                        f"{section_name}.{field_name}",
                        nested_model,
                        item,
                        is_list=True,
                        include_doc=(nested_idx == 0),
                        section_comment=inline_comment,
                        show_inline_comments=show_inline_comments,
                    )
                continue

            if not isinstance(nested_data, dict):
                nested_data = {}
            lines.append("")
            _render_section_block(
                lines,
                f"{section_name}.{field_name}",
                nested_model,
                nested_data,
                is_list=False,
                include_doc=True,
                section_comment=inline_comment,
                show_inline_comments=show_inline_comments,
            )
            continue

        # 移除 # signature 注释行

        value_str = _toml_format_value(value)
        
        # 拼接 key = value 和行内注释
        lines.append(f"{field_name} = {value_str}{inline_comment}")

    while lines and lines[-1] == "":
        lines.pop()

    while lines and lines[-1] == "":
        lines.pop()
