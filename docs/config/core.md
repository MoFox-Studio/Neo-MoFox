# Config 模块实现原理

## 概述

本文档深入探讨 Config 模块的内部实现细节，包括配置加载、验证、自动更新和 TOML 生成机制。

---

## 核心概念

### 静态可见性（Static Visibility）

Config 模块采用"静态可见"的设计，这意味着：

1. **配置节在类体中显式声明** - 所有配置节都作为 ConfigBase 的字段显式定义
2. **IDE 友好** - Pylance 和其他 IDE 能正确推断字段类型
3. **类型安全** - 对错误的字段访问会被发现

**对比示例**：

```python
# ✓ 静态可见 - IDE 能推断类型
class AppConfig(ConfigBase):
    general: GeneralSection = Field(default_factory=GeneralSection)

config = AppConfig.load("config.toml")
config.general.debug  # ✓ IDE 能识别此字段

# ✗ 动态添加 - IDE 无法推断
class AppConfig(ConfigBase):
    pass

setattr(AppConfig, "general", Field(default_factory=GeneralSection))
config = AppConfig.load("config.toml")
config.general.debug  # ✗ IDE 无法识别
```

---

## 配置加载流程

### 1. 从 TOML 文件加载

```python
config = MyConfig.load("config.toml", auto_update=False)
```

**流程**：

```
TOML 文件读取
    ↓
tomllib.load() 解析
    ↓
TOMLData (dict)
    ↓
Pydantic 验证
    ↓
ConfigBase 实例
```

**代码流程**：

```python
@classmethod
def load(cls, path: str | Path, *, auto_update: bool = False) -> Self:
    path = Path(path)
    
    # 1. 读取文件内容
    original_text = path.read_text(encoding="utf-8")
    
    # 2. 用 tomllib 解析 TOML
    with path.open("rb") as f:
        raw = tomllib.load(f)  # TOMLData
    
    # 3. 不需要自动更新时直接转换
    if not auto_update:
        return cls.from_dict(raw)
    
    # 4. 需要自动更新时进行合并和重写
    merged = _merge_with_model_defaults(cls, raw)
    new_text = _render_toml_with_signature(cls, merged)
    
    # 5. 如果内容改变，回写文件
    if _normalize_newlines(original_text) != _normalize_newlines(new_text):
        path.write_text(new_text, encoding="utf-8")
    
    # 6. 返回配置对象
    return cls.from_dict(merged)
```

### 2. 从字典加载

```python
config = MyConfig.from_dict(config_data)
```

**流程**：

```
ConfigData (dict)
    ↓
Pydantic 验证
    ↓
类型转换
    ↓
ConfigBase 实例
```

**代码**：

```python
@classmethod
def from_dict(cls, data: ConfigData) -> Self:
    return cls.model_validate(data)
```

---

## 配置节的自动发现

### _iter_sections() 函数

遍历 ConfigBase 类的所有配置节字段。

```python
def _iter_sections(config_model: type[ConfigBase]) -> list[tuple[str, type[SectionBase]]]:
    sections: list[tuple[str, type[SectionBase]]] = []
    
    # 遍历所有字段
    for field_name, model_field in config_model.model_fields.items():
        annotation = model_field.annotation
        
        # 检查字段是否是 SectionBase 子类
        if isinstance(annotation, type) and issubclass(annotation, SectionBase):
            # 获取节名称
            section_name = _get_section_name(annotation, field_name)
            sections.append((section_name, annotation))
    
    # 按节名称排序（确定性输出）
    sections.sort(key=lambda x: x[0])
    return sections
```

**获取节名称**：

```python
def _get_section_name(section_model: type[SectionBase], fallback: str) -> str:
    # 从 @config_section 装饰器中获取名称
    name = getattr(section_model, "__config_section_name__", None)
    return str(name) if name else fallback
```

---

## 配置合并和验证

### _merge_with_model_defaults() 函数

将原始 TOML 数据与模型定义的默认值合并，并进行类型验证。

