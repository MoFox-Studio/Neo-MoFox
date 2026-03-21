# Monitor 模块

## 概述

`monitor.py` 提供了 LLM 请求的指标收集、监控和分析功能。它能够追踪每次请求的性能数据（延迟、token 使用量、成本等），并生成聚合统计用于性能分析和优化。

## 核心类

### RequestMetrics

```python
@dataclass
class RequestMetrics:
    """单次请求的指标数据。"""
    
    model_name: str                    # 模型名称
    request_name: str                  # 请求名称
    latency: float                     # 延迟（秒）
    tokens_in: int | None = None       # 输入 token 数
    tokens_out: int | None = None      # 输出 token 数
    cost: float | None = None          # 请求成本（美元）
    success: bool = True               # 是否成功
    error: str | None = None           # 错误信息
    error_type: str | None = None      # 错误类型
    timestamp: datetime = ...           # 请求时间戳
    stream: bool = False               # 是否使用流式
    retry_count: int = 0               # 重试次数
    model_index: int = 0               # 模型索引
    extra: dict[str, Any] = ...        # 额外数据
```

**使用示例：**
```python
from src.kernel.llm import RequestMetrics
from datetime import datetime

metrics = RequestMetrics(
    model_name="gpt-4",
    request_name="query_1",
    latency=1.25,
    tokens_in=100,
    tokens_out=50,
    cost=0.003,
    success=True,
    stream=True,
    retry_count=0,
)

collector.record_request(metrics)
```

---

### ModelStats

```python
@dataclass
class ModelStats:
    """模型的统计数据。"""
    
    model_name: str                           # 模型名称
    total_requests: int = 0                   # 总请求数
    success_count: int = 0                    # 成功请求数
    error_count: int = 0                      # 失败请求数
    total_latency: float = 0.0                # 总延迟
    total_tokens_in: int = 0                  # 总输入 token
    total_tokens_out: int = 0                 # 总输出 token
    total_cost: float = 0.0                   # 总成本
    error_types: dict[str, int] = ...         # 错误类型统计
```

**计算属性：**

```python
@property
def success_rate(self) -> float:
    """成功率（0-1）"""
    if self.total_requests == 0:
        return 0.0
    return self.success_count / self.total_requests

@property
def avg_latency(self) -> float:
    """平均延迟（秒）"""
    if self.total_requests == 0:
        return 0.0
    return self.total_latency / self.total_requests

@property
def avg_cost(self) -> float:
    """平均成本（美元/请求）"""
    if self.total_requests == 0:
        return 0.0
    return self.total_cost / self.total_requests
```

**使用示例：**
```python
stats = collector.get_stats("gpt-4")

print(f"总请求: {stats.total_requests}")
print(f"成功率: {stats.success_rate:.2%}")
print(f"平均延迟: {stats.avg_latency:.3f}s")
print(f"平均成本: ${stats.avg_cost:.4f}")
print(f"错误类型: {stats.error_types}")
```

---

### MetricsCollector

```python
class MetricsCollector:
    """指标收集器。线程安全，用于收集和存储 LLM 请求的指标。"""
    
    def __init__(self, *, max_history: int = 10000) -> None:
        """
        Args:
            max_history: 最多保留的历史记录数（防止内存溢出）
        """
```

#### 核心方法

##### record_request

```python
def record_request(self, metrics: RequestMetrics) -> None:
    """记录一次请求。"""
```

记录单次请求的指标数据。自动更新 ModelStats。

**使用示例：**
```python
metrics = RequestMetrics(
    model_name="gpt-4",
    request_name="test",
    latency=1.5,
    success=True,
)
collector.record_request(metrics)
```

##### get_stats

```python
def get_stats(self, model_name: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    """获取统计数据。
    
    Args:
        model_name: 指定模型名称，若为 None 则返回所有模型的统计
    
    Returns:
        单个模型的统计字典或所有模型的统计列表
    """
```

返回指定模型或所有模型的聚合统计信息。

**使用示例：**
```python
# 获取特定模型统计
stats = collector.get_stats("gpt-4")
print(f"gpt-4 成功率: {stats['success_rate']:.2%}")
print(f"gpt-4 平均延迟: {stats['avg_latency']:.3f}s")

# 获取所有模型统计
all_stats = collector.get_stats()
for model_stats in all_stats:
    print(f"{model_stats['model_name']}: {model_stats['success_rate']:.2%}")
```

