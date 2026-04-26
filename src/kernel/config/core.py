"""src.kernel.config.core

实现层：承载 config 模块的全部逻辑实现。

`src.kernel.config.__init__` 应保持轻量，只负责对外导出与文档。

支持 WebUI 可视化配置编辑器：
    使用 Field 定义配置项时，可指定 UI 属性（label, icon, placeholder 等），
    系统会自动生成 Schema 供 WebUI 渲染配置表单。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypeVar, Self, get_args, get_origin

import tomllib
from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic import Field as PydanticField
from pydantic_core import PydanticUndefined

from .types import ConfigData, TOMLData


SectionT = TypeVar("SectionT", bound="SectionBase")


# ==================== Field：增强的配置字段定义 ====================


def Field(  # noqa: N802
    default: Any = ...,
    *,
    # === Pydantic 原生验证参数 ===
    ge: float | int | None = None,  # 大于等于（最小值）- 适用于 int/float
    le: float | int | None = None,  # 小于等于（最大值）- 适用于 int/float
    gt: float | int | None = None,  # 大于
    lt: float | int | None = None,  # 小于
    min_length: int | None = None,  # 最小长度 - 适用于 str/list
    max_length: int | None = None,  # 最大长度 - 适用于 str/list
    pattern: str | None = None,  # 正则表达式 - 适用于 str
    # === 通用描述参数 ===
    description: str = "",  # 字段描述（必填，用于生成帮助文本）
    # === WebUI 显示增强参数 ===
    label: str | None = None,  # 显示标签（不指定则使用字段名）
    tag: Literal[
        "general",      # 通用设置
        "security",     # 安全/密码
        "network",      # 网络
        "ai",           # AI/智能
        "database",     # 数据库/存储
        "user",         # 用户
        "timer",        # 时间/调度
        "performance",  # 性能/数值
        "text",         # 文本
        "list",         # 列表/数组
        "advanced",     # 高级设置
        "debug",        # 调试
        "file",         # 文件
        "color",        # 颜色
        "notification", # 通知
        "plugin",       # 插件
    ] | None = None,  # 预设标签（系统会映射到对应图标）
    placeholder: str | None = None,  # 输入框占位符文本
    hint: str | None = None,  # 帮助提示文本
    order: int = 0,  # 显示顺序（越小越靠前）
    hidden: bool = False,  # 是否隐藏
    disabled: bool = False,  # 是否禁用（只读）
    # === 输入控件类型 ===
    input_type: Literal[
        "text",  # 单行文本
        "password",  # 密码（遮罩）
        "textarea",  # 多行文本
        "number",  # 数字输入
        "slider",  # 滑块
        "switch",  # 开关
        "select",  # 下拉选择
        "list",  # 列表编辑器
        "json",  # JSON 编辑器
        "color",  # 颜色选择器
        "file",  # 文件路径选择
    ]
    | None = None,
    # === 控件特定参数 ===
    rows: int | None = None,  # textarea 行数
    step: float | int | None = None,  # number/slider 步进值
    choices: list[Any] | None = None,  # select 选项列表
    # === 列表配置（item_type="list" 时） ===
    item_type: Literal["str", "number", "boolean", "object"] | None = None,  # 列表项类型
    item_fields: dict[str, Any] | None = None,  # 当 item_type="object" 时的字段定义
    min_items: int | None = None,  # 最少列表项数
    max_items: int | None = None,  # 最多列表项数
    # === 条件显示 ===
    depends_on: str | None = None,  # 依赖的字段名
    depends_value: Any = None,  # 依赖字段的期望值
    # === Pydantic 其他参数 ===
    title: str | None = None,
    examples: list[Any] | None = None,
    **extra: Any,
) -> Any:
    """增强的配置字段定义函数。

    这是 Pydantic Field 的增强版本，支持：
    1. Pydantic 原生验证参数（ge, le, min_length, max_length, pattern 等）
    2. WebUI 可视化编辑器的 UI 属性（label, icon, placeholder 等）
    3. 自动类型推断（根据约束自动选择最佳控件）

    Args:
        default: 默认值（必需，除非字段是可选的）

        # Pydantic 原生验证
        ge: 最小值（>=）- 适用于 int/float
        le: 最大值（<=）- 适用于 int/float
        gt: 大于（>）- 适用于 int/float
        lt: 小于（<）- 适用于 int/float
        min_length: 最小长度 - 适用于 str/list
        max_length: 最大长度 - 适用于 str/list
        pattern: 正则表达式验证 - 适用于 str

        # 通用
        description: 字段描述（推荐填写，用于生成帮助信息）

        # WebUI 显示
        label: 显示标签（不指定则使用字段名）
        tag: 预设标签（如 "ai", "security"），系统会自动映射到对应图标
        placeholder: 输入框占位符
        hint: 帮助提示文本
        order: 显示顺序（数字越小越靠前）
        hidden: 是否隐藏
        disabled: 是否禁用（只读）

        # 控件类型
        input_type: 强制指定输入控件类型（不指定则自动推断）
            - text: 单行文本
            - password: 密码输入（遮罩）
            - textarea: 多行文本
            - number: 数字输入框
            - slider: 滑块
            - switch: 开关
            - select: 下拉选择
            - list: 列表编辑器
            - json: JSON 编辑器
            - color: 颜色选择器
            - file: 文件路径选择

        # 控件特定参数
        rows: textarea 的行数（默认 5）
        step: number/slider 的步进值（如 0.1）
        choices: select 的选项列表

        # 列表配置
        item_type: 列表项类型（"str", "number", "boolean", "object"）
        item_fields: 当 item_type="object" 时，定义对象字段
        min_items: 最少列表项数
        max_items: 最多列表项数

        # 条件显示
        depends_on: 依赖的字段名（如 "use_proxy"）
        depends_value: 依赖字段的期望值（如 True）

    Returns:
        Pydantic FieldInfo 对象

    Example:
        ```python
        class AISection(SectionBase):
            # 自动推断为 slider（因为有 ge/le）
            temperature: float = Field(
                default=0.7,
                ge=0.0,
                le=2.0,
                step=0.1,
                description="生成温度",
                tag="performance"
            )

            # 强制使用 password 控件
            api_key: str = Field(
                default="",
                description="API 密钥",
                input_type="password",
                placeholder="sk-xxxxxxxx"
            )

            # 条件显示
            use_proxy: bool = Field(
                default=False,
                description="是否使用代理"
            )
            proxy_url: str = Field(
                default="",
                description="代理地址",
                depends_on="use_proxy",
                depends_value=True
            )

        # 在 ConfigBase 中使用（配合 config_section 装饰器）
        class MyConfig(ConfigBase):
            @config_section("ai", title="AI 配置", tag="ai")
            class AIConfig(SectionBase):
                temperature: float = Field(...)

            ai: AIConfig = Field(default_factory=AIConfig)
        ```
    """
    # 构建 json_schema_extra 存储自定义 UI 属性
    json_schema_extra = {
        # WebUI 显示
        "label": label,
        "tag": tag,
        "placeholder": placeholder,
        "hint": hint,
        "order": order,
        "hidden": hidden,
        "disabled": disabled,
        # 控件类型
        "input_type": input_type,
        # 控件特定
        "rows": rows,
        "step": step,
        "choices": choices,
        # 列表配置
        "item_type": item_type,
        "item_fields": item_fields,
        "min_items": min_items,
        "max_items": max_items,
        # 条件显示
        "depends_on": depends_on,
        "depends_value": depends_value,
    }

    # 移除 None 值，减少冗余
    json_schema_extra = {k: v for k, v in json_schema_extra.items() if v is not None}

    # 如果有 examples，添加到 extra 中
    if examples:
        extra["examples"] = examples

    # 调用 Pydantic Field
    return PydanticField(
        default=default,
        ge=ge,
        le=le,
        gt=gt,
        lt=lt,
        min_length=min_length,
        max_length=max_length,
        pattern=pattern,
        description=description,
        title=title,
        json_schema_extra=json_schema_extra if json_schema_extra else None,
        **extra,
    )


# ==================== 配置节与配置基类 ====================


def config_section(
    name: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tag: Literal[
        "general",
        "security",
        "network",
        "ai",
        "database",
        "user",
        "timer",
        "performance",
        "text",
        "list",
        "advanced",
        "debug",
        "file",
        "color",
        "notification",
        "plugin",
    ] | None = None,
    order: int = 0,
) -> Callable[[type[SectionT]], type[SectionT]]:
    """配置节装饰器（增强版）。

    用于标记配置节类，并设置其在 TOML 中的节名以及 WebUI 显示元数据。

    Args:
        name: TOML 节名（必需）
        title: WebUI 显示标题（可选，不指定则使用节名美化）
        description: 节描述（可选，不指定则使用类 docstring 首行）
        tag: 预设标签（可选），系统会自动映射到对应图标
        order: 显示顺序，数字越小越靠前（默认 0）

    Returns:
        装饰器函数

    重要：该装饰器使用泛型返回类型，确保 IDE/Pylance 能保留被装饰类的具体类型，
    避免把 `SectionB` 降级成 `SectionBase`，从而导致字段（如 `value_b`）无法被识别。

    Example:
        ```python
        class MyConfig(ConfigBase):
            @config_section(
                "general",
                title="通用设置",
                description="基本配置选项",
                tag="general",
                order=0,
            )
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True, description="启用功能")

            general: GeneralSection = Field(default_factory=GeneralSection)
        ```
    """

    def decorator(cls: type[SectionT]) -> type[SectionT]:
        cls.__config_section_name__ = name  # type: ignore[attr-defined]
        cls.__config_section_title__ = title  # type: ignore[attr-defined]
        cls.__config_section_description__ = description  # type: ignore[attr-defined]
        cls.__config_section_tag__ = tag  # type: ignore[attr-defined]
        cls.__config_section_order__ = order  # type: ignore[attr-defined]
        return cls

    return decorator


class SectionBase(BaseModel):
    """配置节基类。

    配置节是一组相关的配置选项。它们会被 ConfigBase 自动收集并映射到 TOML 节。
    """

    model_config = ConfigDict(
        extra="forbid"
    )  # 禁止传入模型未声明的字段，防止配置拼写错误被静默忽略


class ConfigBase(BaseModel):
    """配置基类（静态可见）。

    配置类本身是一个 Pydantic 模型，所有配置节都应作为字段显式声明。
    这能让 IDE/Pylance 在访问 `config.xxx.yyy` 时正确进行类型推断。

    示例：
        ```python
        class MyConfig(ConfigBase):
            @config_section(
                "general",
                title="通用设置",
                description="基本配置选项",
                tag="general",
                order=0,
            )
            class GeneralSection(SectionBase):
                enabled: bool = Field(default=True, description="启用功能")

            general: GeneralSection = Field(default_factory=GeneralSection)

            @config_section(
                "advanced",
                title="高级设置",
                description="除非你知道自己在干什么，否则别动",
                tag="advanced",
                order=100,
            )
            class AdvancedSection(SectionBase):
                debug_mode: bool = Field(default=False, description="调试模式")

            advanced: AdvancedSection = Field(default_factory=AdvancedSection)
        ```
    """

    model_config = ConfigDict(
        extra="forbid"
    )  # 同 SectionBase：禁止多余字段，保持配置结构严格

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
        # 确保文件存在，若父目录不存在则递归创建
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        original_text = path.read_text(
            encoding="utf-8"
        )  # 保存原始文本，用于后续变更检测
        with path.open("rb") as f:
            raw = tomllib.load(f)

        if not auto_update:
            # 不需要签名同步，直接解析原始数据
            return cls.from_dict(raw)

        # 合并用户值与模型默认值
        merged = _merge_with_model_defaults(cls, raw)
        new_text = _render_toml_with_signature(
            cls, merged
        )  # 按最新模型签名重新渲染 TOML 文本

        # 仅当文本内容（规范化换行后）发生变化时才写回文件，避免不必要的磁盘操作和时间戳更新
        if _normalize_newlines(original_text) != _normalize_newlines(new_text):
            path.write_text(new_text, encoding="utf-8")

        return cls.from_dict(merged)

    @classmethod
    def default(cls) -> ConfigData:
        """生成默认配置字典。

        对于没有显式默认值的必填字段，会按字段类型生成占位值，
        以确保默认配置文件可被稳定生成。
        """

        return _merge_with_model_defaults(cls, {})


def _normalize_newlines(text: str) -> str:
    """将文本中所有换行符统一规范化为 LF（``\n``）。

    用于在比较 TOML 文本前消除平台差异（CRLF / CR / LF）。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _type_repr(annotation: Any) -> str:
    """将类型注解转换为可读字符串。

    优先使用 ``__name__`` 属性（如 ``str``、``int``），
    回退到 ``str(annotation)``（处理泛型等复杂类型），
    若发生异常则返回 ``"unknown"``。
    """
    try:
        return getattr(annotation, "__name__", None) or str(annotation)
    except Exception:
        return "unknown"


