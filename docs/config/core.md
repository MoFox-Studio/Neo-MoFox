# Config 模块实现原理

## 概述

本文档对应 `src/kernel/config/core.py` 的当前实现，说明 Config 模块如何进行：

1. 配置节发现（含普通节、数组节、嵌套节）
2. 配置加载与自动回写
3. 字段校验、默认值填充与占位值兜底
4. 带注释 TOML 的确定性渲染

---

## 设计目标

### 静态可见

配置采用“静态可见”模型：配置节必须在 `ConfigBase` 子类中显式声明，保证 IDE 能推断类型。

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field


class AppConfig(ConfigBase):
    @config_section("general", title="通用设置")
    class GeneralSection(SectionBase):
        enabled: bool = Field(default=True, description="是否启用")

    general: GeneralSection = Field(default_factory=GeneralSection)
```

### 严格结构

`ConfigBase` 与 `SectionBase` 均使用 `ConfigDict(extra="forbid")`，未声明字段不会被静默接收。

---

## 加载流程

### `ConfigBase.load(path, auto_update=False)`

执行步骤：

1. 将 `path` 转为 `Path`
2. 若文件不存在：自动创建父目录与空文件
3. 使用 `tomllib.load()` 读取 TOML
4. `auto_update=False`：直接 `from_dict(raw)`
5. `auto_update=True`：
6. 调用 `_merge_with_model_defaults()` 生成合并结果
7. 调用 `_render_toml_with_signature()` 生成标准化 TOML
8. 仅当内容变更时写回
9. 返回 `from_dict(merged)`

关键点：

- 文件缺失不会抛 `FileNotFoundError`，会自动创建
- 回写前会做换行规范化比较，避免无效写盘

```python
config = MyConfig.load("config/app.toml", auto_update=True)
```

### `ConfigBase.from_dict(data)`

直接调用 `model_validate(data)`，由 Pydantic 完成类型验证和转换。

---

## 配置节发现

### `_iter_sections()`

`_iter_sections()` 会扫描 `config_model.model_fields`，识别两类配置节字段：

1. `SectionBase` 子类：普通节
2. `list[SectionBase 子类]`：数组节

返回 `_SectionInfo` 列表，包含：

- `name`：节名（优先取 `@config_section` 指定名）
- `model`：节模型类
- `is_list`：是否数组节
- `default_factory`：字段默认工厂

节顺序采用“声明顺序”，不做字母排序。

---

## 合并策略

### `_merge_with_model_defaults()`

该函数将原始 TOML 数据与模型默认值合并，输出稳定结构：

1. 仅保留模型声明的节与字段
2. 用户值优先，但必须通过 `TypeAdapter(annotation).validate_python()`
3. 校验失败则回退到默认值或占位值
4. 支持普通节、数组节与嵌套节递归合并

### `_merge_section_fields()`

字段处理优先级：

1. 有效用户值
2. `default_factory` 生成值
3. `default` 静态默认值
4. `_placeholder_for_type()` 占位值

### `_placeholder_for_type()`

典型兜底规则：

- `str -> ""`
- `int -> 0`
- `float -> 0.0`
- `bool -> False`
- `list[...] -> []`
- `dict[...] -> {}`
- `Optional[T] -> T` 的占位值

---

## TOML 渲染

### `_render_toml_with_signature()`

渲染输出特征：

1. 节级注释来自 `SectionBase` 的 docstring
2. 字段注释来自 `Field(description=...)`
3. 字段签名注释格式为：`# 值类型：..., 默认值：...`
4. 普通节使用 `[section]`
5. 数组节使用 `[[section]]`
6. 嵌套节使用 `[parent.child]` 或 `[[parent.child]]`
7. 输出末尾固定单个换行

示例：

```toml
# 数据库配置
[database]
# 端口
# 值类型：int, 默认值：5432
port = 5432

# 规则列表
[[rules]]
# 名称
# 值类型：str, 默认值：""
name = ""
```

### `_toml_format_value()`

支持类型：

1. `bool/int/float/str`
2. `list`
3. `dict`（内联表）
4. 多行字符串（三引号）

注意：TOML 不支持 `null`，`None` 会被写成空字符串。

---

## 元数据装饰与增强字段

### `@config_section(...)`

除 `name` 外，还支持：

- `title`
- `description`
- `tag`
- `order`

这些元数据会写入类属性，供上层 WebUI/配置管理能力使用。

### `Field(...)`

`src.kernel.config.Field` 是对 Pydantic Field 的增强封装：

1. 保留常见验证参数（`ge/le/min_length/pattern` 等）
2. 支持 UI 元信息（`label/input_type/placeholder/depends_on` 等）
3. 通过 `json_schema_extra` 挂载 UI 属性

---

## 稳定性与性能

1. 仅在文本变化时写回文件
2. 比较前会统一换行符（CRLF/CR/LF）
3. 对默认工厂调用做了兼容封装（支持 `factory()` 与 `factory({})`）
4. 渲染过程会剔除无效空行，保证输出稳定

---

## 相关资源

- [Config 主文档](./README.md) - 使用指南
- [类型系统](./types.md) - 类型定义
- [示例代码](../../examples/src/kernel/config/config_example.py) - 可运行示例