```python
def _merge_with_model_defaults(
    config_model: type[ConfigBase],
    raw: TOMLData,
) -> ConfigData:
    merged: ConfigData = {}
    
    # 遍历每个配置节
    for section_name, section_model in _iter_sections(config_model):
        raw_section = raw.get(section_name)
        if not isinstance(raw_section, dict):
            raw_section = {}
        
        section_out: dict[str, Any] = {}
        
        # 遍历每个字段
        for key, field in section_model.model_fields.items():
            annotation = field.annotation
            
            # 1. 获取默认值
            default_value = (
                field.default
                if field.default is not None and field.default is not ...
                else None
            )
            if field.default_factory is not None:
                try:
                    default_value = _eval_default_factory(field.default_factory)
                except Exception:
                    default_value = None
            
            # 2. 优先使用原始值（经过类型验证）
            if key in raw_section:
                candidate = raw_section[key]
                try:
                    # 使用 TypeAdapter 进行类型验证
                    section_out[key] = TypeAdapter(annotation).validate_python(candidate)
                    continue
                except Exception:
                    pass  # 验证失败，使用默认值或占位值
            
            # 3. 使用默认值或占位值
            if default_value is not None:
                section_out[key] = default_value
            else:
                section_out[key] = _placeholder_for_type(annotation)
        
        merged[section_name] = section_out
    
    return merged
```

### 占位值生成

```python
def _placeholder_for_type(annotation: Any) -> Any:
    """为没有默认值的字段生成占位值"""
    
    origin = getattr(annotation, "__origin__", None)
    
    if annotation in (str,):
        return ""
    if annotation in (int,):
        return 0
    if annotation in (float,):
        return 0.0
    if annotation in (bool,):
        return False
    if origin is list or annotation is list:
        return []
    if origin is dict or annotation is dict:
        return {}
    
    return ""  # 默认占位值
```

---

## TOML 格式生成

### _render_toml_with_signature() 函数

根据模型定义生成带完整注释的 TOML 文件。

```python
def _render_toml_with_signature(
    config_model: type[ConfigBase],
    data: ConfigData,
) -> str:
    """生成带签名的 TOML 文本"""
    
    lines: list[str] = []
    sections = _iter_sections(config_model)
    
    # 遍历每个配置节
    for idx, (section_name, section_model) in enumerate(sections):
        if idx != 0:
            lines.append("")  # 节之间添加空行
        
        # 1. 添加节的文档
        section_doc = inspect.getdoc(section_model) or ""
        if section_doc:
            for doc_line in section_doc.splitlines():
                lines.append(f"# {doc_line}")
        
        # 2. 添加节头
        lines.append(f"[{section_name}]")
        
        # 3. 添加每个字段
        section_data = data.get(section_name, {})
        
        for field_name, field in section_model.model_fields.items():
            # a) 字段文档
            description = field.description or ""
            if description:
                for doc_line in description.splitlines():
                    lines.append(f"# {doc_line}")
            
            # b) 字段签名（类型和默认值）
            annotation = field.annotation
            type_text = _type_repr(annotation)
            
            sig_parts = [f"type={type_text}"]
            
            # 获取默认值
            if field.default_factory is not None:
                try:
                    default_text = _toml_format_value(
                        _eval_default_factory(field.default_factory)
                    )
                    sig_parts.append(f"default={default_text}")
                except Exception:
                    sig_parts.append("default=<required>")
            elif field.default is not None and field.default is not ...:
                default_text = _toml_format_value(field.default)
                sig_parts.append(f"default={default_text}")
            else:
                sig_parts.append("default=<required>")
            
            lines.append("# signature: " + ", ".join(sig_parts))
            
            # c) 字段值
            value = section_data.get(field_name)
            lines.append(f"{field_name} = {_toml_format_value(value)}")
            lines.append("")
        
        # 清理末尾空行
        while lines and lines[-1] == "":
            lines.pop()
    
    return "\n".join(lines).rstrip() + "\n"
```

### TOML 值格式化

```python
def _toml_format_value(value: Any) -> str:
    """将 Python 值格式化为 TOML 格式"""
    
    if isinstance(value, bool):
        return "true" if value else "false"
    
    if isinstance(value, int):
        return str(value)
    
    if isinstance(value, float):
        return repr(value)
    
    if isinstance(value, str):
        return _toml_escape_string(value)
    
    if isinstance(value, list):
        return "[" + ", ".join(_toml_format_value(v) for v in value) + "]"
    
    if isinstance(value, dict):
        # 内联表格式
        items: list[str] = []
        for k in sorted(value.keys(), key=lambda x: str(x)):
            if not isinstance(k, str):
                continue
            items.append(f"{_toml_format_key(k)} = {_toml_format_value(value[k])}")
        return "{ " + ", ".join(items) + " }"
    
    if value is None:
        # TOML 不支持 null，使用空字符串
        return _toml_escape_string("")
    
    return _toml_escape_string(str(value))
```

