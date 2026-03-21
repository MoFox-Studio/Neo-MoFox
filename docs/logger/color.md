# Color 模块

## 概述

`src.kernel.logger.color` 定义 Logger 使用的颜色枚举与转换函数。

模块导出：

- `COLOR`
- `get_rich_color()`
- `DEFAULT_LEVEL_COLORS`

---

## `COLOR` 枚举

```python
class COLOR(Enum):
    BLACK = "black"
    RED = "red"
    GREEN = "green"
    YELLOW = "yellow"
    BLUE = "blue"
    MAGENTA = "magenta"
    CYAN = "cyan"
    WHITE = "white"

    BRIGHT_BLACK = "bright_black"
    BRIGHT_RED = "bright_red"
    BRIGHT_GREEN = "bright_green"
    BRIGHT_YELLOW = "bright_yellow"
    BRIGHT_BLUE = "bright_blue"
    BRIGHT_MAGENTA = "bright_magenta"
    BRIGHT_CYAN = "bright_cyan"
    BRIGHT_WHITE = "bright_white"

    GRAY = "grey50"
    ORANGE = "orange"
    PURPLE = "purple"
    PINK = "deep_pink"

    DEBUG = "dim"
    INFO = "blue"
    WARNING = "yellow"
    ERROR = "red"
    CRITICAL = "bold red"
```

---

## 分类说明

### 基础颜色

- `COLOR.BLACK`
- `COLOR.RED`
- `COLOR.GREEN`
- `COLOR.YELLOW`
- `COLOR.BLUE`
- `COLOR.MAGENTA`
- `COLOR.CYAN`
- `COLOR.WHITE`

### 明亮颜色

- `COLOR.BRIGHT_BLACK`
- `COLOR.BRIGHT_RED`
- `COLOR.BRIGHT_GREEN`
- `COLOR.BRIGHT_YELLOW`
- `COLOR.BRIGHT_BLUE`
- `COLOR.BRIGHT_MAGENTA`
- `COLOR.BRIGHT_CYAN`
- `COLOR.BRIGHT_WHITE`

### 扩展颜色

- `COLOR.GRAY`
- `COLOR.ORANGE`
- `COLOR.PURPLE`
- `COLOR.PINK`

### 日志级别推荐颜色

- `COLOR.DEBUG`
- `COLOR.INFO`
- `COLOR.WARNING`
- `COLOR.ERROR`
- `COLOR.CRITICAL`

---

## `get_rich_color()`

```python
get_rich_color(color: COLOR | str) -> str
```

作用：

- 传入 `COLOR` 枚举时，返回其 `value`
- 传入字符串时，直接转为 `str` 返回

示例：

```python
from src.kernel.logger.color import COLOR, get_rich_color

get_rich_color(COLOR.BLUE)          # "blue"
get_rich_color("bold magenta")     # "bold magenta"
get_rich_color("#7DCFFF")          # "#7DCFFF"
```

---

## `DEFAULT_LEVEL_COLORS`

```python
DEFAULT_LEVEL_COLORS = {
    "DEBUG": COLOR.DEBUG,
    "INFO": COLOR.INFO,
    "WARNING": COLOR.WARNING,
    "ERROR": COLOR.ERROR,
    "CRITICAL": COLOR.CRITICAL,
}
```

该映射表达的是“日志级别推荐颜色”。

在 `Logger._log()` 中，各级别日志输出颜色即按这一语义组织：

- `DEBUG` 使用 dim 风格
- `INFO` 使用蓝色
- `WARNING` 使用黄色
- `ERROR` 使用红色
- `CRITICAL` 使用加粗红色

---

## 使用示例

### 为 logger 指定固定颜色

```python
from src.kernel.logger import COLOR, get_logger

db_logger = get_logger("kernel.db", display="DB", color=COLOR.CYAN)
plugin_logger = get_logger("core.plugin", display="Plugin", color=COLOR.GREEN)
```

### 直接使用 Rich 样式字符串

```python
from src.kernel.logger import get_logger

logger = get_logger("app.runtime", color="bold yellow")
```

### 使用十六进制颜色

```python
from src.kernel.logger import get_logger

logger = get_logger("brand", color="#5E81AC")
```

---

## 默认颜色策略

当 `get_logger()` 的 `color` 参数为 `None` 时，logger 模块不会退回 `COLOR.WHITE`，而是：

1. 对 `name` 做标准化
2. 使用 MD5 摘要计算颜色索引
3. 从内部默认调色板中选出稳定颜色

这意味着：

- 同一个 `name` 在不同运行中通常得到同样的默认颜色
- 不同模块无需手动配色也能较好区分

示例：

```python
from src.kernel.logger import get_logger

logger_a = get_logger("kernel.db")
logger_b = get_logger("kernel.event")
logger_c = get_logger("core.plugin")
```

---

## 选择建议

| 场景 | 推荐颜色 |
| --- | --- |
| 核心基础设施 | `COLOR.CYAN` / `COLOR.BLUE` |
| 业务组件 | `COLOR.GREEN` |
| 风险提示 | `COLOR.YELLOW` / `COLOR.ORANGE` |
| 错误处理 | `COLOR.RED` |
| 调试模块 | `COLOR.GRAY` / `COLOR.DEBUG` |
| 强调模块 | `bold magenta` / `#7DCFFF` |

---

## 注意事项

- `COLOR` 只是预设集合，不限制你传入任意 Rich 样式字符串
- `Logger` 名称颜色与日志级别颜色是两个概念
- `print_panel()` 默认边框颜色取 logger 自身颜色，而不是日志级别颜色
