# Logger 高级用法

## 概述

本文档介绍了 Logger 的高级特性和最佳实践，包括自定义配置、性能优化、监控和集成模式。

---

## 高级配置

### 自定义控制台输出

#### 修改富文本输出

```python
from kernel.logger import get_logger
from rich.console import Console
from rich.theme import Theme

# 创建自定义主题
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "debug": "dim white",
})

logger = get_logger("app")

# 使用自定义主题
logger.set_console_theme(custom_theme)

logger.info("自定义主题的信息")
```

#### 富文本面板输出

```python
from kernel.logger import get_logger
from rich.panel import Panel
from rich.text import Text

logger = get_logger("app")

# 基本面板
logger.print_panel(
    "系统状态",
    title="Status Report",
    border_style="green"
)

# 带样式的面板
logger.print_panel(
    "[bold cyan]API 服务器已启动[/]\n"
    "[yellow]监听端口: 8080[/]\n"
    "[green]准备就绪[/]",
    title="Server Status",
    border_style="green",
    expand=False
)
```

#### 表格输出

```python
from kernel.logger import get_logger
from rich.table import Table

logger = get_logger("app")

# 创建表格
table = Table(title="用户统计")
table.add_column("用户 ID", style="cyan")
table.add_column("用户名", style="magenta")
table.add_column("状态", style="green")

table.add_row("1", "alice", "在线")
table.add_row("2", "bob", "离线")
table.add_row("3", "charlie", "在线")

logger.print_rich(table)
```

---

## 元数据与结构化日志

### 使用元数据进行上下文跟踪

```python
from kernel.logger import get_logger

logger = get_logger("app.request_handler")

def handle_request(request_id: str, user_id: str, action: str):
    # 为这个请求设置元数据
    logger.set_metadata("request_id", request_id)
    logger.set_metadata("user_id", user_id)
    logger.set_metadata("action", action)
    
    try:
        logger.info("处理请求开始")
        # 业务逻辑
        process_action(action)
        logger.info("请求处理完成")
    except Exception as e:
        logger.error(f"请求处理失败: {e}")
    finally:
        # 清理元数据
        logger.clear_metadata()

def process_action(action: str):
    # 元数据会自动包含在日志中
    logger.debug(f"执行操作: {action}")
```

### 批量设置元数据

```python
logger = get_logger("app")

# 批量设置元数据
metadata = {
    "version": "1.0.0",
    "environment": "production",
    "region": "us-east-1",
    "component": "api-gateway"
}

for key, value in metadata.items():
    logger.set_metadata(key, value)

logger.info("应用启动")
# 输出将包含所有元数据
```

### 查询元数据

```python
logger = get_logger("app")

logger.set_metadata("user_id", "123")
logger.set_metadata("session", "abc-def")

# 获取特定元数据
user_id = logger.get_metadata("user_id")  # "123"

# 获取所有元数据
all_metadata = logger.get_metadata()  # {"user_id": "123", "session": "abc-def"}

# 检查元数据是否存在
if logger.has_metadata("user_id"):
    print("User ID 已设置")
```

---

## 日志轮转与文件管理

### 日期轮转的实际应用

```python
from kernel.logger import get_logger, RotationMode
from pathlib import Path
from datetime import datetime, timedelta

logger = get_logger(
    "production",
    enable_file=True,
    file_rotation=RotationMode.DATE,
    log_dir="logs/production"
)

# DATE 模式自动为每天创建新文件
logger.info("2025-02-04 的日志")  # 写入 production_2025-02-04.log
logger.info("2025-02-05 的日志")  # 写入 production_2025-02-05.log
```

### 按大小轮转的实际应用

```python
from kernel.logger import get_logger, RotationMode

logger = get_logger(
    "api_server",
    enable_file=True,
    file_rotation=RotationMode.SIZE,
    max_file_size=10 * 1024 * 1024,  # 10MB
    log_dir="logs/api"
)

# 当 api_server.log 达到 10MB 时，
# 自动重命名为 api_server_1.log，创建新的 api_server.log

logger.info("这个日志会写入 api_server.log 或其轮转文件")
```