def _toml_escape_string(value: str) -> str:
    """对字符串进行 TOML 基本字符串最小转义并包裹双引号。

    仅转义反斜杠（``\\``）和双引号（``"``），其余字符保持原样。
    """
    # TOML 基本字符串，最小转义
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_format_key(key: str) -> str:
    """将字典键格式化为合法的 TOML 键。

    若键由字母、数字、``_``、``-`` 组成，则直接作为裸键输出；
    否则退回到双引号字符串键以保证合法性。
    """
    # 尽量使用裸键；否则退回到字符串键
    if key and all(ch.isalnum() or ch in {"_", "-"} for ch in key):
        return key
    return _toml_escape_string(key)


def _toml_format_value(value: Any) -> str:
    """将 Python 值序列化为 TOML 值字符串。

    支持 ``bool``、``int``、``float``、``str``（含多行）、``list``、``dict``
    以及 ``None``（以空字符串占位，因为 TOML 不支持 null 值）。
    其他类型会先转换为字符串再做基本字符串转义。
    """
    if isinstance(value, bool):
        return (
            "true" if value else "false"
        )  # bool 必须在 int 之前判断，因为 bool 是 int 的子类
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)  # repr 保留足够精度，避免 str() 丢失小数位
    if isinstance(value, str):
        if "\n" in value:
            # 多行字符串使用三引号
            escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
            return f'"""\n{escaped}"""'
        return _toml_escape_string(value)
    if isinstance(value, list):
        return (
            "[" + ", ".join(_toml_format_value(v) for v in value) + "]"
        )  # 递归格式化列表元素
    if isinstance(value, dict):
        items: list[str] = []
        for k in sorted(
            value.keys(), key=lambda x: str(x)
        ):  # 字典键排序以保证输出确定性
            if not isinstance(k, str):
                continue  # TOML 键必须为字符串，跳过非法键
            items.append(f"{_toml_format_key(k)} = {_toml_format_value(value[k])}")
        return "{ " + ", ".join(items) + " }"  # 内联表（inline table）格式
    if value is None:
        # TOML 不支持 null；用空字符串占位
        return _toml_escape_string("")
    return _toml_escape_string(str(value))  # 其他未知类型转字符串后按基本字符串处理


