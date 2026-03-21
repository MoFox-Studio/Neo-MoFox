# Policy 模块

## 概述

`policy/` 子模块实现了负载均衡和重试策略。它决定了在多个模型之间如何轮流尝试、何时重试、如何处理错误等。

## 模块结构

```
policy/
├── base.py           # 策略接口定义
├── round_robin.py    # 轮询策略实现
└── __init__.py       # 公开 API
```

## 核心接口

### ModelStep（步骤信息）

```python
@dataclass(frozen=True, slots=True)
class ModelStep:
    """下一步执行计划。
    
    - model=None 表示策略耗尽，应停止重试并把最后一次异常抛给上层。
    - delay_seconds 由 policy 决定（例如 retry_interval）。
    """
    
    model: dict[str, Any] | None       # 下一个要尝试的模型配置
    delay_seconds: float = 0.0          # 延迟时间（秒）
    meta: dict[str, Any] | None = None # 元数据（模型索引、重试次数等）
```

表示策略决定的下一步行动。

---

### PolicySession 协议

```python
class PolicySession(Protocol):
    def first(self) -> ModelStep:
        """获取初始的模型步骤。"""
        ...
    
    def next_after_error(self, error: BaseException) -> ModelStep:
        """基于错误获取下一步。"""
        ...
```

表示单次请求的策略会话。

---

### Policy 协议

```python
class Policy(Protocol):
    def new_session(self, *, model_set: Any, request_name: str) -> PolicySession:
        """为新请求创建会话。"""
        ...
```

定义了所有策略必须实现的接口。

---

## RoundRobinPolicy（轮询策略）

```python
class RoundRobinPolicy(Policy):
    """简单轮询：在 `model_set`（list[dict]）上循环选择。"""
```

默认的负载均衡策略，按轮询方式在模型间切换。

### 工作原理

1. **初始选择**：从当前轮询位置开始
2. **重试**：在当前模型上重试 `max_retry` 次
3. **切换**：重试耗尽后切换到下一个模型
4. **终止**：所有模型都耗尽时停止

### 使用示例

```python
from src.kernel.llm import LLMRequest, RoundRobinPolicy

models = [
    {
        "client_type": "openai",
        "model_identifier": "gpt-4",
        "api_key": "key1",
        "max_retry": 2,           # 该模型重试 2 次
        "retry_interval": 1.0,    # 重试间隔 1 秒
    },
    {
        "client_type": "openai",
        "model_identifier": "gpt-3.5-turbo",
        "api_key": "key2",
        "max_retry": 3,           # 该模型重试 3 次
        "retry_interval": 0.5,    # 重试间隔 0.5 秒
    }
]

# 使用默认的 RoundRobinPolicy
request = LLMRequest(model_set=models)

# 或显式指定
policy = RoundRobinPolicy()
request = LLMRequest(model_set=models, policy=policy)
```

### 执行流程

```
开始
  ↓
选择 gpt-4 (模型 0)
  ↓
[第 1 次尝试]
  ├─ 成功 → 返回结果
  └─ 失败 → 重试
  ↓
[第 2 次尝试]
  ├─ 成功 → 返回结果
  └─ 失败 → 重试超过 max_retry(2)
  ↓
切换到 gpt-3.5-turbo (模型 1)，等待 0.5s
  ↓
[第 1 次尝试]
  ├─ 成功 → 返回结果
  └─ 失败 → 重试
  ↓
[第 2、3 次尝试]
  ...
  ├─ 成功 → 返回结果
  └─ 全部失败 → 耗尽
  ↓
终止，抛出异常
```

### 配置参数详解

#### max_retry

```python
{
    "max_retry": 3,  # 该模型最多重试 3 次
}
```

- 默认值：0（不重试，只尝试一次）
- 范围：0-∞
- 说明：同一模型的最大重试次数

#### retry_interval

```python
{
    "retry_interval": 1.5,  # 重试等待 1.5 秒
}
```

- 默认值：0（无延迟）
- 范围：0-∞
- 说明：重试间的等待时间（秒）

### ModelStep 元数据

```python
step = session.first()
print(step.meta)
# {'model_index': 0, 'attempt': 1}

step = session.next_after_error(error)
print(step.meta)
# {'model_index': 1, 'attempt': 3, 'switch': True, 'retry': 1}
```

元数据包含：
- `model_index`: 当前模型的索引
- `attempt`: 总尝试次数
- `retry`: 当前模型的重试次数
- `switch`: 是否刚切换模型

---

## 使用模式

### 模式 1：基础故障转移

```python
models = [
    {"client_type": "openai", "model_identifier": "gpt-4", "api_key": "key1", "max_retry": 2},
    {"client_type": "openai", "model_identifier": "gpt-3.5-turbo", "api_key": "key2", "max_retry": 2},
]

request = LLMRequest(model_set=models)
request.add_payload(LLMPayload(ROLE.USER, Text("Query")))

response = await request.send()
message = await response
# 框架会自动尝试 gpt-4 两次，然后尝试 gpt-3.5-turbo 两次
```

### 模式 2：多模型负载均衡

```python
models = [
    {"client_type": "openai", "model_identifier": "gpt-4", "api_key": "key1", "max_retry": 1},
    {"client_type": "openai", "model_identifier": "gpt-4", "api_key": "key2", "max_retry": 1},  # 不同 API key
    {"client_type": "openai", "model_identifier": "gpt-4", "api_key": "key3", "max_retry": 1},
]

# 轮询方式分散负载
request = LLMRequest(model_set=models, request_name="distributed_query")
```

### 模式 3：高可用性

