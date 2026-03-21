# FileHandler 模块

## 概述

`src.kernel.logger.file_handler` 负责日志文件写入与轮转。

当前设计特点：

- 由 logger 模块统一创建和持有全局 `FileHandler`
- 所有启用文件输出的 logger 共用同一个处理器
- 写入过程带锁，适用于多线程场景
- 写入失败会静默忽略，避免影响主业务流程

---

## `RotationMode`

```python
class RotationMode(Enum):
    SIZE = "size"
    DATE = "date"
    NEVER = "never"
```

### `RotationMode.SIZE`

按文件大小轮转。

文件名示例：

```text
mofox.log
mofox_1.log
mofox_2.log
```

### `RotationMode.DATE`

按日期轮转，并且把当前启动会话标识加入文件名。

文件名示例：

```text
mofox_20260319_101530_123456_ab12cd34_2026-03-19.log
```

命名格式：

```text
{base_filename}_{startup_session_id}_{YYYY-MM-DD}.log
```

### `RotationMode.NEVER`

不轮转，始终写入同一文件：

```text
mofox.log
```

---

## `FileHandler` 初始化

```python
FileHandler(
    log_dir: str | Path = "logs",
    base_filename: str = "app",
    rotation_mode: RotationMode = RotationMode.DATE,
    max_size: int = 10 * 1024 * 1024,
) -> None
```

参数说明：

- `log_dir`：日志目录
- `base_filename`：基础文件名
- `rotation_mode`：轮转策略
- `max_size`：按大小轮转时的阈值

---

## 与 `initialize_logger_system()` 的关系

业务代码通常**不直接创建** `FileHandler`，而是通过：

```python
from src.kernel.logger import RotationMode, initialize_logger_system

initialize_logger_system(
    log_dir="logs",
    enable_file=True,
    file_rotation=RotationMode.DATE,
    log_filename="mofox",
)
```

此时 logger 模块会内部创建全局 `FileHandler`。

因此当前推荐做法是：

- 在初始化阶段统一设置文件输出
- 运行阶段只通过 `get_logger()` 获取 logger
- 不在单个 logger 上配置独立文件目录

---

## 轮转行为说明

### DATE 模式

每次启动 `FileHandler` 时，都会生成新的 `startup_session_id`。

因此即使同一天内重启多次进程，也会生成不同文件，而不是所有同日运行都写入同一个文件。

适合：

- 需要按“运行会话 + 日期”区分日志的场景
- 希望避免多次启动混写到同一文件的场景

### SIZE 模式

当基础文件大小超过阈值时，会查找下一个可用后缀：

```text
mofox.log
mofox_1.log
mofox_2.log
...
```

适合：

- 日志量较大
- 需要控制单文件大小

### NEVER 模式

始终写入单文件。

适合：

- 临时调试
- 单次脚本
- 短生命周期进程

---

## 线程安全

`FileHandler.write()` 使用内部锁保护写入与轮转流程：

```python
def write(self, message: str) -> None:
    with self._lock:
        ...
```

这意味着多线程同时写日志时，会按串行方式落盘，避免文件句柄竞争。

---

## 使用示例

### 推荐方式：通过全局初始化启用文件输出

```python
from src.kernel.logger import RotationMode, get_logger, initialize_logger_system

initialize_logger_system(
    log_dir="logs",
    enable_file=True,
    file_rotation=RotationMode.SIZE,
    max_file_size=5 * 1024 * 1024,
    log_filename="mofox",
)

logger = get_logger("app.runtime")
logger.info("日志会写入共享文件")
```

### 直接创建 `FileHandler`

```python
from src.kernel.logger.file_handler import FileHandler, RotationMode

handler = FileHandler(
    log_dir="logs/manual",
    base_filename="debug",
    rotation_mode=RotationMode.NEVER,
)

handler.write("manual log line\n")
handler.close()
```

---

## 注意事项

- 写入失败会被静默忽略，因此它不是强一致审计通道
- `DATE` 模式文件名包含会话 ID，不是简单的 `{date}.log`
- 当前 logger 设计是“全局共享文件处理器”，不是“每个 logger 一个文件处理器”
- `shutdown_logger_system()` 会关闭当前全局 `FileHandler`
