# Logger 高级用法

## 概述

本文聚焦 `src.kernel.logger` 的高级使用方式，内容以当前实现为准，不包含未实现的扩展 API。

---

## 1. 控制台、文件、事件三种输出面的关系

一次 `logger.info()` 调用，内部可能触发三类输出：

1. **控制台输出**：受 logger 当前 `log_level` 过滤
2. **文件输出**：只要该 logger 启用了文件输出，就写入共享文件
3. **事件广播**：若启用广播且存在运行中的事件循环，则发布 `LOG_OUTPUT_EVENT`

这三者互相独立。

### 典型模式

```python
from src.kernel.logger import initialize_logger_system, get_logger

initialize_logger_system(log_level="INFO", enable_file=True)

logger = get_logger(
    "app.worker",
    enable_event_broadcast=True,
)

logger.debug("不会出现在控制台，但仍可能写入文件")
logger.info("会出现在控制台，也会写入文件")
```

---

## 2. 请求级上下文管理

`Logger` 持有实例级元数据字典。适合附加请求 ID、用户 ID、会话 ID 等上下文。

```python
from src.kernel.logger import get_logger

logger = get_logger("app.api", display="API")


def handle_request(request_id: str, user_id: str) -> None:
    logger.set_metadata("request_id", request_id)
    logger.set_metadata("user_id", user_id)

    try:
        logger.info("开始处理请求", path="/messages", method="POST")
        logger.info("请求处理完成", status_code=200)
    finally:
        logger.clear_metadata()
```

### 建议

- 请求结束后及时 `clear_metadata()`
- 临时上下文可以通过日志调用时的 `**kwargs` 直接传入
- 避免将敏感信息直接写入元数据

---

## 3. 异常日志记录

`exc_info` 支持两种常见形式：

- `exc_info=True`：读取当前异常上下文
- `exc_info=<异常对象>`：显式传入异常对象

```python
from src.kernel.logger import get_logger

logger = get_logger("app.service")

try:
    raise ValueError("配置非法")
except ValueError as exc:
    logger.error("服务初始化失败", exc_info=exc, stage="bootstrap")
```

如果已安装 `rich` traceback，终端中的未捕获异常也会以更可读的格式显示。

---

## 4. 使用 `print_panel()` 展示关键状态

`print_panel()` 适合输出阶段性结果或状态摘要。

```python
from src.kernel.logger import COLOR, get_logger

logger = get_logger("plugin.manager", display="PluginManager", color=COLOR.GREEN)

logger.print_panel(
    "已加载 12 个插件\n已注册 46 个组件\n未发现冲突",
    title="启动摘要",
    border_style="green",
)
```

说明：

- `border_style=None` 时会默认使用 logger 自身颜色
- `print_panel()` 属于 Rich 直出，不经过日志等级过滤逻辑

---

## 5. 使用 `print_rich()` 输出复杂对象

当需要输出表格、树、Markdown 或自定义 Rich 对象时，直接使用 `print_rich()`。

```python
from rich.table import Table

from src.kernel.logger import get_logger

logger = get_logger("app.report")

table = Table(title="插件统计")
table.add_column("插件")
table.add_column("状态")
table.add_row("default_chatter", "loaded")
table.add_row("emoji_sender", "loaded")

logger.print_rich(table)
```

---

## 6. 为不同模块使用稳定颜色

如果不传 `color`，`get_logger()` 会根据名称哈希自动分配稳定颜色：

```python
from src.kernel.logger import get_logger

logger_a = get_logger("kernel.db")
logger_b = get_logger("kernel.event")
logger_c = get_logger("core.plugin")
```

这样可以在不手动维护颜色枚举的情况下，让不同模块在控制台上保持稳定区分。

如果你需要固定品牌色或强调重点模块，再显式传入 `COLOR` 或 Rich 样式字符串。

---

## 7. 事件系统集成

Logger 通过 `LOG_OUTPUT_EVENT` 与事件总线集成。

```python
from src.kernel.event import get_event_bus
from src.kernel.logger import LOG_OUTPUT_EVENT, get_logger

event_bus = get_event_bus()
logger = get_logger("monitor", enable_event_broadcast=True)


async def on_log(event_name: str, params: dict) -> tuple:
    if params["level"] in {"ERROR", "CRITICAL"}:
        print("捕获高优先级日志：", params["message"])
    return (None, params)


event_bus.subscribe(LOG_OUTPUT_EVENT, on_log)
logger.error("数据库连接失败", retry=False)
```

### 注意事项

- 若当前线程没有运行中的事件循环，则不会广播
- 广播异常会被静默吞掉，不影响主流程
- 如需可靠告警链路，应在事件订阅方自行做持久化与重试

---

## 8. 生命周期管理

### 重新初始化全局文件输出

多次调用 `initialize_logger_system()` 时，模块会先关闭旧的全局文件处理器，再按新配置重建。

```python
from src.kernel.logger import RotationMode, initialize_logger_system

initialize_logger_system(log_dir="logs/dev", file_rotation=RotationMode.NEVER)
initialize_logger_system(log_dir="logs/prod", file_rotation=RotationMode.DATE)
```

### 关闭与清理

```python
from src.kernel.logger import clear_all_loggers, shutdown_logger_system

shutdown_logger_system()
clear_all_loggers()
```

推荐在测试 teardown 或应用退出阶段执行。

---

## 9. 最佳实践

### 推荐

- 在应用入口统一调用 `initialize_logger_system()`
- 使用层次化命名，例如 `kernel.db`、`core.plugin`、`app.runtime`
- 对长生命周期上下文使用 `set_metadata()`
- 对单次日志上下文使用 `**kwargs`
- 在边界层记录错误，在核心路径避免重复打印同一异常

### 不推荐

- 在每个函数里反复调用不同名字的 logger
- 把密钥、令牌、数据库密码写入日志
- 依赖日志广播作为唯一可靠的审计机制
- 假定文件输出也会被 `log_level` 过滤

---

## 10. 与实现不符的旧写法说明

以下写法不再适用于当前实现：

```python
get_logger("app", log_dir="logs")
get_logger("app", file_rotation=RotationMode.DATE)
logger.set_console_theme(...)
logger.enable_file_output(...)
logger.has_metadata(...)
logger.get_metadata()  # 无参获取全部元数据
```

原因：这些参数或方法当前实现中不存在。

请以 [README.md](README.md) 与 [src/kernel/logger/logger.py](../../src/kernel/logger/logger.py) 为准。