```python
models = [
    {
        "client_type": "openai",
        "model_identifier": "gpt-4",
        "api_key": "primary_key",
        "max_retry": 3,           # 主模型重试多次
        "retry_interval": 2.0,    # 等待较长时间
    },
    {
        "client_type": "openai",
        "model_identifier": "gpt-3.5-turbo",
        "api_key": "backup_key",
        "max_retry": 1,
        "retry_interval": 1.0,
    }
]

request = LLMRequest(model_set=models)
```

---

## 扩展自定义策略

### 实现自定义策略

```python
from src.kernel.llm.policy import Policy, PolicySession, ModelStep

class CustomPolicy(Policy):
    """自定义策略：根据模型性能选择。"""
    
    def __init__(self):
        self.model_scores = {}  # 模型性能分数
    
    def new_session(self, *, model_set, request_name):
        return CustomPolicySession(model_set, self.model_scores)

class CustomPolicySession(PolicySession):
    def __init__(self, model_set, scores):
        self.model_set = model_set
        self.scores = scores
        self.current_idx = self._select_best_model()
        self.attempted_models = set()
    
    def _select_best_model(self):
        """选择性能最好的模型。"""
        best_idx = 0
        best_score = float('-inf')
        
        for i, model in enumerate(self.model_set):
            score = self.scores.get(model.get("model_identifier", i), 0)
            if score > best_score:
                best_score = score
                best_idx = i
        
        return best_idx
    
    def first(self) -> ModelStep:
        model = self.model_set[self.current_idx]
        self.attempted_models.add(self.current_idx)
        return ModelStep(model=model, meta={"model_index": self.current_idx})
    
    def next_after_error(self, error: BaseException) -> ModelStep:
        # 不在已尝试模型上再试
        for i, model in enumerate(self.model_set):
            if i not in self.attempted_models:
                self.attempted_models.add(i)
                return ModelStep(model=model, meta={"model_index": i, "switch": True})
        
        return ModelStep(model=None, meta={"reason": "all_models_exhausted"})

# 使用自定义策略
policy = CustomPolicy()
request = LLMRequest(model_set=models, policy=policy)
```

### 基于错误类型的策略

```python
class ErrorAwarePolicy(Policy):
    """根据错误类型调整策略。"""
    
    def new_session(self, *, model_set, request_name):
        return ErrorAwarePolicySession(model_set)

class ErrorAwarePolicySession(PolicySession):
    def __init__(self, model_set):
        self.model_set = model_set
        self.current_idx = 0
        self.retry_count = 0
    
    def first(self) -> ModelStep:
        return ModelStep(model=self.model_set[self.current_idx])
    
    def next_after_error(self, error: BaseException) -> ModelStep:
        from src.kernel.llm import (
            LLMRateLimitError,
            LLMTimeoutError,
            LLMAuthenticationError
        )
        
        # 速率限制：等待后重试同模型
        if isinstance(error, LLMRateLimitError):
            return ModelStep(
                model=self.model_set[self.current_idx],
                delay_seconds=error.retry_after or 5.0,
                meta={"reason": "rate_limit", "retry": True}
            )
        
        # 超时：立即切换模型
        if isinstance(error, LLMTimeoutError):
            self.current_idx = (self.current_idx + 1) % len(self.model_set)
            return ModelStep(
                model=self.model_set[self.current_idx],
                meta={"reason": "timeout", "switch": True}
            )
        
        # 认证错误：不重试
        if isinstance(error, LLMAuthenticationError):
            self.current_idx = (self.current_idx + 1) % len(self.model_set)
            return ModelStep(
                model=self.model_set[self.current_idx],
                meta={"reason": "auth_error", "switch": True}
            )
        
        # 其他错误：正常轮询
        self.current_idx = (self.current_idx + 1) % len(self.model_set)
        return ModelStep(model=self.model_set[self.current_idx])
```

---

## 常见问题

### Q: 如何禁用重试？

A: 设置 `max_retry: 0`：
```python
models = [{"max_retry": 0, ...}]
```

### Q: 如何控制重试延迟？

A: 使用 `retry_interval`：
```python
models = [{"max_retry": 3, "retry_interval": 2.0}]
```

### Q: 能否实现指数退避？

A: 可以。创建自定义策略：
```python
class ExponentialBackoffPolicy(Policy):
    def next_after_error(self, error):
        delay = 2 ** self.retry_count  # 指数退避
        return ModelStep(..., delay_seconds=delay)
```

### Q: 如何记录重试信息？

A: 使用 `ModelStep.meta`：
```python
step = session.next_after_error(error)
logger.info(f"Retry: {step.meta}")
```

---

## 最佳实践

### 1. 配置合理的重试次数

```python
# ✓ 合理配置
models = [
    {"max_retry": 2, "retry_interval": 1.0},  # 快速重试
    {"max_retry": 1, "retry_interval": 2.0},  # 备用模型，重试少
]

# ✗ 不合理
models = [
    {"max_retry": 100},  # 太多重试
    {"max_retry": -1},   # 无效配置
]
```

### 2. 使用多个模型提高可用性

```python
models = [
    {"client_type": "openai", "model_identifier": "gpt-4", ...},
    {"client_type": "openai", "model_identifier": "gpt-3.5-turbo", ...},
]
```

### 3. 监控重试

```python
from src.kernel.llm import get_global_collector

collector = get_global_collector()
stats = collector.get_stats("gpt-4")
print(f"重试次数: {stats.extra.get('retry_count', 0)}")
```

---

## 相关文档

- [Request 模块](../request.md)
- [Response 模块](../response.md)
- [Exceptions 模块](../exceptions.md)

