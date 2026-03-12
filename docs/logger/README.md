# Logger 模块文档

## 概述

Logger 模块提供了一个统一的、功能完整的日志系统，基于 Python 的 `rich` 库，支持彩色输出、元数据跟踪、文件输出和事件广播。

### 核心特性

- **彩色输出**：支持丰富的颜色主题和日志级别区分
- **元数据跟踪**：为每条日志附加上下文信息
- **多种输出方式**：控制台、文件、rich 面板、事件广播
- **统一日志文件**：所有logger共享同一个日志文件，便于集中管理和查看
- **文件轮转**：支持按日期、按大小或不轮转三种模式
- **线程安全**：使用锁保护并发访问
- **异常格式化**：自动使用 rich 格式化异常堆栈跟踪
- **事件集成**：与事件总线集成，可订阅日志事件
- **性能优化**：即发即弃的事件广播，不影响日志性能

## 快速开始

### 全局配置（推荐）

在应用启动时初始化全局日志配置，所有logger将共享这些配置：

```python
from kernel.logger import initialize_logger_system, RotationMode

# 初始化全局日志系统
initialize_logger_system(
    log_dir="logs",                      # 日志目录
    log_level="INFO",                    # 全局日志等级
    enable_file=True,                    # 启用文件输出
    file_rotation=RotationMode.DATE,     # 按日期轮转
    log_filename="mofox"                 # 统一日志文件名（所有logger共享）
)
```

### 基础用法

```python
from kernel.logger import get_logger, COLOR

# 创建日志记录器（自动使用全局配置）
logger = get_logger("my_app", display="MyApp", color=COLOR.BLUE)

# 输出各级别的日志
logger.debug("这是调试信息")
logger.info("这是普通信息")
logger.warning("这是警告")
logger.error("这是错误")
logger.critical("这是严重错误")
```

**输出示例**：
```
[14:30:45] MyApp | DEBUG | 这是调试信息
[14:30:45] MyApp | INFO | 这是普通信息
[14:30:45] MyApp | WARNING | 这是警告
[14:30:45] MyApp | ERROR | 这是错误
[14:30:45] MyApp | CRITICAL | 这是严重错误
```

### 多个Logger共享日志文件

```python
from kernel.logger import initialize_logger_system, get_logger, COLOR

# 初始化全局日志系统
initialize_logger_system(
    log_dir="logs",
    log_level="INFO",
    enable_file=True,
    log_filename="mofox"  # 所有logger都写入 mofox_YYYY-MM-DD.log
)

# 创建多个logger
logger_core = get_logger("core", display="核心", color=COLOR.BLUE)
logger_plugin = get_logger("plugin", display="插件", color=COLOR.GREEN)
logger_event = get_logger("event", display="事件", color=COLOR.YELLOW)

# 所有日志都会输出到同一个文件
logger_core.info("核心系统启动")
logger_plugin.info("加载插件")
logger_event.info("事件总线初始化")

# 日志文件内容：
# [14:30:45] 核心 | INFO | 核心系统启动
# [14:30:45] 插件 | INFO | 加载插件
# [14:30:45] 事件 | INFO | 事件总线初始化
```

### 启用文件输出

```python
from kernel.logger import get_logger, RotationMode

# 创建支持文件输出的日志记录器
logger = get_logger(
    "app",
    display="Application",
    enable_file=True,                    # 启用文件输出
    log_dir="logs",                      # 日志目录
    file_rotation=RotationMode.DATE      # 按日期轮转
)

logger.info("这条日志会同时输出到控制台和文件")
```

### 使用元数据

```python
logger = get_logger("api", display="API")

# 为所有后续日志设置全局元数据
logger.set_metadata("request_id", "req_12345")
logger.set_metadata("user_id", "user_789")

# 输出时会自动包含元数据
logger.info("处理请求")  # 会显示 request_id 和 user_id
```

