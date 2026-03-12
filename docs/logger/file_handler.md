# FileHandler 模块

## 概述

`file_handler.py` 提供了日志文件的输出和轮转功能。它负责管理日志文件的创建、写入和轮转。

## 文件轮转模式

### RotationMode 枚举

```python
class RotationMode(Enum):
    """日志轮转模式"""
    
    SIZE = "size"  # 按文件大小轮转
    DATE = "date"  # 按日期轮转
    NEVER = "never"  # 不轮转
```

### SIZE 模式 - 按大小轮转

当日志文件达到指定大小时，创建一个新的日志文件。

**特点**：
- 每个文件的大小受控
- 适合长期运行的应用
- 文件名格式：`app.log`, `app_1.log`, `app_2.log`, ...

**配置示例**：
```python
from kernel.logger import get_logger, RotationMode

logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.SIZE,
    max_file_size=10 * 1024 * 1024,  # 10MB
)
```

**适用场景**：
- 日志量大的应用
- 需要精确控制文件大小的场景
- 磁盘空间有限

### DATE 模式 - 按日期轮转

每天创建一个新的日志文件。

**特点**：
- 按日期自动轮转，无需配置
- 便于按日期查看日志
- 文件名格式：`app_2025-02-04.log`, `app_2025-02-05.log`, ...

**配置示例**：
```python
from kernel.logger import get_logger, RotationMode

logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.DATE
)
```

**适用场景**：
- 一般应用开发
- 便于日常日志维护
- 清晰的日期组织

### NEVER 模式 - 不轮转

所有日志都写入同一文件，不进行轮转。

**特点**：
- 最简单的方式
- 适合临时调试
- 文件名格式：`app.log`

**配置示例**：
```python
from kernel.logger import get_logger, RotationMode

logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.NEVER
)
```

**适用场景**：
- 短期调试
- 日志量小的应用
- 单次运行的脚本

---

## FileHandler 类

### 初始化

```python
handler = FileHandler(
    log_dir: str | Path = "logs",
    base_filename: str = "app",
    rotation_mode: RotationMode = RotationMode.DATE,
    max_size: int = 10 * 1024 * 1024,  # 10MB
)
```

**参数说明**：

- `log_dir`：日志目录路径
- `base_filename`：基础文件名（不含扩展名）
- `rotation_mode`：轮转模式
- `max_size`：最大文件大小（字节），仅在 SIZE 模式下生效

### 属性

- `log_dir`：日志目录的 `Path` 对象
- `base_filename`：基础文件名
- `rotation_mode`：当前的轮转模式
- `max_size`：最大文件大小

---

## 文件命名规则

### DATE 模式

```
logs/
  app_2025-02-02.log
  app_2025-02-03.log
  app_2025-02-04.log
```

文件名格式：`{base_filename}_{YYYY-MM-DD}.log`

### SIZE 模式

```
logs/
  app.log      (10MB)
  app_1.log    (10MB)
  app_2.log    (10MB)
  app_3.log    (5MB) # 当前活跃文件
```

文件名格式：
- 当前文件：`{base_filename}.log`
- 归档文件：`{base_filename}_{N}.log`

### NEVER 模式

```
logs/
  app.log      (不断增长)
```

文件名格式：`{base_filename}.log`

---

## 线程安全性

FileHandler 使用线程锁确保线程安全：

```python
# 内部实现
_lock = threading.Lock()

def write(self, message: str) -> None:
    with self._lock:
        # 写入文件的操作
```

多个线程可以同时调用 `write()` 方法，FileHandler 会确保正确的序列化。

---

## 使用示例

### 基本文件输出

```python
from kernel.logger import get_logger, RotationMode

logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.DATE,
    log_dir="logs"
)

logger.info("日志已写入文件")
```

**结果**：在 `logs/app_2025-02-04.log` 中创建日志文件。

### 自定义日志目录

```python
from pathlib import Path

logger = get_logger(
    "app",
    enable_file=True,
    log_dir=Path("./var/logs")  # 自定义目录
)
```

### 处理多个模块的日志

```python
from kernel.logger import get_logger, RotationMode

# 每个模块独立的日志文件
db_logger = get_logger(
    "database",
    enable_file=True,
    log_dir="logs/database"
)

api_logger = get_logger(
    "api",
    enable_file=True,
    log_dir="logs/api"
)

cache_logger = get_logger(
    "cache",
    enable_file=True,
    log_dir="logs/cache"
)
```

**结果**：
```
logs/
  database/
    database_2025-02-04.log
  api/
    api_2025-02-04.log
  cache/
    cache_2025-02-04.log
```

### 动态启用/禁用文件输出

