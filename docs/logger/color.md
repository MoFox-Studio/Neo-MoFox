# Color 模块

## 概述

`color.py` 定义了 Logger 模块支持的所有颜色，以及颜色转换函数。它与 `rich` 库的颜色系统集成。

## 颜色枚举

### COLOR 类

```python
class COLOR(Enum):
    """日志颜色枚举"""
```

### 颜色分类

#### 基础颜色（8 种）

```python
COLOR.BLACK        # 黑色
COLOR.RED          # 红色
COLOR.GREEN        # 绿色
COLOR.YELLOW       # 黄色
COLOR.BLUE         # 蓝色
COLOR.MAGENTA      # 洋红色
COLOR.CYAN         # 青色
COLOR.WHITE        # 白色
```

#### 明亮变体（8 种）

```python
COLOR.BRIGHT_BLACK      # 明亮黑色（暗灰）
COLOR.BRIGHT_RED        # 明亮红色
COLOR.BRIGHT_GREEN      # 明亮绿色
COLOR.BRIGHT_YELLOW     # 明亮黄色
COLOR.BRIGHT_BLUE       # 明亮蓝色
COLOR.BRIGHT_MAGENTA    # 明亮洋红
COLOR.BRIGHT_CYAN       # 明亮青色
COLOR.BRIGHT_WHITE      # 明亮白色
```

#### 特殊颜色（4 种）

```python
COLOR.GRAY      # 灰色（中性灰）
COLOR.ORANGE    # 橙色
COLOR.PURPLE    # 紫色
COLOR.PINK      # 粉色（深粉）
```

#### 日志级别推荐颜色

```python
COLOR.DEBUG     # 暗灰色 - 用于 DEBUG 级别
COLOR.INFO      # 蓝色   - 用于 INFO 级别
COLOR.WARNING   # 黄色   - 用于 WARNING 级别
COLOR.ERROR     # 红色   - 用于 ERROR 级别
COLOR.CRITICAL  # 加粗红色 - 用于 CRITICAL 级别
```

---

## 函数

### get_rich_color()

将 `COLOR` 枚举或字符串转换为 `rich` 库支持的颜色字符串。

```python
def get_rich_color(color: COLOR | str) -> str:
    """获取 rich 库支持的颜色字符串
    
    Args:
        color: COLOR 枚举或颜色字符串
    
    Returns:
        str: rich 支持的颜色字符串
    """
```

**示例**：

```python
from kernel.logger.color import COLOR, get_rich_color

# 使用 COLOR 枚举
color_str = get_rich_color(COLOR.BLUE)
print(color_str)  # "blue"

# 使用字符串
color_str = get_rich_color("red")
print(color_str)  # "red"

# 使用 rich 样式
color_str = get_rich_color("bold magenta")
print(color_str)  # "bold magenta"
```

---

## 预定义的颜色映射

### DEFAULT_LEVEL_COLORS

```python
DEFAULT_LEVEL_COLORS = {
    "DEBUG": COLOR.DEBUG,
    "INFO": COLOR.INFO,
    "WARNING": COLOR.WARNING,
    "ERROR": COLOR.ERROR,
    "CRITICAL": COLOR.CRITICAL,
}
```

这个字典映射日志级别到推荐的颜色。Logger 在输出各级别的日志时使用这个映射。

---

## 使用示例

### 基础颜色使用

```python
from kernel.logger import get_logger, COLOR

# 创建不同颜色的日志记录器
app_logger = get_logger("app", color=COLOR.BLUE)
db_logger = get_logger("database", color=COLOR.CYAN)
api_logger = get_logger("api", color=COLOR.GREEN)
error_logger = get_logger("error", color=COLOR.RED)

# 输出日志
app_logger.info("应用启动")        # 蓝色前缀
db_logger.info("数据库已连接")    # 青色前缀
api_logger.info("API 服务就绪")   # 绿色前缀
error_logger.error("严重错误")    # 红色前缀
```

### 使用字符串指定颜色

```python
from kernel.logger import get_logger

# 使用 rich 支持的颜色字符串
logger = get_logger("app", color="magenta")

# 使用 rich 样式
logger = get_logger("app", color="bold yellow")
logger = get_logger("app", color="italic cyan")
logger = get_logger("app", color="underline green")
```

### 使用明亮颜色创建醒目的日志

```python
from kernel.logger import get_logger, COLOR

# 使用明亮颜色
warning_logger = get_logger("warn", color=COLOR.BRIGHT_YELLOW)
alert_logger = get_logger("alert", color=COLOR.BRIGHT_RED)
success_logger = get_logger("success", color=COLOR.BRIGHT_GREEN)
```

### 为不同的用途选择颜色