def _get_section_name(section_model: type[SectionBase], fallback: str) -> str:
    """获取配置节的 TOML 节名称。

    若 ``section_model`` 通过 ``@config_section`` 装饰器注册了名称，则使用该名称；
    否则回退到传入的 ``fallback``（通常为字段名）。
    """
    name = getattr(section_model, "__config_section_name__", None)
    return str(name) if name else fallback


def _eval_default_factory(factory: Any) -> Any:
    """兼容 Pydantic v2 的 default_factory 形态（可能需要 validated_data）。"""

    try:
        return factory()  # 大多数 Pydantic v2 default_factory 不需要参数
    except TypeError:
        return factory({})  # 部分旧形态的 factory 需要一个空字典作为 validated_data


def _iter_sections(config_model: type[ConfigBase]) -> list[_SectionInfo]:
    """遍历 ConfigBase 模型的所有配置节字段，返回有序的 ``_SectionInfo`` 列表。

    按字段在模型中的声明顺序返回，每个元素记录节名、节模型类、是否为列表节
    以及对应字段的 ``default_factory``。
    非 ``SectionBase`` 子类的字段会被忽略。
    """
    sections: list[_SectionInfo] = []
    for field_name, model_field in config_model.model_fields.items():
        annotation = model_field.annotation
        section_model, is_list = _get_section_model_from_annotation(annotation)
        if section_model is not None:  # 跳过非 SectionBase 字段（如普通标量字段）
            sections.append(
                _SectionInfo(
                    name=_get_section_name(
                        section_model, field_name
                    ),  # 优先使用装饰器注册名，回退字段名
                    model=section_model,
                    is_list=is_list,
                    default_factory=model_field.default_factory,  # 保留 factory 以便后续生成默认列表项
                )
            )
    return sections


