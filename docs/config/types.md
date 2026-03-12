# Config 模块类型系统

## 概述

Config 模块定义了一套类型系统，用于表示配置数据、配置节和 TOML 格式。这些类型提高了代码的可读性和类型安全性。

---

## 类型定义

### ConfigData

表示整个配置的数据结构。

```python
ConfigData: TypeAlias = dict[str, dict[str, Any]]
```

**结构**：
```
{
    "section_name": {
        "field_name": value,
        "field_name": value,
        ...
    },
    "section_name": {
        ...
    }
}
```

**使用示例**：

```python
config_data: ConfigData = {
    "general": {
        "debug": True,
        "app_name": "MyApp",
        "workers": 4
    },
    "database": {
        "host": "localhost",
        "port": 5432,
        "username": "admin"
    },
    "cache": {
        "enabled": True,
        "ttl": 3600
    }
}

# 从字典加载配置
config = MyConfig.from_dict(config_data)
```

**特点**：
- 嵌套字典结构
- 外层键是配置节名称
- 内层键是字段名称
- 值可以是任何 Python 对象

---

### SectionData

表示单个配置节的数据。

```python
SectionData: TypeAlias = dict[str, Any]
```

**结构**：
```
{
    "field_name": value,
    "field_name": value,
    ...
}
```

**使用示例**：

```python
section_data: SectionData = {
    "host": "localhost",
    "port": 5432,
    "username": "admin",
    "password": "secret"
}

# 通常作为 ConfigData 的值
config_data: ConfigData = {
    "database": section_data
}
```

**特点**：
- 平面字典结构
- 键是字段名称
- 值是字段值

---

### TOMLData

表示 TOML 文件解析后的原始数据。

```python
TOMLData: TypeAlias = dict[str, Any]
```

**结构**：与 TOML 文件结构相同

**使用示例**：

```python
import tomllib
from pathlib import Path

# 从 TOML 文件解析原始数据
with open("config.toml", "rb") as f:
    toml_data: TOMLData = tomllib.load(f)

# 通常转换为 ConfigData
config = MyConfig.from_dict(toml_data)
```

**特点**：
- 直接来自 TOML 解析器
- 可能包含模型未定义的字段
- 需要进行类型验证

---

## 类型转换

### TOML 数据类型映射

| TOML 类型 | Python 类型 | ConfigData |
|----------|-----------|-----------|
| `true` / `false` | `bool` | `True` / `False` |
| `42` | `int` | `42` |
| `3.14` | `float` | `3.14` |
| `"hello"` | `str` | `"hello"` |
| `[1, 2, 3]` | `list` | `[1, 2, 3]` |
| `{a = 1}` | `dict` | `{"a": 1}` |

---

## 使用模式

### 模式 1: 从不同来源加载配置

```python
from src.kernel.config import ConfigBase, Field, SectionBase, config_section
from typing import TypeAlias
import json
import tomllib

ConfigData: TypeAlias = dict[str, dict[str, Any]]

class AppConfig(ConfigBase):
    # ... 配置定义
    pass

# 方式 1: 从 TOML 文件加载
config1 = AppConfig.load("config.toml")

# 方式 2: 从字典加载（可来自 JSON、环境变量等）
config_dict: ConfigData = {
    "general": {"debug": True},
    "database": {"host": "localhost"}
}
config2 = AppConfig.from_dict(config_dict)

# 方式 3: 从 JSON 文件加载
with open("config.json") as f:
    json_data = json.load(f)
config3 = AppConfig.from_dict(json_data)
```

### 模式 2: 配置数据转换

```python
from src.kernel.config import ConfigBase

class AppConfig(ConfigBase):
    # ... 配置定义
    pass

# 生成默认配置
default_data = AppConfig.default()  # ConfigData

# 可以保存为 JSON
import json
with open("config_template.json", "w") as f:
    json.dump(default_data, f, indent=2)
```

### 模式 3: 类型安全的配置访问

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

@config_section("server")
class ServerSection(SectionBase):
    host: str = Field(default="localhost")
    port: int = Field(default=8000)

class AppConfig(ConfigBase):
    server: ServerSection = Field(default_factory=ServerSection)

# 加载配置
config = AppConfig.load("config.toml")