**输出示例**：
```
[14:30:45] API | INFO | 处理请求
  request_id=req_12345 | user_id=user_789
```

### 面板输出

```python
logger = get_logger("app")

# 输出一个面板格式的日志
logger.print_panel(
    "系统已启动！所有服务正常运行。",
    title="启动完成",
    border_style="green"
)
```

## 模块结构

```
kernel/logger/
├── __init__.py           # 公开 API 导出
├── logger.py            # 核心日志记录器实现
├── color.py             # 颜色定义
├── file_handler.py      # 文件处理器
└── README.md            # 本文档
```

## 颜色系统

### 支持的颜色

Logger 模块支持丰富的颜色选项：

**基础颜色**：
- `COLOR.BLACK`, `COLOR.RED`, `COLOR.GREEN`, `COLOR.YELLOW`
- `COLOR.BLUE`, `COLOR.MAGENTA`, `COLOR.CYAN`, `COLOR.WHITE`

**明亮变体**：
- `COLOR.BRIGHT_BLACK`, `COLOR.BRIGHT_RED`, `COLOR.BRIGHT_GREEN`, 等...

**特殊颜色**：
- `COLOR.GRAY` - 灰色
- `COLOR.ORANGE` - 橙色
- `COLOR.PURPLE` - 紫色
- `COLOR.PINK` - 粉色

**日志级别推荐颜色**：
- `COLOR.DEBUG` - 暗灰色
- `COLOR.INFO` - 蓝色
- `COLOR.WARNING` - 黄色
- `COLOR.ERROR` - 红色
- `COLOR.CRITICAL` - 加粗红色

### 使用自定义颜色

```python
from kernel.logger import get_logger, COLOR

# 方法 1：使用 COLOR 枚举
logger = get_logger("app", color=COLOR.GREEN)

# 方法 2：使用字符串
logger = get_logger("app", color="magenta")

# 方法 3：使用 rich 支持的颜色
logger = get_logger("app", color="bold cyan")
```

## 核心 API

### get_logger()

获取或创建日志记录器。

```python
logger = get_logger(
    name: str,                           # 日志记录器唯一名称
    display: str | None = None,          # 显示名称（如果为 None 则使用 name）
    color: COLOR | str = COLOR.WHITE,    # 日志颜色
    console: Console | None = None,      # 自定义 Console 实例
    enable_file: bool = False,           # 是否启用文件输出
    log_dir: str | Path = "logs",        # 日志目录
    file_rotation: RotationMode = RotationMode.DATE,  # 文件轮转模式
    max_file_size: int = 10485760,       # 最大文件大小（10MB）
    enable_event_broadcast: bool = True  # 是否启用事件广播
) -> Logger
```

**参数说明**：

- `name`：日志记录器的唯一标识，同名调用会返回同一实例
- `display`：用于日志输出中显示的名称
- `color`：日志输出的颜色
- `console`：如果提供自定义的 `rich.Console` 实例，则使用它
- `enable_file`：是否同时输出到文件
- `log_dir`：日志文件保存的目录
- `file_rotation`：文件轮转策略
- `max_file_size`：单个文件的最大字节数
- `enable_event_broadcast`：是否向事件总线发送日志事件

**示例**：
```python
# 创建控制台日志记录器
logger = get_logger("app", display="应用", color=COLOR.BLUE)

# 创建支持文件输出的日志记录器
logger = get_logger(
    "app",
    display="应用",
    enable_file=True,
    file_rotation=RotationMode.DATE
)

# 获取已存在的日志记录器（相同 name）
logger2 = get_logger("app")  # 返回同一实例
```

### Logger 类的日志方法

#### debug()

输出 DEBUG 级别的日志。

```python
logger.debug(message: str, **kwargs: Any) -> None
```

**示例**：
```python
logger.debug("正在处理请求", request_id="123", method="GET")
```

#### info()

输出 INFO 级别的日志。