@dataclass(frozen=True)
class _SectionInfo:
    """单个配置节的元信息，由 ``_iter_sections`` 生成。

    Attributes:
        name: TOML 节名称（来自 ``@config_section`` 或字段名）。
        model: 对应的 ``SectionBase`` 子类。
        is_list: 是否为数组节（``[[section]]``）。
        default_factory: 字段的 Pydantic ``default_factory``，用于生成默认列表项。
    """

    name: str
    model: type[SectionBase]
    is_list: bool
    default_factory: Any = None


def _get_section_model_from_annotation(
    annotation: Any,
) -> tuple[type[SectionBase] | None, bool]:
    """从字段类型注解中提取 SectionBase 子类及是否为列表节。

    - 若注解直接是 ``SectionBase`` 的子类，返回 ``(model, False)``。
    - 若注解为 ``list[SectionBase子类]``，返回 ``(model, True)``。
    - 其他情况返回 ``(None, False)``。
    """
    if isinstance(annotation, type) and issubclass(annotation, SectionBase):
        return annotation, False  # 直接子类，单节（[section]）

    origin = get_origin(annotation)  # 提取泛型原始类型，如 list[X] → list
    if origin is list:
        args = get_args(annotation)  # 获取泛型参数，如 list[ModelA] → (ModelA,)
        if args:
            item = args[0]
            if isinstance(item, type) and issubclass(item, SectionBase):
                return item, True  # list[SectionBase子类]，数组节（[[section]]）

    return None, False  # 普通字段，不是配置节