### 定期清理旧日志

```python
import os
from pathlib import Path
from datetime import datetime, timedelta
from kernel.logger import get_logger

def cleanup_old_logs(log_dir: str, max_days: int = 30):
    """删除 N 天前的日志文件"""
    log_path = Path(log_dir)
    
    if not log_path.exists():
        return
    
    cutoff_time = datetime.now() - timedelta(days=max_days)
    deleted_count = 0
    
    for log_file in log_path.glob("*.log"):
        file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        if file_mtime < cutoff_time:
            try:
                log_file.unlink()
                deleted_count += 1
                print(f"已删除: {log_file.name}")
            except Exception as e:
                print(f"删除失败: {e}")
    
    return deleted_count

# 定期执行清理
cleanup_old_logs("logs", max_days=30)
```

### 监控日志大小

```python
from pathlib import Path

def get_log_statistics(log_dir: str):
    """获取日志目录的统计信息"""
    log_path = Path(log_dir)
    
    if not log_path.exists():
        return None
    
    files = list(log_path.glob("*.log"))
    total_size = sum(f.stat().st_size for f in files)
    total_size_mb = total_size / (1024 * 1024)
    
    return {
        "file_count": len(files),
        "total_size_bytes": total_size,
        "total_size_mb": total_size_mb,
        "largest_file": max(files, key=lambda f: f.stat().st_size).name if files else None,
        "oldest_file": min(files, key=lambda f: f.stat().st_mtime).name if files else None,
    }

# 使用
stats = get_log_statistics("logs")
if stats:
    print(f"日志文件数: {stats['file_count']}")
    print(f"总大小: {stats['total_size_mb']:.2f}MB")
    if stats['total_size_mb'] > 1000:
        print("警告: 日志文件大小超过 1GB")
```

---

## 事件驱动的日志处理

### 订阅日志事件

```python
from kernel.logger import get_logger, get_event_bus

logger = get_logger("app")
event_bus = get_event_bus()

# 定义日志事件处理函数
def on_log_event(event_data):
    """处理所有日志事件"""
    level = event_data.get("level")
    message = event_data.get("message")
    
    # 这里可以实现：
    # 1. 发送到监控系统
    # 2. 触发告警
    # 3. 写入其他存储系统
    
    if level == "ERROR":
        print(f"触发告警: {message}")
        send_alert(message)

# 订阅日志事件
event_bus.subscribe("log.event", on_log_event)

logger.error("重要错误")  # 会触发上面的 on_log_event 处理
```

### 条件日志事件

```python
from kernel.logger import get_logger, get_event_bus

logger = get_logger("app")
event_bus = get_event_bus()

# 只处理 ERROR 级别的日志
def on_error_log(event_data):
    if event_data.get("level") == "ERROR":
        # 发送到告警系统
        send_critical_alert(event_data.get("message"))

event_bus.subscribe("log.event", on_error_log)

# 只处理特定组件的日志
def on_database_log(event_data):
    if "database" in event_data.get("logger_name", ""):
        log_to_database_monitor(event_data)

event_bus.subscribe("log.event", on_database_log)
```

---

## 多模块日志协调

### 为不同模块创建日志记录器

```python
from kernel.logger import get_logger

# 核心模块
core_logger = get_logger(
    "app.core",
    enable_file=True,
    log_dir="logs/core"
)

# 数据库模块
db_logger = get_logger(
    "app.database",
    enable_file=True,
    log_dir="logs/database"
)

# API 模块
api_logger = get_logger(
    "app.api",
    enable_file=True,
    log_dir="logs/api"
)

# 缓存模块
cache_logger = get_logger(
    "app.cache",
    enable_file=True,
    log_dir="logs/cache"
)

# 在不同模块中使用
def core_function():
    core_logger.info("核心逻辑执行")

def database_query():
    db_logger.debug("执行数据库查询")

def api_handler():
    api_logger.info("处理 API 请求")

def cache_operation():
    cache_logger.debug("缓存操作")
```