```python
logger.info(message: str, **kwargs: Any) -> None
```

#### warning()

输出 WARNING 级别的日志。

```python
logger.warning(message: str, **kwargs: Any) -> None
```

#### error()

输出 ERROR 级别的日志。

```python
logger.error(message: str, **kwargs: Any) -> None
```

#### critical()

输出 CRITICAL 级别的日志。

```python
logger.critical(message: str, **kwargs: Any) -> None
```

### 元数据管理

#### set_metadata()

设置全局元数据。

```python
logger.set_metadata(key: str, value: Any) -> None
```

**示例**：
```python
logger.set_metadata("user_id", "user_123")
logger.set_metadata("session_id", "sess_456")
```

#### get_metadata()

获取元数据。

```python
value = logger.get_metadata(key: str) -> Any
```

#### remove_metadata()

移除指定的元数据。

```python
logger.remove_metadata(key: str) -> None
```

#### clear_metadata()

清空所有元数据。

```python
logger.clear_metadata() -> None
```

### 高级输出

#### print_panel()

输出格式化的面板。

```python
logger.print_panel(
    message: str,
    title: str | None = None,
    border_style: str | None = None
) -> None
```

**示例**：
```python
logger.print_panel(
    "系统启动完成！",
    title="启动通知",
    border_style="green"
)
```

#### print_rich()

直接使用 rich 的 print 功能。

```python
logger.print_rich(*args: Any, **kwargs: Any) -> None
```

**示例**：
```python
from rich.table import Table

table = Table(title="用户统计")
table.add_column("姓名")
table.add_column("年龄")
table.add_row("Alice", "30")
table.add_row("Bob", "25")

logger.print_rich(table)
```

### 文件输出管理

#### enable_file_output()

动态启用文件输出。

```python
logger.enable_file_output(
    log_dir: str | Path = "logs",
    file_rotation: RotationMode = RotationMode.DATE,
    max_file_size: int = 10485760
) -> None
```

#### disable_file_output()

禁用文件输出。

```python
logger.disable_file_output() -> None
```

### 资源管理

#### close()

关闭日志记录器，释放资源。

```python
logger.close() -> None
```

---

## 文件输出与轮转

### RotationMode（轮转模式）

| 模式 | 说明 | 文件名示例 |
|------|------|---------|
| `DATE` | 按日期轮转，每天一个文件 | `app_2025-02-04.log` |
| `SIZE` | 按文件大小轮转 | `app.log`, `app_1.log`, `app_2.log` |
| `NEVER` | 不轮转，一直追加到同一文件 | `app.log` |

### 按日期轮转

```python
logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.DATE,  # 每天创建新文件
    log_dir="logs"
)

# 输出示例：
# logs/app_2025-02-04.log
# logs/app_2025-02-05.log
```

### 按大小轮转

```python
logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.SIZE,
    max_file_size=1024 * 1024,  # 1MB
    log_dir="logs"
)

# 输出示例：
# logs/app.log (1MB)
# logs/app_1.log (1MB)
# logs/app_2.log (...)
```

### 不轮转

```python
logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.NEVER,
    log_dir="logs"
)

# 输出示例：
# logs/app.log (一直追加)
```

---

## 事件广播

Logger 可以与事件系统集成，发布日志事件。

### 启用事件广播

```python
logger = get_logger(
    "app",
    enable_event_broadcast=True  # 启用事件广播
)
```

### 订阅日志事件

```python
from kernel.event import get_event_bus
from kernel.logger import LOG_OUTPUT_EVENT

event_bus = get_event_bus()

async def on_log_output(event_name, params):
    """处理日志事件"""
    level = params.get("level")
    message = params.get("message")
    logger_name = params.get("logger_name")
    metadata = params.get("metadata", {})
    
    print(f"[{logger_name}] {level}: {message}")
    if metadata:
        print(f"  元数据: {metadata}")
    
    # 返回处理结果
    from kernel.event import EventDecision
    return (EventDecision.SUCCESS, params)

# 订阅日志事件
event_bus.subscribe(LOG_OUTPUT_EVENT, on_log_output)
```