---

## 自动更新机制

### 更新流程

1. **读取原始文件** - 保存用于对比
2. **解析 TOML** - 获取原始数据
3. **合并默认值** - 使用模型定义补充缺失字段
4. **生成新 TOML** - 根据合并后的数据重新生成
5. **对比和回写** - 如果内容改变则覆盖文件

**示例**：

```
原始 config.toml：
[general]
debug = false
# app_name 字段缺失

↓ auto_update=True

[general]
debug = false
app_name = "MyApp"  # 自动添加默认值
# 包含完整的签名注释
```

### 保持用户值

自动更新时会尽可能保留用户在配置文件中的值：

```python
if key in raw_section:
    candidate = raw_section[key]
    try:
        # 验证类型
        section_out[key] = TypeAdapter(annotation).validate_python(candidate)
        continue
    except Exception:
        pass  # 类型不匹配，使用默认值
```

---

## 装饰器实现

### @config_section 装饰器

```python
def config_section(name: str) -> Callable[[type[SectionT]], type[SectionT]]:
    """配置节装饰器
    
    将节名称存储在类属性中，以便后续查询。
    """
    
    def decorator(cls: type[SectionT]) -> type[SectionT]:
        # 设置节名称
        cls.__config_section_name__ = name  # type: ignore[attr-defined]
        return cls
    
    return decorator
```

**使用**：

```python
@config_section("database")
class DatabaseSection(SectionBase):
    pass

# 等价于
DatabaseSection.__config_section_name__ = "database"
```

---

## 类型转换

### TypeAdapter

使用 Pydantic 的 TypeAdapter 进行灵活的类型转换和验证：

```python
from pydantic import TypeAdapter

# 验证列表
adapter = TypeAdapter(list[str])
result = adapter.validate_python(["a", "b"])  # ✓ ["a", "b"]

# 验证字典
adapter = TypeAdapter(dict[str, int])
result = adapter.validate_python({"a": 1})  # ✓ {"a": 1}
result = adapter.validate_python({"a": "1"})  # ✗ 异常
```

---

## 性能特性

### 1. 确定性输出

所有 TOML 生成都是确定性的（相同输入产生相同输出）：

```python
# 字段按字母顺序排列
sections.sort(key=lambda x: x[0])

# 键按字母顺序排列
for k in sorted(value.keys(), key=lambda x: str(x)):
    pass
```

### 2. 增量更新

只在内容改变时回写文件：

```python
if _normalize_newlines(original_text) != _normalize_newlines(new_text):
    path.write_text(new_text, encoding="utf-8")
```

### 3. 换行符规范化

处理不同操作系统的换行符差异：

```python
def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
```

---

## 错误处理

### 类型验证异常

```python
try:
    # 尝试验证类型
    section_out[key] = TypeAdapter(annotation).validate_python(candidate)
except Exception:
    # 验证失败，使用默认值
    section_out[key] = default_value or _placeholder_for_type(annotation)
```

### 默认值工厂异常

```python
def _eval_default_factory(factory: Any) -> Any:
    try:
        return factory()
    except TypeError:
        # Pydantic v2 的特殊处理
        return factory({})
    except Exception:
        return None
```

---

## 扩展性

### 自定义验证器

使用 Pydantic 的 @field_validator：

```python
from pydantic import field_validator

@config_section("server")
class ServerSection(SectionBase):
    port: int
    
    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("端口范围错误")
        return v
```

### 自定义字段类型

```python
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

class CustomType:
    """自定义配置字段类型"""
    
    def __init__(self, value: str):
        self.value = value
    
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.chain_schema([
            core_schema.str_schema(),
            core_schema.no_info_plain_validator_function(cls),
        ])
```

---

## 相关资源

- [Config 主文档](./README.md) - 使用指南
- [类型系统](./types.md) - 类型定义
- [高级用法](./advanced.md) - 自定义和最佳实践