def _merge_with_model_defaults(
    config_model: type[ConfigBase],
    raw: TOMLData,
) -> ConfigData:
    """将 raw 与模型默认值合并，并移除模型未定义的节/字段。

    保留 raw 中能通过字段类型校验的值；不合法的值回退到默认值/占位值。
    """

    merged: dict[str, Any] = {}
    for section in _iter_sections(config_model):
        raw_section = raw.get(section.name)  # 从原始 TOML 数据中取出对应节的内容

        if section.is_list:
            items_out: list[dict[str, Any]] = []
            if isinstance(raw_section, list) and len(raw_section) > 0:
                # 用户已有数据：逐项合并，保留用户值
                for item in raw_section:
                    if not isinstance(item, dict):
                        continue  # 忽略格式不合法的列表项
                    items_out.append(_merge_section_fields(section.model, item))
            elif section.default_factory is not None:
                # 首次创建或 raw 中无此节：从字段 default_factory 获取默认列表项
                default_list = _eval_default_factory(section.default_factory)
                if isinstance(default_list, list):
                    for default_item in default_list:
                        if isinstance(default_item, SectionBase):
                            items_out.append(
                                default_item.model_dump()
                            )  # Pydantic 模型转字典
                        elif isinstance(default_item, dict):
                            items_out.append(default_item)
            merged[section.name] = items_out
            continue

        if not isinstance(raw_section, dict):
            raw_section = {}  # 节缺失或类型不对时用空字典兜底，确保后续合并正常进行

        merged[section.name] = _merge_section_fields(section.model, raw_section)

    return merged


