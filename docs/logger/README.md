# Logger 模块文档

## 概述

`src.kernel.logger` 提供统一日志能力，面向整个应用共享使用。模块基于 `rich` 实现彩色控制台输出，并提供以下能力：

- 彩色日志输出
- 基于 `Logger` 名称的稳定默认颜色映射
- 全局文件输出与轮转
- 元数据附加与上下文跟踪
- `rich` 面板/对象输出
- 异常堆栈格式化
- 事件总线广播
- 线程安全写入

Logger 模块遵循“先全局初始化，再按需获取 logger”的使用方式。

---

## 模块结构

```text
src/kernel/logger/
├── __init__.py
├── logger.py
├── color.py
└── file_handler.py
```

文档拆分如下：

- [README.md](README.md)：模块总览与核心 API
- [advanced.md](advanced.md)：高级用法与最佳实践
- [color.md](color.md)：颜色系统说明
- [file_handler.md](file_handler.md)：文件输出与轮转说明

---

## 快速开始

### 1. 初始化全局日志系统

推荐在应用启动阶段只调用一次：

```python
from src.kernel.logger import RotationMode, initialize_logger_system

initialize_logger_system(
    log_dir="logs",
    log_level="INFO",
    enable_file=True,
    file_rotation=RotationMode.DATE,
    max_file_size=10 * 1024 * 1024,
    enable_event_broadcast=True,
    log_filename="mofox",
)
```

### 2. 获取 logger 并记录日志

```python
from src.kernel.logger import COLOR, get_logger

logger = get_logger("app.runtime", display="Runtime", color=COLOR.CYAN)

logger.debug("这条 DEBUG 仅在日志等级允许时显示")
logger.info("应用启动完成")
logger.warning("检测到可恢复问题")
logger.error("发生错误")
logger.critical("出现严重错误")
```

### 3. 使用元数据

```python
from src.kernel.logger import get_logger

logger = get_logger("api")
logger.set_metadata("request_id", "req-001")
logger.set_metadata("user_id", "u-100")

logger.info("开始处理请求", method="GET", path="/health")
```

控制台输出会包含主消息，元数据会以附加行输出。

---

## 核心设计

### 全局配置与实例获取

- `initialize_logger_system()` 负责设置全局默认配置。
- `get_logger()` 负责按名称获取或创建 `Logger` 实例。
- 同名 `Logger` 只会创建一次，后续调用返回同一实例。

### 共享文件处理器

当启用文件输出时，所有 logger 共享同一个全局 `FileHandler`。

这意味着：

- 不会为每个 logger 单独创建文件处理器
- 文件目录、轮转策略、文件名由全局初始化统一控制
- `get_logger()` 不负责设置日志目录或轮转方式

### 日志等级行为

- 控制台输出会经过日志等级过滤
- 文件输出**不会**经过日志等级过滤，只要该 logger 启用了文件输出，就会写入文件

如果需要同时控制控制台与文件输出范围，应在业务层统一约束调用级别。

### 事件广播行为

启用事件广播后，logger 会尝试向事件总线发布 `LOG_OUTPUT_EVENT`。

- 事件名固定为：`"log_output"`
- 仅在存在运行中的事件循环时才会异步发布
- 发布采用即发即弃策略，不阻塞主日志流程
- 广播失败不会影响日志输出本身

---

## 核心 API

### `initialize_logger_system()`

```python
initialize_logger_system(
    log_dir: str | Path = "logs",
    log_level: str = "DEBUG",
    enable_file: bool = True,
    file_rotation: RotationMode = RotationMode.DATE,
    max_file_size: int = 10 * 1024 * 1024,
    enable_event_broadcast: bool = True,
    log_filename: str = "mofox",
) -> None
```

参数说明：

- `log_dir`：日志目录
- `log_level`：默认控制台日志等级，支持 `DEBUG/INFO/WARNING/ERROR/CRITICAL`
- `enable_file`：是否默认启用文件输出
- `file_rotation`：文件轮转策略
- `max_file_size`：按大小轮转时的阈值
- `enable_event_broadcast`：是否默认启用日志事件广播
- `log_filename`：共享日志文件基础名

### `get_global_log_config()`

```python
get_global_log_config() -> dict[str, Any]
```

返回当前全局日志配置的副本。

### `get_logger()`

```python
get_logger(
    name: str,
    display: str | None = None,
    color: COLOR | str | None = None,
    console: Console | None = None,
    enable_file: bool | None = None,
    enable_event_broadcast: bool | None = None,
    log_level: str | None = None,
) -> Logger
```

参数说明：

- `name`：logger 唯一名称
- `display`：显示名称，默认为 `name`
- `color`：前缀颜色；传入 `None` 时会按 `name` 稳定映射默认颜色
- `console`：自定义 `rich.console.Console`
- `enable_file`：是否启用文件输出；为 `None` 时继承全局配置
- `enable_event_broadcast`：是否启用事件广播；为 `None` 时继承全局配置
- `log_level`：控制台日志等级；为 `None` 时继承全局配置

注意：`get_logger()` 当前**不支持**传入 `log_dir`、`file_rotation`、`max_file_size`、`log_filename`。

### `Logger` 实例方法

#### 日志方法

```python
logger.debug(message: str, **kwargs: Any) -> None
logger.info(message: str, **kwargs: Any) -> None
logger.warning(message: str, **kwargs: Any) -> None
logger.error(message: str, **kwargs: Any) -> None
logger.critical(message: str, **kwargs: Any) -> None
```

