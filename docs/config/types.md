# Config 模块类型系统

## 概述

Config 模块在 `src/kernel/config/types.py` 中定义了 3 个公开类型别名，用于描述配置读写数据形态。

```python
ConfigData: TypeAlias = dict[str, dict[str, Any]]
SectionData: TypeAlias = dict[str, Any]
TOMLData: TypeAlias = dict[str, Any]
```

---

## 类型定义

### ConfigData

表示“配置对象输入/输出”的主类型。

```python
ConfigData: TypeAlias = dict[str, dict[str, Any]]
```

语义：

1. 外层 key：节名（如 `general`、`database`）
2. 外层 value：该节的键值对（`SectionData`）

示例：

```python
config_data: ConfigData = {
    "general": {
        "debug": True,
        "app_name": "Neo-MoFox",
    },
    "database": {
        "host": "localhost",
        "port": 5432,
    },
}
```

### SectionData

表示单个节的键值字典。

```python
SectionData: TypeAlias = dict[str, Any]
```

示例：

```python
database_section: SectionData = {
    "host": "localhost",
    "port": 5432,
}
```

### TOMLData

表示 `tomllib.load()` 解析后的原始 TOML 数据。

```python
TOMLData: TypeAlias = dict[str, Any]
```

示例：

```python
import tomllib

with open("config/app.toml", "rb") as f:
    raw: TOMLData = tomllib.load(f)
```

---

## 类型与运行时行为

`ConfigData` 是当前公开 API 的简明类型，适合大多数“普通节”场景。

在运行时，`core.py` 还支持：

1. 数组节（`list[SectionBase]`，TOML 形态 `[[section]]`）
2. 嵌套节（`[parent.child]`）

因此当你处理复杂节结构时，具体字段值通常通过 `Any` 向下兼容，最终由 Pydantic 验证。

---

## 与 Pydantic 的关系

### `from_dict()`

`ConfigBase.from_dict(data)` 直接调用 `model_validate(data)`，因此类型安全主要由 Pydantic 保证。

```python
cfg = MyConfig.from_dict(config_data)
```

### `extra="forbid"`

`ConfigBase` 与 `SectionBase` 都设置了：

```python
model_config = ConfigDict(extra="forbid")
```

含义：未声明字段会被拒绝，不会悄悄混入配置对象。

---

## 实践建议

1. 业务边界层优先使用 `ConfigData` / `SectionData` 注解，提升可读性。
2. 复杂输入先交给 `from_dict()` 或 `load()`，避免手写转换逻辑。
3. 对外暴露接口时，不要把 `dict[str, dict[str, Any]]` 直接写在签名里，优先用别名。

示例：

```python
from src.kernel.config import ConfigData


def load_app_config(data: ConfigData) -> AppConfig:
    return AppConfig.from_dict(data)
```

---

## 常见误区

1. 误区：`src.kernel.config.Field` 等同于 `pydantic.Field`。
2. 事实：config 模块导出的 `Field` 是增强版封装，包含 UI 元数据参数。

1. 误区：`TOMLData` 一定与最终配置对象结构完全一致。
2. 事实：原始 TOML 可能包含多余节、错误类型，最终以 Pydantic 校验和合并结果为准。

---

## 相关资源

- [Config 主文档](./README.md) - 使用指南
- [实现原理](./core.md) - 加载、合并、渲染细节
- [示例代码](../../examples/src/kernel/config/config_example.py) - 可运行示例