def _merge_section_fields(
    section_model: type[SectionBase],
    raw_section: dict[str, Any],
) -> dict[str, Any]:
    """将单个配置节的原始数据与模型字段定义合并。

    对每个字段：
    - 若字段为嵌套 ``SectionBase``（单个或列表），递归合并。
    - 若 ``raw_section`` 中存在该字段且能通过类型校验，则保留用户值。
    - 否则使用字段的 ``default`` / ``default_factory``，最后回退到类型占位值。

    默认返回严格按模型字段顺序组装的字典，不含模型未定义的键。
    若 section_model 声明了 ``__config_extra_section_model__``，则会额外保留
    raw_section 中未定义的字典子节，并按该模型补齐默认字段。
    """
    section_out: dict[str, Any] = {}
    for key, field in section_model.model_fields.items():
        annotation = field.annotation
        nested_model, is_list = _get_section_model_from_annotation(
            annotation
        )  # 判断字段是否为嵌套节

        if nested_model is not None:
            if is_list:
                raw_list = raw_section.get(key)
                items_out: list[dict[str, Any]] = []
                if isinstance(raw_list, list) and len(raw_list) > 0:
                    # 用户已有数据：逐项合并
                    for item in raw_list:
                        if not isinstance(item, dict):
                            continue  # 跳过非字典元素（格式错误的列表项）
                        items_out.append(_merge_section_fields(nested_model, item))
                elif field.default_factory is not None:
                    # 无数据时从字段 default_factory 获取默认列表项
                    default_list = _eval_default_factory(field.default_factory)
                    if isinstance(default_list, list):
                        for default_item in default_list:
                            if isinstance(default_item, SectionBase):
                                items_out.append(
                                    default_item.model_dump()
                                )  # 模型实例转字典
                            elif isinstance(default_item, dict):
                                items_out.append(default_item)
                section_out[key] = items_out
            else:
                raw_nested = raw_section.get(key)
                if not isinstance(raw_nested, dict) or len(raw_nested) == 0:
                    # 无用户数据时，优先使用当前字段的 default_factory 提供的完整对象
                    if field.default_factory is not None:
                        default_obj = _eval_default_factory(field.default_factory)
                        if isinstance(default_obj, SectionBase):
                            section_out[key] = (
                                default_obj.model_dump()
                            )  # 模型实例 → 字典
                            continue
                        elif isinstance(default_obj, dict):
                            section_out[key] = default_obj  # 直接使用字典形态的默认值
                            continue
                    # 回退到原逻辑：用空字典递归合并，确保所有字段都被填充占位值
                    raw_nested = raw_nested if isinstance(raw_nested, dict) else {}
                section_out[key] = _merge_section_fields(nested_model, raw_nested)
            continue

        # 获取字段默认值：优先使用 ``default_factory`` 生成的值（如果存在且能成功生成），否则使用 ``default``（如果合法），最后回退到类型占位值。
        default_value = (
            field.default
            if field.default is not None
            and field.default is not ...  # Pydantic 用 ... 表示必填无默认
            and field.default
            is not PydanticUndefined  # PydanticUndefined 也表示无默认值
            else None
        )
        if field.default_factory is not None:
            try:
                default_value = _eval_default_factory(
                    field.default_factory
                )  # factory 优先级高于 default
            except Exception:
                default_value = None  # factory 调用失败时降级，不中断整体合并

        if key in raw_section:
            candidate = raw_section[key]
            try:
                # 用 TypeAdapter 做严格类型校验，校验通过则直接使用用户值
                section_out[key] = TypeAdapter(annotation).validate_python(candidate)
                continue
            except Exception:
                pass  # 校验失败：用户值类型不符，回落到下方的默认值/占位值逻辑

        if default_value is not None:
            section_out[key] = default_value  # 使用模型默认值
        else:
            section_out[key] = _placeholder_for_type(
                annotation
            )  # 最终兜底：按类型生成占位值

    extra_section_model = getattr(section_model, "__config_extra_section_model__", None)
    if isinstance(extra_section_model, type) and issubclass(extra_section_model, SectionBase):
        for key, value in raw_section.items():
            if key in section_model.model_fields:
                continue
            raw_extra = value if isinstance(value, dict) else {}
            section_out[key] = _merge_section_fields(extra_section_model, raw_extra)

    return section_out