### 全局配置所有日志记录器

```python
from kernel.logger import get_all_loggers, set_level_for_all

# 为所有日志记录器设置级别
set_level_for_all("DEBUG")

# 获取所有日志记录器
all_loggers = get_all_loggers()

for logger_name, logger_instance in all_loggers.items():
    print(f"Logger: {logger_name}")
```

### 统一清理所有日志

```python
from kernel.logger import clear_all_loggers

# 清理所有日志记录器的元数据
clear_all_loggers()
```

---

## 性能优化

### 1. 避免频繁创建日志记录器

```python
# ✗ 不好的做法
def process_item(item_id):
    logger = get_logger("app")  # 每次调用时创建/获取
    logger.info(f"处理 {item_id}")

# ✓ 好的做法
logger = get_logger("app")  # 创建一次

def process_item(item_id):
    logger.info(f"处理 {item_id}")
```

### 2. 使用条件日志来避免不必要的格式化

```python
from kernel.logger import get_logger

logger = get_logger("app")

# ✗ 不好的做法 - 即使不输出也会格式化
logger.debug(f"复杂对象: {expensive_operation()}")

# ✓ 好的做法 - 只在需要时才执行
if logger.is_debug_enabled():
    logger.debug(f"复杂对象: {expensive_operation()}")

# ✓ 或者使用 lazy 模式
logger.debug("复杂对象: %r", expensive_operation)
```

### 3. 批量日志输出

```python
from kernel.logger import get_logger

logger = get_logger("app")

# 处理大量数据时，批量收集日志
logs = []
for i in range(10000):
    if i % 100 == 0:
        logs.append(f"处理进度: {i}")

for log_msg in logs:
    logger.info(log_msg)
```

---

## 集成模式

### 与 Web 框架集成 (FastAPI 示例)

```python
from fastapi import FastAPI
from kernel.logger import get_logger

app = FastAPI()
logger = get_logger("fastapi_app", enable_file=True)

@app.middleware("http")
async def log_middleware(request, call_next):
    # 为每个请求设置元数据
    logger.set_metadata("method", request.method)
    logger.set_metadata("path", request.url.path)
    logger.set_metadata("client", request.client.host if request.client else "unknown")
    
    logger.info("收到请求")
    
    try:
        response = await call_next(request)
        logger.set_metadata("status_code", response.status_code)
        logger.info("请求处理完成")
        return response
    except Exception as e:
        logger.error(f"请求处理失败: {e}")
        raise
    finally:
        logger.clear_metadata()
```

### 与异步任务集成 (Celery 示例)

```python
from celery import Celery
from kernel.logger import get_logger

app = Celery("tasks")
logger = get_logger("celery_tasks", enable_file=True, file_rotation="date")

@app.task
def long_running_task(task_id):
    """长时间运行的任务"""
    logger.set_metadata("task_id", task_id)
    logger.set_metadata("task_type", "long_running")
    
    try:
        logger.info("任务开始执行")
        
        for step in range(10):
            logger.info(f"执行步骤 {step + 1}/10")
            perform_step(step)
        
        logger.info("任务执行完成")
    except Exception as e:
        logger.error(f"任务执行失败: {e}")
        raise
    finally:
        logger.clear_metadata()

def perform_step(step):
    # 执行任务步骤
    pass
```

---

## 调试与诊断

### 启用诊断日志

```python
from kernel.logger import get_logger

# 创建诊断专用日志记录器
diagnostic_logger = get_logger("diagnostic", enable_file=True)

def diagnose_system():
    """诊断系统状态"""
    import platform
    import sys
    
    diagnostic_logger.info("系统诊断开始")
    diagnostic_logger.set_metadata("python_version", sys.version)
    diagnostic_logger.set_metadata("platform", platform.system())
    diagnostic_logger.set_metadata("processor", platform.processor())
    
    diagnostic_logger.info("系统信息已记录")
    diagnostic_logger.clear_metadata()
```