```python
from kernel.logger import get_logger, COLOR

# 系统级日志
system_logger = get_logger("system", color=COLOR.WHITE)

# 业务日志
business_logger = get_logger("business", color=COLOR.GREEN)

# 性能日志
perf_logger = get_logger("performance", color=COLOR.YELLOW)

# 错误和警告
error_logger = get_logger("errors", color=COLOR.RED)
warn_logger = get_logger("warnings", color=COLOR.YELLOW)

# 调试日志
debug_logger = get_logger("debug", color=COLOR.GRAY)
```

---

## 颜色选择指南

| 模块/用途 | 推荐颜色 | 说明 |
|---------|--------|------|
| 应用核心 | `BLUE` 或 `CYAN` | 醒目但不会显得过度 |
| 数据库 | `CYAN` 或 `MAGENTA` | 与 I/O 操作关联 |
| API 接口 | `GREEN` 或 `BRIGHT_GREEN` | 绿色代表"开放/连接" |
| 缓存系统 | `MAGENTA` | 与快速/特殊处理关联 |
| 安全/认证 | `YELLOW` 或 `ORANGE` | 引起注意 |
| 错误处理 | `RED` 或 `BRIGHT_RED` | 清晰表示问题 |
| 警告 | `YELLOW` | 标准警告颜色 |
| 调试信息 | `GRAY` | 降低视觉优先级 |
| 成功/完成 | `GREEN` | 正面反馈 |
| 系统级 | `WHITE` 或 `BRIGHT_WHITE` | 中立、清晰 |

---

## Rich 颜色支持

Logger 使用的颜色由 `rich` 库支持。除了上述预定义的颜色外，你还可以使用 `rich` 支持的任何颜色和样式。

### Rich 支持的颜色示例

```python
from kernel.logger import get_logger

# 使用 RGB 颜色
logger = get_logger("app", color="rgb(100, 150, 200)")

# 使用十六进制颜色
logger = get_logger("app", color="#FF5733")

# 使用命名颜色
logger = get_logger("app", color="steel_blue")

# 使用样式
logger = get_logger("app", color="bold red")
logger = get_logger("app", color="italic blue")
logger = get_logger("app", color="underline green")

# 组合样式
logger = get_logger("app", color="bold italic cyan")
logger = get_logger("app", color="bright bold yellow")
```

### Rich 命名颜色列表（常用）

```
red, blue, green, yellow, magenta, cyan, white, black, gray,
orange, purple, pink, brown, navy, teal, olive, lime, silver,
maroon, coral, salmon, gold, khaki, turquoise, violet, indigo,
...
```

更多颜色请参考 [Rich 文档](https://rich.readthedocs.io)。

---

## 最佳实践

### 1. 为相关的日志记录器使用一致的颜色主题

```python
# ✓ 好的做法 - 使用一致的颜色主题
core_color = COLOR.BLUE
logger1 = get_logger("core.database", color=core_color)
logger2 = get_logger("core.cache", color=core_color)
logger3 = get_logger("core.scheduler", color=core_color)

# ✗ 不好的做法 - 颜色混乱
logger1 = get_logger("database", color=COLOR.RED)
logger2 = get_logger("cache", color=COLOR.YELLOW)
logger3 = get_logger("scheduler", color=COLOR.MAGENTA)
```

### 2. 为不同的关注点使用不同的颜色

```python
# ✓ 好的做法
system_logger = get_logger("system", color=COLOR.WHITE)
business_logger = get_logger("business", color=COLOR.GREEN)
error_logger = get_logger("error", color=COLOR.RED)
```

### 3. 避免过度使用亮色

```python
# ✗ 不好的做法 - 全是亮色，视觉疲劳
logger1 = get_logger("app1", color=COLOR.BRIGHT_RED)
logger2 = get_logger("app2", color=COLOR.BRIGHT_YELLOW)
logger3 = get_logger("app3", color=COLOR.BRIGHT_GREEN)

# ✓ 好的做法 - 混合使用
logger1 = get_logger("app1", color=COLOR.RED)
logger2 = get_logger("app2", color=COLOR.YELLOW)
logger3 = get_logger("app3", color=COLOR.GREEN)
```

### 4. 为明确的语义选择颜色

```python
# ✓ 好的做法 - 颜色反映语义
success_logger = get_logger("success", color=COLOR.GREEN)
warning_logger = get_logger("warning", color=COLOR.YELLOW)
failure_logger = get_logger("failure", color=COLOR.RED)
```

---

## 相关资源

- [Logger 主文档](./README.md) - Logger 使用指南
- [FileHandler 文档](./file_handler.md) - 文件处理
- [高级用法](./advanced.md) - 高级特性