def _placeholder_for_type(annotation: Any) -> Any:
    """按类型注解生成占位值，用于必填字段缺失默认值时的兜底处理。

    规则：
    - ``list[...]`` → ``[]``
    - ``dict[...]`` → ``{}``
    - ``Optional[T]`` / ``T | None`` → 递归生成 ``T`` 的占位值
    - ``str`` → ``""``，``int`` → ``0``，``float`` → ``0.0``，``bool`` → ``False``
    - 其他未知类型 → ``""``（空字符串）
    """
    origin = get_origin(annotation)  # 取泛型原始类型，如 list[str] → list
    args = get_args(annotation)  # 取泛型参数，如 list[str] → (str,)

    if origin is list:
        return []
    if origin is dict:
        return {}

    if args and type(None) in args:
        # Optional[T] 或 T | None：取第一个非 None 类型递归生成占位值
        for arg in args:
            if arg is type(None):
                continue
            return _placeholder_for_type(arg)

    if annotation in (str,):
        return ""
    if annotation in (int,):
        return 0
    if annotation in (float,):
        return 0.0
    if annotation in (bool,):
        return False
    if annotation is list:
        return []  # 无参裸 list（未声明元素类型）
    if annotation is dict:
        return {}  # 无参裸 dict
    return ""  # 无法识别的类型，最终兜底返回空字符串


def _render_toml_with_signature(
    config_model: type[ConfigBase],
    data: ConfigData,
) -> str:
    """按模型签名生成带注释的 TOML（确定性输出）。"""

    lines: list[str] = []
    sections = _iter_sections(config_model)

    for idx, section in enumerate(sections):
        if idx != 0:
            lines.append("")  # 在相邻两个顶级节之间插入空行，提升可读性

        # 获取当前节的数据字典，供字段值渲染使用；若类型不符则回退到空字典以避免渲染错误
        section_data = data.get(section.name)
        if section.is_list:
            items: list[dict[str, Any]] = []
            if isinstance(section_data, list):
                items = [
                    item for item in section_data if isinstance(item, dict)
                ]  # 过滤掉格式异常的元素
            if not items:
                items = [
                    _merge_section_fields(section.model, {})
                ]  # 列表为空时补一个默认项，确保 TOML 文件有示例

            for item_idx, item in enumerate(items):
                _render_section_block(
                    lines,
                    section.name,
                    section.model,
                    item,
                    is_list=True,
                    include_doc=(
                        item_idx == 0
                    ),  # 仅第一个列表项前写节文档注释，避免重复
                )
                if item_idx != len(items) - 1:
                    lines.append("")  # 同一数组节的相邻条目间插入空行
            continue

        if not isinstance(section_data, dict):
            section_data = {}  # 节数据缺失时用空字典，后续渲染用占位值填充

        _render_section_block(
            lines,
            section.name,
            section.model,
            section_data,
            is_list=False,
            include_doc=True,
        )

    while lines and lines[-1] == "":
        lines.pop()  # 去除文件末尾多余空行

    return "\n".join(lines).rstrip() + "\n"  # 确保文件以单个换行符结尾