### 日志事件的数据结构

```python
{
    "timestamp": "2025-02-04T14:30:45.123",    # ISO 格式时间戳
    "level": "INFO",                            # 日志级别
    "logger_name": "app",                       # 日志记录器名称
    "display": "应用",                          # 显示名称
    "color": "blue",                            # 日志颜色
    "message": "应用已启动",                    # 日志消息
    "metadata": {                               # 元数据（可选）
        "user_id": "user_123",
        "request_id": "req_456"
    }
}
```

---

## 全局管理函数

### remove_logger()

移除指定的日志记录器。

```python
from kernel.logger import remove_logger

remove_logger("my_app")
```

### get_all_loggers()

获取所有已创建的日志记录器。

```python
from kernel.logger import get_all_loggers

loggers = get_all_loggers()
for name, logger in loggers.items():
    print(f"{name}: {logger}")
```

### clear_all_loggers()

清除所有日志记录器。

```python
from kernel.logger import clear_all_loggers

clear_all_loggers()
```

### install_rich_traceback_formatter()

安装 rich 的异常格式化。

```python
from kernel.logger import install_rich_traceback_formatter

install_rich_traceback_formatter()
```

**效果**：当程序发生未捕获的异常时，会自动使用 rich 的格式化输出异常堆栈，包括源代码行、局部变量等详细信息。

---

## 使用模式

### 模式 1：为不同的模块创建专用日志记录器

```python
# database.py
from kernel.logger import get_logger, COLOR

logger = get_logger("database", display="数据库", color=COLOR.CYAN)

def connect_db():
    logger.info("连接数据库...")
    # 连接逻辑

# api.py
from kernel.logger import get_logger, COLOR

logger = get_logger("api", display="API", color=COLOR.GREEN)

def handle_request():
    logger.info("处理请求...")
    # 处理逻辑
```

### 模式 2：为每个用户会话跟踪日志

```python
logger = get_logger("app")

class RequestHandler:
    def __init__(self, session_id: str, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
    
    def handle(self):
        # 为这次处理设置会话元数据
        logger.set_metadata("session_id", self.session_id)
        logger.set_metadata("user_id", self.user_id)
        
        try:
            logger.info("开始处理请求")
            # 处理逻辑
            logger.info("请求处理完成")
        finally:
            # 清理元数据
            logger.clear_metadata()
```

### 模式 3：异常记录

```python
logger = get_logger("app")

try:
    risky_operation()
except Exception as e:
    logger.error(f"操作失败: {e}", error_type=type(e).__name__)
    # 异常堆栈会由 rich 自动格式化显示
```

### 模式 4：性能监控日志

```python
import time
from kernel.logger import get_logger

logger = get_logger("perf", display="性能")

def slow_operation():
    start = time.time()
    logger.info("开始执行耗时操作...")
    
    # 执行操作
    time.sleep(2)
    
    duration = time.time() - start
    logger.info("操作完成", duration_seconds=duration)
```

### 模式 5：结构化日志（用于日志分析）

```python
logger = get_logger("app")

# 为订单处理流程记录结构化日志
logger.info(
    "订单已创建",
    order_id="ORD_12345",
    customer_id="CUST_789",
    amount=99.99,
    currency="USD"
)

logger.info(
    "订单已支付",
    order_id="ORD_12345",
    payment_method="credit_card",
    payment_id="PAY_456"
)

logger.info(
    "订单已发货",
    order_id="ORD_12345",
    tracking_number="TRACK_123456"
)
```

---

## 最佳实践

### 1. 为每个模块创建单一的日志记录器

```python
# ✓ 好的做法
logger = get_logger(__name__, display="我的模块", color=COLOR.BLUE)

# ✗ 不好的做法（频繁创建）
def my_function():
    logger = get_logger("temp")
    logger.info("...")
```