### 性能分析日志

```python
from kernel.logger import get_logger
import time

logger = get_logger("performance", enable_file=True)

def log_performance(func):
    """装饰器: 记录函数执行时间"""
    def wrapper(*args, **kwargs):
        start_time = time.time()
        logger.set_metadata("function", func.__name__)
        
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            logger.info(f"执行完成 (耗时: {elapsed:.2f}s)")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"执行失败 (耗时: {elapsed:.2f}s): {e}")
            raise
        finally:
            logger.clear_metadata()
    
    return wrapper

@log_performance
def expensive_operation():
    time.sleep(2)
    return "完成"
```

---

## 常见问题

### Q: 如何在多进程环境中安全使用 Logger？

A: FileHandler 使用线程锁保证线程安全。对于多进程环境：

```python
import multiprocessing
from kernel.logger import get_logger

def worker(worker_id):
    # 每个进程获取自己的 Logger 实例
    logger = get_logger(f"worker_{worker_id}")
    logger.info(f"Worker {worker_id} 开始")

if __name__ == "__main__":
    processes = [
        multiprocessing.Process(target=worker, args=(i,))
        for i in range(4)
    ]
    
    for p in processes:
        p.start()
    
    for p in processes:
        p.join()
```

### Q: 如何集成 Logger 与日志聚合系统（如 ELK）？

A: 订阅日志事件并转发到聚合系统：

```python
from kernel.logger import get_logger, get_event_bus
import json
import requests

logger = get_logger("app")
event_bus = get_event_bus()

def send_to_elasticsearch(event_data):
    """发送日志到 Elasticsearch"""
    payload = {
        "timestamp": datetime.now().isoformat(),
        "level": event_data.get("level"),
        "message": event_data.get("message"),
        "metadata": event_data.get("metadata", {})
    }
    
    try:
        requests.post(
            "http://elasticsearch:9200/_index/logs/_doc",
            json=payload
        )
    except Exception as e:
        logger.error(f"发送日志失败: {e}")

# 订阅所有日志事件
event_bus.subscribe("log.event", send_to_elasticsearch)
```

### Q: 如何进行日志的搜索和分析？

A: 由于日志是纯文本格式，可以使用标准工具：

```bash
# 查找特定错误
grep "ERROR" logs/*.log

# 查找特定用户的所有日志
grep "user_id=123" logs/*.log

# 统计错误数量
grep -c "ERROR" logs/*.log

# 查看最新的 N 条日志
tail -n 100 logs/app_*.log

# 查看实时日志
tail -f logs/app_*.log
```

或者使用 Python 脚本：

```python
from pathlib import Path
from datetime import datetime

def analyze_logs(log_dir: str, keyword: str):
    """分析日志文件"""
    results = []
    
    for log_file in Path(log_dir).glob("*.log"):
        with open(log_file, "r") as f:
            for line_no, line in enumerate(f, 1):
                if keyword.lower() in line.lower():
                    results.append({
                        "file": log_file.name,
                        "line": line_no,
                        "content": line.strip()
                    })
    
    return results

# 查找所有 ERROR
errors = analyze_logs("logs", "ERROR")
for error in errors:
    print(f"{error['file']}:{error['line']} - {error['content']}")
```

---

## 总结

Logger 模块提供了强大的日志记录能力：

1. **灵活的输出** - 控制台 + 文件，支持富文本格式
2. **智能轮转** - 按日期、按大小、不轮转三种模式
3. **元数据跟踪** - 方便的上下文管理
4. **事件驱动** - 与其他系统集成
5. **线程安全** - 多线程环境下可靠使用
6. **高性能** - 异步文件操作，避免阻塞

---

## 相关资源

- [Logger 主文档](./README.md) - 基础使用指南
- [Color 颜色系统](./color.md) - 颜色选择参考
- [FileHandler 文件输出](./file_handler.md) - 文件轮转详解
