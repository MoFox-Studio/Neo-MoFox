# Config 模块

## 概述

`config` 模块提供了基于 Pydantic 的类型安全配置文件系统，支持自动类型校验与 TOML 存储。

**核心特性**：
- 🔒 **类型安全** - 使用 Pydantic BaseModel 进行自动类型校验
- 📄 **TOML 支持** - 配置以 TOML 格式存储和加载
- 🔄 **自动更新** - 可选的自动签名更新和回写
- 📝 **自动注释** - 配置字段包含自描述文档
- 🎯 **IDE 友好** - 静态类型推断，支持代码补全
- 🧩 **模块化** - 配置分节管理，结构清晰

---

## 快速开始

### 定义配置

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

class MyAppConfig(ConfigBase):
    """应用配置"""
    
    @config_section("general")
    class GeneralSection(SectionBase):
        """通用设置"""
        
        debug: bool = Field(
            default=False,
            description="调试模式"
        )
        
        app_name: str = Field(
            default="MyApp",
            description="应用名称"
        )
        
        max_workers: int = Field(
            default=4,
            description="最大工作线程数"
        )
    
    @config_section("database")
    class DatabaseSection(SectionBase):
        """数据库配置"""
        
        host: str = Field(
            default="localhost",
            description="数据库主机"
        )
        
        port: int = Field(
            default=5432,
            description="数据库端口"
        )
        
        username: str = Field(
            default="admin",
            description="数据库用户名"
        )
    
    # 在类体中显式声明配置节字段
    general: GeneralSection = Field(default_factory=GeneralSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
```

### 加载配置

```python
from pathlib import Path

# 从 TOML 文件加载配置
config = MyAppConfig.load("config/app.toml")

# 访问配置值
print(f"Debug: {config.general.debug}")
print(f"App Name: {config.general.app_name}")
print(f"DB Host: {config.database.host}")
```

### 自动签名更新

```python
# 启用自动更新：如果配置文件与模型定义不一致，自动回写
config = MyAppConfig.load(
    "config/app.toml",
    auto_update=True  # 自动更新配置文件
)
```

### 生成默认配置

```python
# 获取默认配置字典
default_config = MyAppConfig.default()
print(default_config)

# 从字典加载配置
config = MyAppConfig.from_dict({
    "general": {
        "debug": True,
        "app_name": "TestApp"
    },
    "database": {
        "host": "db.example.com",
        "port": 5432
    }
})
```

---

## 核心概念

### 1. ConfigBase - 配置基类

ConfigBase 是配置文件的顶级类，继承自 Pydantic BaseModel。

**特点**：
- 所有配置节都作为字段显式声明
- 支持类型注解和自动校验
- 提供加载、保存和合并功能

```python
class AppConfig(ConfigBase):
    """应用配置"""
    
    general: GeneralSection = Field(default_factory=GeneralSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
```

### 2. SectionBase - 配置节基类

SectionBase 代表配置文件中的一个节（section），包含相关的配置选项。

**特点**：
- 使用 @config_section 装饰器指定节名称
- 节内所有字段都是配置选项
- 支持字段级别的文档和类型注解

```python
@config_section("database")
class DatabaseSection(SectionBase):
    """数据库配置"""
    
    host: str = Field(default="localhost", description="主机地址")
    port: int = Field(default=5432, description="端口号")
    username: str = Field(default="admin", description="用户名")
    password: str = Field(default="", description="密码")
```

### 3. @config_section 装饰器

指定配置节名称，并可附带 WebUI 展示元信息。

```python
@config_section(
    "section_name",
    title="显示标题",
    description="节描述",
    tag="general",
    order=0,
)
class MySection(SectionBase):
    """节的文档"""
    field1: str = Field(...)
```

**参数**：
- `name`：节在 TOML 中的名称
- `title`：WebUI 显示标题（可选）
- `description`：WebUI 节描述（可选）
- `tag`：节标签（可选，用于分组和图标映射）
- `order`：显示顺序，越小越靠前（可选）

### 4. Field - 字段定义

使用 config 模块增强版 `Field` 定义配置字段。

除 Pydantic 常规验证参数外，还支持 WebUI 元信息。

```python
Field(
    default=...,                 # 默认值
    description="...",           # 字段文档
    ge=0,                         # 数值下界（可选）
    le=100,                       # 数值上界（可选）
    label="显示名称",             # WebUI 标签（可选）
    input_type="slider",         # 控件类型（可选）
    placeholder="请输入...",      # 占位符（可选）
    order=10,                     # 显示顺序（可选）
    depends_on="feature_enabled", # 条件显示依赖字段（可选）
    depends_value=True,           # 条件显示期望值（可选）
)
```

---

## TOML 文件格式

### 自动生成的 TOML

当使用 `auto_update=True` 时，Config 模块自动生成格式化的 TOML 文件。

**示例** (`config/app.toml`)：

```toml
# 通用设置
[general]
# 调试模式
# 值类型：bool, 默认值：false
debug = false

# 应用名称
# 值类型：str, 默认值："MyApp"
app_name = "MyApp"

# 最大工作线程数
# 值类型：int, 默认值：4
max_workers = 4

# 数据库配置
[database]
# 数据库主机
# 值类型：str, 默认值："localhost"
host = "localhost"

# 数据库端口
# 值类型：int, 默认值：5432
port = 5432

# 数据库用户名
# 值类型：str, 默认值："admin"
username = "admin"
```

### TOML 节的含义

- `[section_name]` - 定义一个配置节
- `[[section_name]]` - 定义数组节（列表项配置）
- `[parent.child]` - 定义嵌套子节
- `key = value` - 配置选项
- `# description` - 字段文档（来自 Field.description）
- `# 值类型：..., 默认值：...` - 字段签名，包含类型和默认值

### TOML 数据类型

| TOML 类型 | Python 类型 | 示例 |
|----------|-----------|------|
| 布尔值 | `bool` | `true`, `false` |
| 整数 | `int` | `42`, `-1` |
| 浮点数 | `float` | `3.14`, `1e-5` |
| 字符串 | `str` | `"hello"`, `'world'` |
| 数组 | `list` | `[1, 2, 3]` |
| 表 | `dict` | `{a = 1, b = 2}` |

---

## 使用模式

### 模式 1: 基本配置

```python
from src.kernel.config import ConfigBase, SectionBase, config_section, Field

@config_section("app")
class AppSection(SectionBase):
    """应用配置"""
    name: str = Field(default="MyApp")
    version: str = Field(default="1.0.0")

class SimpleConfig(ConfigBase):
    app: AppSection = Field(default_factory=AppSection)

# 加载和使用
config = SimpleConfig.load("config.toml")
print(config.app.name)
```

### 模式 2: 多节配置

```python
@config_section("server")
class ServerSection(SectionBase):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)

@config_section("database")
class DatabaseSection(SectionBase):
    url: str = Field(default="sqlite:///db.sqlite")

class FullConfig(ConfigBase):
    server: ServerSection = Field(default_factory=ServerSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)

config = FullConfig.load("config.toml")
```

### 模式 3: 复杂字段类型

```python
from typing import list

@config_section("logging")
class LoggingSection(SectionBase):
    """日志配置"""
    level: str = Field(default="INFO")
    handlers: list[str] = Field(
        default_factory=list,
        description="日志处理器列表"
    )
    options: dict[str, str] = Field(
        default_factory=dict,
        description="日志选项"
    )

class AppConfig(ConfigBase):
    logging: LoggingSection = Field(default_factory=LoggingSection)
```

### 模式 4: 环境特定配置

```python
import os

# 根据环境选择不同的配置文件
env = os.getenv("ENV", "development")
config_file = f"config/{env}.toml"

config = MyAppConfig.load(config_file, auto_update=True)
```

### 模式 5: 配置验证

```python
from pydantic import field_validator

@config_section("server")
class ServerSection(SectionBase):
    host: str
    port: int
    
    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("端口必须在 1-65535 之间")
        return v
```

---

## API 参考

### ConfigBase 方法

#### load()

从 TOML 文件加载配置。

```python
config = MyConfig.load(
    path: str | Path,
    auto_update: bool = False
) -> MyConfig
```

**参数**：
- `path`：TOML 文件路径
- `auto_update`：是否自动更新配置文件以匹配模型定义

**返回**：配置实例

**说明**：
- 如果文件不存在，会自动创建空文件并继续加载
- `auto_update=True` 时会比对配置文件和模型定义的"签名"
- 若签名不一致，自动回写 TOML 文件，尽可能保留用户值

**使用示例**：

```python
# 基本加载
config = MyConfig.load("config.toml")

# 自动更新
config = MyConfig.load("config.toml", auto_update=True)
```

#### from_dict()

从字典加载配置。

```python
config = MyConfig.from_dict(data: dict) -> MyConfig
```

**参数**：
- `data`：配置字典，结构为 `{section_name: {field_name: value, ...}, ...}`

**返回**：配置实例

**使用示例**：

```python
config_dict = {
    "general": {
        "debug": True,
        "app_name": "MyApp"
    },
    "database": {
        "host": "localhost",
        "port": 5432
    }
}

config = MyConfig.from_dict(config_dict)
```

#### default()

生成默认配置字典。

```python
default_config = MyConfig.default() -> dict
```

**返回**：包含所有默认值的配置字典

**使用示例**：

```python
defaults = MyConfig.default()
print(defaults)
# {'general': {'debug': False, 'app_name': 'MyApp'}, ...}
```

---

## 最佳实践

### 1. 为所有字段提供描述

```python
# ✓ 好的做法
@config_section("database")
class DatabaseSection(SectionBase):
    host: str = Field(
        default="localhost",
        description="数据库主机地址"
    )
    port: int = Field(
        default=5432,
        description="数据库服务端口"
    )

# ✗ 不好的做法
@config_section("database")
class DatabaseSection(SectionBase):
    host: str = Field(default="localhost")
    port: int = Field(default=5432)
```

### 2. 使用明确的节名称

```python
# ✓ 好的做法
@config_section("database")
class DatabaseSection(SectionBase):
    pass

@config_section("cache")
class CacheSection(SectionBase):
    pass

# ✗ 不好的做法
@config_section("db")
class Section1(SectionBase):
    pass

@config_section("s2")
class Section2(SectionBase):
    pass
```

### 3. 组织相关配置到同一节

```python
# ✓ 好的做法 - 相关配置在同一节
@config_section("database")
class DatabaseSection(SectionBase):
    host: str
    port: int
    username: str
    password: str

# ✗ 不好的做法 - 相关配置分散
@config_section("server")
class ServerSection(SectionBase):
    db_host: str
    db_port: int

@config_section("general")
class GeneralSection(SectionBase):
    db_user: str
    db_pass: str
```

### 4. 设置合理的默认值

```python
# ✓ 好的做法 - 开箱即用
@config_section("general")
class GeneralSection(SectionBase):
    debug: bool = Field(default=False)
    workers: int = Field(default=4)
    timeout: float = Field(default=30.0)

# ✗ 不好的做法 - 缺少默认值
@config_section("general")
class GeneralSection(SectionBase):
    debug: bool  # 会被设为占位值
    workers: int  # 会被设为占位值
```

### 5. 在定义类时显式声明字段

```python
# ✓ 好的做法 - IDE 能正确推断
class AppConfig(ConfigBase):
    general: GeneralSection = Field(default_factory=GeneralSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)

config = AppConfig.load("config.toml")
config.general.debug  # IDE 自动补全

# ✗ 不好的做法 - IDE 无法推断
class AppConfig(ConfigBase):
    pass  # 动态添加字段
```

### 6. 使用 auto_update 保持配置文件同步

```python
# ✓ 推荐做法
config = MyConfig.load("config.toml", auto_update=True)
# 配置文件会自动同步，包含所有新增字段和文档

# 简单场景可不使用
config = MyConfig.load("config.toml", auto_update=False)
```

---

## 常见问题

### Q: 如何为不同的环境使用不同的配置？

A: 根据环境变量选择不同的配置文件：

```python
import os

env = os.getenv("APP_ENV", "development")
config_file = f"config/{env}.toml"

config = AppConfig.load(config_file, auto_update=True)
```

文件结构：
```
config/
  development.toml
  staging.toml
  production.toml
```

### Q: 如何验证配置字段值？

A: 使用 Pydantic 的验证器：

```python
from pydantic import field_validator

@config_section("server")
class ServerSection(SectionBase):
    port: int = Field(default=8000)
    
    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("端口必须在 1-65535 之间")
        return v
```

### Q: 如何在配置中使用复杂类型？

A: 使用 Pydantic 支持的任何类型：

```python
from typing import list, dict
from datetime import timedelta

@config_section("advanced")
class AdvancedSection(SectionBase):
    tags: list[str] = Field(default_factory=list)
    options: dict[str, int] = Field(default_factory=dict)
    timeout: timedelta = Field(default=timedelta(seconds=30))
```

### Q: 如何处理 TOML 中不存在的字段？

A: Config 模块会使用默认值或占位值：

```python
# 如果 TOML 中缺少 "debug" 字段
debug: bool = Field(default=False)
# 会使用默认值 False

# 如果没有默认值
debug: bool
# 会使用占位值 false
```

### Q: 如何生成配置文件模板？

A: 使用 default() 方法：

```python
import json

defaults = AppConfig.default()

# 手动创建 TOML 文件
with open("config/template.toml", "w") as f:
    # 使用 auto_update=True 加载空配置文件
    empty_config = AppConfig.load("config/template.toml", auto_update=True)
```

---

## 类型定义

### ConfigData

```python
ConfigData: TypeAlias = dict[str, dict[str, Any]]
```

配置字典的类型，结构为 `{section_name: {field_name: value, ...}, ...}`

### SectionData

```python
SectionData: TypeAlias = dict[str, Any]
```

单个配置节的类型，结构为 `{field_name: value, ...}`

### TOMLData

```python
TOMLData: TypeAlias = dict[str, Any]
```

TOML 文件的原始解析数据

---

## 相关资源

- [类型定义详解](./types.md) - Config 模块的类型系统
- [实现原理](./core.md) - 内部实现细节
- [示例代码](../../examples/src/kernel/config/config_example.py) - 最小可运行示例