### 2. 使用合理的日志级别

- **DEBUG**：开发调试信息（详细）
- **INFO**：一般信息（程序流）
- **WARNING**：警告（可能的问题）
- **ERROR**：错误（需要关注）
- **CRITICAL**：严重错误（需要立即处理）

```python
# ✓ 好的做法
logger.debug("变量值: x=10, y=20")
logger.info("用户登录成功")
logger.warning("连接超时，已重试")
logger.error("无法连接数据库")
logger.critical("内存不足，系统关闭")

# ✗ 不好的做法（滥用）
logger.info("变量值: x=10, y=20")  # 应该用 debug
logger.error("用户登录成功")       # 应该用 info
```

### 3. 使用元数据提供上下文

```python
# ✓ 好的做法
logger.set_metadata("request_id", request.id)
logger.set_metadata("user_id", user.id)
logger.info("处理完成", status_code=200)

# ✗ 不好的做法
logger.info(f"处理完成 request_id={request.id} user_id={user.id} status_code=200")
```

### 4. 启用文件输出用于生产环境

```python
# 开发环境
logger = get_logger("app")

# 生产环境
logger = get_logger(
    "app",
    enable_file=True,
    file_rotation=RotationMode.DATE,
    log_dir="/var/log/app"
)
```

### 5. 不要在高频路径中创建新的元数据

```python
# ✓ 好的做法
logger.set_metadata("session_id", session_id)
for item in items:
    logger.info(f"处理 {item}")  # 重用 session_id

# ✗ 不好的做法
for item in items:
    logger.set_metadata("item", item)  # 频繁修改
    logger.info(f"处理 {item}")
```

---

## 常见问题

### Q: 如何同时使用多个日志记录器？

A: 为不同的模块创建不同的日志记录器，每个使用不同的 `name`：

```python
app_logger = get_logger("app", display="应用", color=COLOR.BLUE)
db_logger = get_logger("database", display="数据库", color=COLOR.CYAN)
api_logger = get_logger("api", display="API", color=COLOR.GREEN)
```

### Q: 如何收集日志统计信息？

A: 订阅 `LOG_OUTPUT_EVENT` 事件，收集统计数据：

```python
from kernel.logger import LOG_OUTPUT_EVENT
from kernel.event import get_event_bus

stats = {"info": 0, "warning": 0, "error": 0}

async def collect_stats(event_name, params):
    level = params["level"].lower()
    stats[level] = stats.get(level, 0) + 1
    return (EventDecision.SUCCESS, params)

event_bus = get_event_bus()
event_bus.subscribe(LOG_OUTPUT_EVENT, collect_stats)
```

### Q: 日志文件会自动清理吗？

A: 不会自动清理。你需要定期手动清理旧日志文件。建议使用计划任务：

```python
# 清理 30 天前的日志
import os
from pathlib import Path
from datetime import datetime, timedelta

log_dir = Path("logs")
cutoff_time = datetime.now() - timedelta(days=30)

for log_file in log_dir.glob("*.log"):
    if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff_time:
        os.remove(log_file)
```

### Q: 如何禁用某个日志记录器？

A: 使用 `remove_logger()` 移除它：

```python
from kernel.logger import remove_logger

remove_logger("logger_name")
```

### Q: 文件输出会影响性能吗？

A: 文件输出使用了线程锁，对性能的影响较小。事件广播是"即发即弃"的（使用 `ensure_future`），不会阻塞日志记录。

---

## 相关资源

- [Color 颜色系统](./color.md) - 颜色定义和使用
- [FileHandler 文件处理](./file_handler.md) - 文件输出详解
- [高级用法](./advanced.md) - 高级特性和最佳实践

## 版本信息

- **当前版本**：1.0.0
- **Python 版本**：3.10+
- **依赖**：rich >= 10.0