# 类型安全的访问（IDE 自动补全）
host: str = config.server.host
port: int = config.server.port

# 错误会在开发时被发现
# config.server.unknown_field  # IDE 会提示错误
```

### 模式 4: 配置验证

```python
from pydantic import field_validator, ConfigDict

@config_section("database")
class DatabaseSection(SectionBase):
    host: str
    port: int
    
    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("端口必须在 1-65535 之间")
        return v

# 加载时会自动验证
try:
    config = AppConfig.load("config.toml")
except ValueError as e:
    print(f"配置验证失败: {e}")
```

---

## Pydantic 集成

Config 模块使用 Pydantic 的以下特性：

### BaseModel

```python
class ConfigBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    @classmethod
    def from_dict(cls, data: ConfigData) -> Self:
        return cls.model_validate(data)
```

**特点**：
- 自动类型转换和验证
- 支持自定义验证器
- 支持配置模型行为

### ConfigDict

```python
model_config = ConfigDict(extra="forbid")
```

**含义**：
- `extra="forbid"` - 不允许未定义的字段

### Field

```python
from pydantic import Field

Field(
    default=...,              # 默认值
    default_factory=...,      # 默认值工厂函数
    description="...",        # 字段文档
    validation_alias="...",   # 验证别名
)
```

---

## 类型注解最佳实践

### 1. 显式声明类型

```python
# ✓ 好的做法 - 显式声明类型
from src.kernel.config import ConfigData

config_data: ConfigData = {
    "section": {"field": "value"}
}

# ✗ 不好的做法 - 隐式类型
config_data = {  # type: dict[str, dict[str, Any]]
    "section": {"field": "value"}
}
```

### 2. 使用类型别名

```python
# ✓ 好的做法
from typing import TypeAlias
from src.kernel.config import ConfigData

MyConfigType: TypeAlias = ConfigData

def load_config(data: MyConfigType) -> AppConfig:
    return AppConfig.from_dict(data)

# ✗ 不好的做法
def load_config(data: dict[str, dict[str, Any]]) -> AppConfig:
    return AppConfig.from_dict(data)
```

### 3. 在函数签名中使用类型

```python
# ✓ 好的做法
from src.kernel.config import ConfigData, SectionData

def process_section(section: SectionData) -> None:
    for key, value in section.items():
        print(f"{key}: {value}")

def process_config(config: ConfigData) -> None:
    for section_name, section_data in config.items():
        process_section(section_data)

# ✗ 不好的做法
def process_section(section: dict) -> None:
    pass

def process_config(config: dict) -> None:
    pass
```

---

## 类型检查

Config 模块与 Pydantic 2.x 和 Python 3.10+ 的类型系统完全兼容。

### 使用 mypy 检查

```bash
mypy config/

# 输出
# config/app.py:5: error: Argument 1 to "load" has incompatible type
```

### 类型错误示例

```python
from src.kernel.config import ConfigBase

class AppConfig(ConfigBase):
    # ... 定义
    pass

# ✓ 正确
config: AppConfig = AppConfig.load("config.toml")

# ✗ 错误 - 类型不匹配
config: str = AppConfig.load("config.toml")
# mypy: error: Incompatible types in assignment

# ✗ 错误 - 参数类型不匹配
config = AppConfig.load(123)  # 期望 str | Path
# mypy: error: Argument 1 to "load" has incompatible type
```

---

## 高级类型用法

### 泛型配置

```python
from typing import Generic, TypeVar

T = TypeVar("T")

class EnvironmentConfig(Generic[T]):
    """支持不同环境的泛型配置"""
    
    @staticmethod
    def load_for_env(env: str) -> T:
        config_file = f"config/{env}.toml"
        # ... 加载逻辑
        pass
```

### 配置继承

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

@config_section("base")
class BaseSection(SectionBase):
    name: str = Field(default="")

class BaseConfig(ConfigBase):
    base: BaseSection = Field(default_factory=BaseSection)

# 继承并扩展
class ExtendedConfig(BaseConfig):
    # 继承 base 字段
    # 可以添加新的节
    pass
```

---

## 相关资源

- [Config 主文档](./README.md) - 使用指南
- [实现原理](./core.md) - 内部实现
- [高级用法](./advanced.md) - 自定义和扩展