##### get_recent_history

```python
def get_recent_history(self, limit: int = 100) -> list[RequestMetrics]:
    """获取最近的请求历史。"""
```

获取最近 N 条请求记录。

**使用示例：**
```python
# 获取最后 10 条记录
recent = collector.get_recent_history(limit=10)
for metrics in recent:
    print(f"{metrics.model_name}: {metrics.latency:.3f}s, success={metrics.success}")
```

##### clear

```python
def clear(self) -> None:
    """清空所有统计数据。"""
```

清除所有的统计数据和历史记录。通常用于测试或重新开始统计。

**使用示例：**
```python
collector.clear()
# 所有统计重置
```

---

### RequestTimer

```python
class RequestTimer:
    """请求计时器（上下文管理器）。"""
    
    def __init__(self) -> None:
        """初始化计时器。"""
    
    @property
    def elapsed(self) -> float:
        """已用时间（秒）。"""
```

**使用示例：**
```python
from src.kernel.llm import RequestTimer

timer = RequestTimer()
with timer:
    message = await request.send(stream=False)
    message = await message

print(f"耗时: {timer.elapsed:.3f}s")
```

---

### get_global_collector

```python
def get_global_collector() -> MetricsCollector:
    """获取全局 MetricsCollector 实例。"""
```

返回应用全局唯一的指标收集器实例。

**使用示例：**
```python
from src.kernel.llm import get_global_collector

collector = get_global_collector()
stats = collector.get_stats("gpt-4")
```

---

## 使用模式

### 模式 1：自动指标收集

```python
# 启用指标收集（默认）
request = LLMRequest(model_set=models, enable_metrics=True)
request.add_payload(LLMPayload(ROLE.USER, Text("Query")))

response = await request.send()
message = await response

# 指标自动记录
collector = get_global_collector()
stats = collector.get_stats("gpt-4")
print(f"成功率: {stats.success_rate:.2%}")
```

### 模式 2：性能分析

```python
from src.kernel.llm import get_global_collector

# 进行多次请求
for i in range(100):
    request = LLMRequest(model_set=models)
    request.add_payload(LLMPayload(ROLE.USER, Text(f"Query {i}")))
    response = await request.send()
    await response

# 分析性能
collector = get_global_collector()
stats = collector.get_stats("gpt-4")

print(f"总请求: {stats.total_requests}")
print(f"成功: {stats.success_count}, 失败: {stats.error_count}")
print(f"成功率: {stats.success_rate:.2%}")
print(f"平均延迟: {stats.avg_latency:.3f}s")
print(f"总 token: {stats.total_tokens_in + stats.total_tokens_out}")
print(f"总成本: ${stats.total_cost:.4f}")
```

### 模式 3：多模型对比

```python
# 同时使用多个模型
models = [
    {"client_type": "openai", "model_identifier": "gpt-4", ...},
    {"client_type": "openai", "model_identifier": "gpt-3.5-turbo", ...},
]

for i in range(50):
    request = LLMRequest(model_set=models)
    request.add_payload(LLMPayload(ROLE.USER, Text(f"Query {i}")))
    response = await request.send()
    await response

# 对比性能
collector = get_global_collector()
for model_name in ["gpt-4", "gpt-3.5-turbo"]:
    stats = collector.get_stats(model_name)
    if stats:
        print(f"\n{model_name}:")
        print(f"  成功率: {stats.success_rate:.2%}")
        print(f"  平均延迟: {stats.avg_latency:.3f}s")
        print(f"  平均成本: ${stats.avg_cost:.4f}")
```

### 模式 4：错误监控

```python
# 进行多次请求
requests_to_make = [...]

for query in requests_to_make:
    request = LLMRequest(model_set=models)
    request.add_payload(LLMPayload(ROLE.USER, Text(query)))
    try:
        response = await request.send()
        await response
    except LLMError:
        pass  # 错误由 RequestMetrics 记录

# 分析错误
collector = get_global_collector()
stats = collector.get_stats("gpt-4")

print(f"失败数: {stats.error_count}")
print(f"错误类型:")
for error_type, count in stats.error_types.items():
    print(f"  {error_type}: {count}")
```

### 模式 5：手动记录指标