def _render_section_block(
    lines: list[str],
    section_name: str,
    section_model: type[SectionBase],
    section_data: dict[str, Any],
    *,
    is_list: bool,
    include_doc: bool,
) -> None:
    """将一个配置节渲染为 TOML 行并追加到 ``lines``。

    Args:
        lines: 目标行列表，结果直接 append 进去（in-place 修改）。
        section_name: TOML 节名，如 ``"database"`` 或 ``"server.tls"``。
        section_model: 该节对应的 ``SectionBase`` 子类，用于读取字段元信息。
        section_data: 该节当前的数据字典，字段值从中取出后格式化写入。
        is_list: 若为 ``True`` 则生成 ``[[section_name]]``；否则生成 ``[section_name]``。
        include_doc: 是否在节头前写入 section_model 的 docstring 注释行。

    对嵌套 ``SectionBase`` 字段会递归调用自身以生成子节块。
    每个叶子字段会输出字段描述注释、类型 + 默认值签名注释，以及键值行。
    """
    if include_doc:
        # 节级文档注释来自 section_model 的 docstring；字段级文档注释来自 Field.description。
        section_doc = inspect.getdoc(section_model) or ""  # getdoc 会自动去除缩进
        if section_doc:
            for doc_line in section_doc.splitlines():
                lines.append(f"# {doc_line}")  # 每行文档前加 # 前缀写入 TOML 注释

    if is_list:
        lines.append(f"[[{section_name}]]")  # TOML 数组表头
    else:
        lines.append(f"[{section_name}]")  # TOML 普通表头

    for field_name, field in section_model.model_fields.items():
        annotation = field.annotation
        nested_model, nested_is_list = _get_section_model_from_annotation(annotation)

        if nested_model is not None:
            # 字段是嵌套节，递归渲染为子表（子节名 = 父节名.字段名）
            nested_data = section_data.get(field_name)
            if nested_is_list:
                nested_items: list[dict[str, Any]] = []
                if isinstance(nested_data, list):
                    nested_items = [
                        item for item in nested_data if isinstance(item, dict)
                    ]
                if not nested_items:
                    nested_items = [_merge_section_fields(nested_model, {})]  # 补默认项

                for nested_idx, item in enumerate(nested_items):
                    lines.append("")  # 嵌套数组节前插入空行
                    _render_section_block(
                        lines,
                        f"{section_name}.{field_name}",  # 生成 [[parent.child]] 格式的节名
                        nested_model,
                        item,
                        is_list=True,
                        include_doc=(nested_idx == 0),  # 仅第一项前写注释
                    )
                continue

            if not isinstance(nested_data, dict):
                nested_data = {}  # 嵌套节数据缺失时用空字典兜底
            lines.append("")  # 嵌套普通节前插入空行
            _render_section_block(
                lines,
                f"{section_name}.{field_name}",  # 生成 [parent.child] 格式的节名
                nested_model,
                nested_data,
                is_list=False,
                include_doc=True,
            )
            continue

        # 叶子字段：先写 Field.description 注释，再写类型/默认值签名，最后写键值行
        description = field.description or ""
        if description:
            for doc_line in description.splitlines():
                lines.append(f"# {doc_line}")  # 字段描述注释

        type_text = _type_repr(annotation)  # 将注解转为可读字符串，写入签名注释

        default_text = None
        if field.default_factory is not None:
            try:
                default_text = _toml_format_value(
                    _eval_default_factory(field.default_factory)
                )  # 调用 factory 取默认值并序列化
            except Exception:
                default_text = None  # factory 调用失败时不显示默认值
        elif (
            field.default is not None
            and field.default is not ...
            and field.default is not PydanticUndefined
        ):
            default_text = _toml_format_value(field.default)  # 静态默认值序列化

        sig_parts = [f"值类型：{type_text}"]
        if default_text is not None:
            # 多行默认值不能嵌进单行注释，只显示第一行内容并加 ... 省略
            # TOML 多行字符串格式为 '"""\n{内容}"""'，第 0 行是 '"""'，第 1 行才是第一行内容
            if "\n" in default_text:
                parts = default_text.split("\n")
                first_line = parts[1] if len(parts) > 1 else ""
                display_default = f'"{first_line}..."'
            else:
                display_default = default_text
            sig_parts.append(f"默认值：{display_default}")
        else:
            sig_parts.append("默认值：<必填>")  # 无默认值的必填字段标记

        lines.append("# " + ", ".join(sig_parts))  # 类型+默认值签名注释行

        value = section_data.get(field_name)  # 从数据字典取当前字段的值
        lines.append(
            f"{field_name} = {_toml_format_value(value)}"
        )  # 写出 key = value 行
        lines.append("")  # 字段间插入空行，提升可读性

    extra_section_model = getattr(section_model, "__config_extra_section_model__", None)
    if isinstance(extra_section_model, type) and issubclass(extra_section_model, SectionBase):
        for extra_name, extra_data in section_data.items():
            if extra_name in section_model.model_fields or not isinstance(extra_data, dict):
                continue
            lines.append("")
            _render_section_block(
                lines,
                f"{section_name}.{extra_name}",
                extra_section_model,
                extra_data,
                is_list=False,
                include_doc=True,
            )

    while lines and lines[-1] == "":
        lines.pop()  # 去除节尾多余空行（节间空行由上层渲染逻辑统一管理）

    while lines and lines[-1] == "":
        lines.pop()  # 双重保险，确保彻底清除连续空行