说明：

- `message` 支持 `rich` markup
- 额外的 `**kwargs` 会作为元数据输出
- 可通过 `exc_info=True` 或 `exc_info=<异常对象>` 附加异常堆栈

示例：

```python
try:
    1 / 0
except ZeroDivisionError as exc:
    logger.error("计算失败", exc_info=exc, operation="divide")
```

#### 日志等级管理

```python
logger.set_log_level(level: str) -> None
logger.get_log_level() -> str
```

#### 元数据管理

```python
logger.set_metadata(key: str, value: Any) -> None
logger.get_metadata(key: str) -> Any
logger.remove_metadata(key: str) -> None
logger.clear_metadata() -> None
```

#### Rich 输出

```python
logger.print_panel(
    message: str,
    title: str | None = None,
    border_style: str | None = None,
) -> None

logger.print_rich(*args: Any, **kwargs: Any) -> None
```

示例：

```python
logger.print_panel(
    "插件加载完成，共 12 个组件",
    title="Plugin Manager",
    border_style="green",
)
```

### 生命周期与辅助函数

```python
remove_logger(name: str) -> None
get_all_loggers() -> dict[str, Logger]
clear_all_loggers() -> None
shutdown_logger_system() -> None
install_rich_traceback_formatter() -> None
```

说明：

- `remove_logger()`：移除指定实例缓存
- `get_all_loggers()`：获取当前注册表副本
- `clear_all_loggers()`：清空 logger 注册表
- `shutdown_logger_system()`：关闭全局文件处理器
- `install_rich_traceback_formatter()`：安装 `rich` 异常回溯格式化

如果需要完整重置，通常应先调用 `shutdown_logger_system()`，再调用 `clear_all_loggers()`。

---

## 颜色系统

颜色来源于 `COLOR` 枚举与 Rich 支持的样式字符串。

```python
from src.kernel.logger import COLOR, get_logger

logger1 = get_logger("core", color=COLOR.BLUE)
logger2 = get_logger("plugin", color="bold magenta")
logger3 = get_logger("event", color="#7DCFFF")
```

若 `color=None`，模块会基于 `name` 计算稳定默认颜色，以便不同模块在控制台上更容易区分。

更多说明见 [color.md](color.md)。

---

## 文件输出与轮转

### 日期轮转

`RotationMode.DATE` 下的文件名格式为：

```text
{log_filename}_{startup_session_id}_{YYYY-MM-DD}.log
```

例如：

```text
mofox_20260319_101530_123456_ab12cd34_2026-03-19.log
```

其中 `startup_session_id` 由“启动时间 + 随机短 ID”组成，用于区分不同运行会话。

### 大小轮转

`RotationMode.SIZE` 下的文件名格式为：

```text
mofox.log
mofox_1.log
mofox_2.log
```

### 不轮转

`RotationMode.NEVER` 下始终写入：

```text
mofox.log
```

更多说明见 [file_handler.md](file_handler.md)。

---

## 事件广播示例

```python
from src.kernel.event import get_event_bus
from src.kernel.logger import LOG_OUTPUT_EVENT, get_logger

event_bus = get_event_bus()
logger = get_logger("event-demo", enable_event_broadcast=True)


async def on_log(event_name: str, params: dict) -> tuple:
    print(event_name, params["level"], params["message"])
    return (None, params)


event_bus.subscribe(LOG_OUTPUT_EVENT, on_log)
logger.info("这条日志会尝试广播")
```

事件数据通常包含：

- `timestamp`
- `level`
- `logger_name`
- `display`
- `color`
- `message`
- `metadata`（存在时）

---

## 推荐用法

### 推荐：应用启动时统一初始化

```python
from src.kernel.logger import initialize_logger_system

initialize_logger_system(log_dir="logs", log_level="INFO", enable_file=True)
```

### 推荐：按模块获取命名 logger

```python
runtime_logger = get_logger("app.runtime", display="Runtime")
plugin_logger = get_logger("core.plugin", display="Plugin")
db_logger = get_logger("kernel.db", display="Database")
```

### 推荐：请求结束后清理元数据

```python
logger.set_metadata("request_id", request_id)
try:
    logger.info("处理开始")
finally:
    logger.clear_metadata()
```

---

## 常见误区

### 误区 1：在 `get_logger()` 中配置日志目录

错误示例：

```python
get_logger("app", enable_file=True, log_dir="logs")
```

原因：`get_logger()` 没有 `log_dir` 参数。文件输出配置必须通过 `initialize_logger_system()` 完成。

### 误区 2：认为文件输出也会被等级过滤

当前实现中：

- 控制台输出会过滤
- 文件输出不会过滤

### 误区 3：未初始化就假定所有 logger 自动写文件

如果未调用全局初始化，默认全局配置中的 `enable_file` 为 `False`，此时 logger 仅输出到控制台，除非显式在 `get_logger()` 中传入 `enable_file=True` 且全局文件处理器已由初始化建立。

---

## 参考

- [advanced.md](advanced.md)
- [color.md](color.md)
- [file_handler.md](file_handler.md)
- [src/kernel/logger/__init__.py](../../src/kernel/logger/__init__.py)
- [src/kernel/logger/logger.py](../../src/kernel/logger/logger.py)
- [src/kernel/logger/color.py](../../src/kernel/logger/color.py)
- [src/kernel/logger/file_handler.py](../../src/kernel/logger/file_handler.py)