```python
from src.kernel.llm import RequestMetrics, get_global_collector
import time

# 手动测量和记录
start = time.time()
try:
    result = await some_llm_operation()
    elapsed = time.time() - start
    
    metrics = RequestMetrics(
        model_name="custom-model",
        request_name="custom_query",
        latency=elapsed,
        tokens_in=100,
        tokens_out=50,
        cost=0.001,
        success=True,
    )
except Exception as e:
    elapsed = time.time() - start
    metrics = RequestMetrics(
        model_name="custom-model",
        request_name="custom_query",
        latency=elapsed,
        success=False,
        error=str(e),
        error_type=type(e).__name__,
    )

collector = get_global_collector()
collector.record_request(metrics)
```

---

## 高级用法

### 导出统计数据

```python
collector = get_global_collector()

# 导出为字典
stats = collector.get_stats("gpt-4")
data = stats.to_dict()

import json
print(json.dumps(data, indent=2, default=str))
```

### 定时生成报告

```python
import asyncio
from src.kernel.llm import get_global_collector

async def generate_report():
    """每分钟生成一次性能报告。"""
    while True:
        await asyncio.sleep(60)
        
        collector = get_global_collector()
        all_stats = collector.list_all_stats()
        
        print("\n=== 性能报告 ===")
        for model_name, stats in all_stats.items():
            print(f"\n{model_name}:")
            print(f"  总请求: {stats.total_requests}")
            print(f"  成功率: {stats.success_rate:.2%}")
            print(f"  平均延迟: {stats.avg_latency:.3f}s")
            print(f"  平均成本: ${stats.avg_cost:.4f}")

# 在后台运行报告生成
asyncio.create_task(generate_report())
```

### 与日志集成

```python
import logging
from src.kernel.llm import RequestMetrics, get_global_collector

logger = logging.getLogger(__name__)

def log_metrics(metrics: RequestMetrics):
    """将指标记录到日志。"""
    if metrics.success:
        logger.info(
            f"LLM request succeeded",
            extra={
                "model": metrics.model_name,
                "latency": metrics.latency,
                "tokens_in": metrics.tokens_in,
                "tokens_out": metrics.tokens_out,
                "cost": metrics.cost,
            }
        )
    else:
        logger.error(
            f"LLM request failed",
            extra={
                "model": metrics.model_name,
                "error_type": metrics.error_type,
                "error": metrics.error,
            }
        )

# 在录制前调用
collector = get_global_collector()
for metrics in collector.get_history(limit=10):
    log_metrics(metrics)
```

---

## 常见问题

### Q: 历史记录会无限增长吗？

A: 不会。`MetricsCollector` 的 `max_history` 参数限制了历史记录数（默认 10000）。超过限制后会自动删除最旧的记录。

### Q: 能否按时间范围查询？

A: 可以。每条 `RequestMetrics` 都有 `timestamp`：
```python
from datetime import datetime, timedelta

collector = get_global_collector()
history = collector.get_history()
recent = [m for m in history if m.timestamp > datetime.now() - timedelta(hours=1)]
```

### Q: 是否线程安全？

A: 是的。`MetricsCollector` 使用 `threading.Lock` 保护所有数据访问，支持多线程使用。

### Q: 如何禁用指标收集？

A: 创建 `LLMRequest` 时设置 `enable_metrics=False`：
```python
request = LLMRequest(model_set=models, enable_metrics=False)
```

### Q: 能否重置统计信息？

A: 可以。调用 `clear_history()` 重置所有数据：
```python
collector = get_global_collector()
collector.clear_history()
```

---

## 性能考虑

### 1. 指标记录开销

指标记录是线程安全的，但有一定开销。对于高吞吐量场景，可以禁用：
```python
request = LLMRequest(model_set=models, enable_metrics=False)
```

### 2. 历史内存使用

默认保留 10000 条记录。对于长时间运行的应用，可以定期清除：
```python
collector = get_global_collector()
history = collector.get_history(limit=1000)  # 只保留最近 1000 条
collector.clear_history()  # 清除所有
```

### 3. 导出数据进行分析

定期导出数据，以便后续分析：
```python
stats = collector.get_stats("gpt-4")
export_to_database(stats)
collector.clear_history()
```

---

## 相关文档

- [Request 模块](./request.md) - 请求发送
- [Response 模块](./response.md) - 响应处理
- [Exceptions 模块](./exceptions.md) - 错误处理