```python
logger = get_logger("app")

# 开始时只输出到控制台
logger.info("控制台日志")

# 稍后启用文件输出
logger.enable_file_output(
    log_dir="logs",
    file_rotation=RotationMode.DATE
)

logger.info("现在会同时输出到控制台和文件")

# 禁用文件输出
logger.disable_file_output()

logger.info("回到仅控制台输出")
```

---

## 日志文件内容

FileHandler 写入的日志是纯文本格式，不包含 ANSI 颜色代码。

**示例内容**（`app_2025-02-04.log`）：
```
[14:30:45] MyApp | INFO | 应用已启动
  version=1.0.0
[14:30:46] MyApp | DEBUG | 配置已加载
  config_file=config.yaml
[14:31:00] MyApp | INFO | 服务已就绪
[14:31:05] MyApp | ERROR | 连接失败
  reason=Timeout | retry_count=3
```

---

## 最佳实践

### 1. 为不同的应用选择合适的轮转模式

```python
# 小型应用或测试
logger = get_logger(
    "test",
    enable_file=True,
    file_rotation=RotationMode.NEVER
)

# 一般应用
logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.DATE
)

# 高流量应用
logger = get_logger(
    "api_server",
    enable_file=True,
    file_rotation=RotationMode.SIZE,
    max_file_size=50 * 1024 * 1024  # 50MB
)
```

### 2. 使用合理的目录结构

```python
# ✓ 好的做法
logger1 = get_logger("app.core", enable_file=True, log_dir="logs/core")
logger2 = get_logger("app.api", enable_file=True, log_dir="logs/api")
logger3 = get_logger("app.db", enable_file=True, log_dir="logs/database")

# ✗ 不好的做法
logger1 = get_logger("core", enable_file=True, log_dir="logs")
logger2 = get_logger("api", enable_file=True, log_dir="logs")
logger3 = get_logger("db", enable_file=True, log_dir="logs")
# 所有日志混在一起，难以维护
```

### 3. 定期清理旧日志

```python
import os
from pathlib import Path
from datetime import datetime, timedelta

def cleanup_old_logs(log_dir: str, days: int = 30):
    """清理 N 天前的日志文件"""
    log_dir = Path(log_dir)
    cutoff_time = datetime.now() - timedelta(days=days)
    
    for log_file in log_dir.glob("*.log"):
        file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        if file_mtime < cutoff_time:
            log_file.unlink()
            print(f"已删除: {log_file}")

# 使用定时任务调用
cleanup_old_logs("logs", days=30)
```

### 4. 监控日志文件大小

```python
from pathlib import Path

def get_logs_size(log_dir: str):
    """获取日志目录的总大小"""
    log_dir = Path(log_dir)
    total_size = sum(f.stat().st_size for f in log_dir.rglob("*.log"))
    return total_size / (1024 * 1024)  # 转换为 MB

size_mb = get_logs_size("logs")
if size_mb > 1000:  # 大于 1GB
    print(f"警告: 日志文件大小已达 {size_mb:.1f}MB")
```

### 5. 使用环境变量配置日志目录

```python
import os
from kernel.logger import get_logger

log_dir = os.getenv("LOG_DIR", "logs")
logger = get_logger(
    "app",
    enable_file=True,
    log_dir=log_dir
)
```

---

## 常见问题

### Q: 如何确保日志文件不会无限增长？

A: 根据应用特点选择合适的轮转模式：
- 使用 `SIZE` 模式设置合理的 `max_file_size`
- 使用 `DATE` 模式结合定期清理任务
- 定期监控日志目录大小

### Q: 是否可以同时输出到多个文件？

A: 可以，为不同的模块创建不同的日志记录器，每个指向不同的目录或使用不同的文件名：

```python
logger1 = get_logger("app.core", enable_file=True, log_dir="logs/core")
logger2 = get_logger("app.api", enable_file=True, log_dir="logs/api")
```

### Q: 如何读取日志文件？

A: 日志文件是纯文本格式，可以使用任何文本编辑器或编程语言读取：

```python
# 读取日志文件
with open("logs/app_2025-02-04.log", "r") as f:
    for line in f:
        print(line.strip())
```

### Q: 文件输出会影响性能吗？

A: 文件 I/O 操作可能影响性能。建议：
1. 使用异步框架时，文件操作在单独的线程中进行
2. 不要在热路径中频繁创建新的元数据
3. 定期清理旧日志文件

---

## 相关资源

- [Logger 主文档](./README.md) - Logger 使用指南
- [Color 颜色系统](./color.md) - 颜色定义
- [高级用法](./advanced.md) - 高级特性
