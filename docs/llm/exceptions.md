# Exceptions 模块

## 概述

`exceptions.py` 定义了 LLM 模块中使用的所有异常类，以及异常分类和转换函数。这些异常提供了统一、结构化的错误处理机制，帮助上层应用识别和处理不同类型的错误。

## 异常层次结构

```
BaseException
└── RuntimeError
    └── LLMError（所有 LLM 异常的基类）
        ├── LLMConfigurationError
        ├── LLMResponseConsumedError
        ├── LLMRateLimitError
        ├── LLMTimeoutError
        ├── LLMContentFilterError
        ├── LLMTokenLimitError
        ├── LLMAuthenticationError
        └── LLMAPIError
```

## 异常类详解

### LLMError
```python
class LLMError(RuntimeError):
    """LLM 操作的基础异常类。"""
```

所有 LLM 相关异常的基类。用于捕获任何 LLM 相关错误。

**使用示例：**
```python
try:
    response = await request.send()
except LLMError as e:
    print(f"LLM 操作失败: {e}")
```

---

### LLMConfigurationError
```python
class LLMConfigurationError(LLMError):
    """配置错误。"""
```

当提供的配置不完整或无效时抛出。例如：缺少 API key、model_set 格式错误、客户端未注册等。

**使用示例：**
```python
try:
    request = LLMRequest(model_set=None)
except LLMConfigurationError as e:
    print(f"配置错误: {e}")
```

---

### LLMResponseConsumedError
```python
class LLMResponseConsumedError(LLMError):
    """响应已被消费。"""
```

当尝试重复消费同一个 `LLMResponse` 对象时抛出。LLMResponse 是一次性的：要么通过 `await` 收集，要么通过 `async for` 流式处理，两种方式只能选一种。

**使用示例：**
```python
response = await request.send()

# 第一次消费（成功）
content1 = await response

# 第二次消费（失败）
try:
    content2 = await response  # ✗ 抛出 LLMResponseConsumedError
except LLMResponseConsumedError:
    print("响应已被消费，无法再次使用")
```

---

### LLMRateLimitError
```python
class LLMRateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None, model: str | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.model = model
```

当触发 API 速率限制（例如请求过于频繁）时抛出。

**属性：**
- `message`: 错误描述
- `retry_after`: 建议的重试等待时间（秒），可能为 None
- `model`: 触发错误的模型名称，可能为 None

**使用示例：**
```python
try:
    response = await request.send()
except LLMRateLimitError as e:
    if e.retry_after:
        print(f"速率限制，建议等待 {e.retry_after} 秒后重试")
    else:
        print(f"速率限制: {e.message}")
```

---

### LLMTimeoutError
```python
class LLMTimeoutError(LLMError):
    def __init__(self, message: str, timeout: float | None = None, model: str | None = None):
        super().__init__(message)
        self.timeout = timeout
        self.model = model
```

当请求超时时抛出。

**属性：**
- `message`: 错误描述
- `timeout`: 超时时间限制（秒），可能为 None
- `model`: 超时的模型名称，可能为 None

**使用示例：**
```python
try:
    response = await request.send()
except LLMTimeoutError as e:
    print(f"请求超时 ({e.timeout}s): {e.message}")
```

---

### LLMContentFilterError
```python
class LLMContentFilterError(LLMError):
    def __init__(self, message: str, filter_type: str | None = None, model: str | None = None):
        super().__init__(message)
        self.filter_type = filter_type
        self.model = model
```

当请求或响应内容被安全过滤器拒绝时抛出（违反内容政策）。

**属性：**
- `message`: 错误描述
- `filter_type`: 触发的过滤类型（如 "violence", "hate_speech" 等），可能为 None
- `model`: 执行过滤的模型名称，可能为 None

**使用示例：**
```python
try:
    response = await request.send()
except LLMContentFilterError as e:
    print(f"内容被过滤 ({e.filter_type}): {e.message}")
```

---

### LLMTokenLimitError
```python
class LLMTokenLimitError(LLMError):
    def __init__(self, message: str, max_tokens: int | None = None, 
                 requested_tokens: int | None = None, model: str | None = None):
        super().__init__(message)
        self.max_tokens = max_tokens
        self.requested_tokens = requested_tokens
        self.model = model
```

当请求或响应的 token 数量超过模型限制时抛出。

**属性：**
- `message`: 错误描述
- `max_tokens`: 模型的最大 token 限制
- `requested_tokens`: 请求的 token 数量
- `model`: 受限的模型名称，可能为 None

**使用示例：**
```python
try:
    response = await request.send()
except LLMTokenLimitError as e:
    print(f"超过 token 限制: 最多 {e.max_tokens}, 请求 {e.requested_tokens}")
```

---

### LLMAuthenticationError
```python
class LLMAuthenticationError(LLMError):
    def __init__(self, message: str, model: str | None = None):
        super().__init__(message)
        self.model = model
```

当 API 认证失败时抛出（如 API key 无效、过期或无权限）。

**属性：**
- `message`: 错误描述
- `model`: 认证失败的模型名称，可能为 None

**使用示例：**
```python
try:
    response = await request.send()
except LLMAuthenticationError as e:
    print(f"认证失败: 请检查 API key 是否正确 ({e.model})")
```

---

### LLMAPIError
```python
class LLMAPIError(LLMError):
    def __init__(self, message: str, status_code: int | None = None, 
                 error_code: str | None = None, model: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.model = model
```

通用 API 调用错误。当其他特定异常都不适用时使用。

**属性：**
- `message`: 错误描述
- `status_code`: HTTP 状态码（如 500, 503 等），可能为 None
- `error_code`: API 特定的错误码，可能为 None
- `model`: 出错的模型名称，可能为 None

**使用示例：**
```python
try:
    response = await request.send()
except LLMAPIError as e:
    print(f"API 错误 ({e.status_code}, {e.error_code}): {e.message}")
```

---

## 异常转换函数

### classify_exception
```python
def classify_exception(error: BaseException, model: str | None = None) -> BaseException:
    """将第三方 SDK 异常转换为标准化的 LLM 异常。

    这个函数尝试识别常见的 API 错误类型并转换为更具体的异常类型。
    如果无法识别，返回原始异常。
    """
```

**功能：**
将来自第三方 SDK（如 OpenAI SDK）的异常转换为标准化的 LLM 异常，便于统一处理。

**支持的转换：**
- OpenAI `RateLimitError` → `LLMRateLimitError`
- OpenAI `APITimeoutError` → `LLMTimeoutError`
- OpenAI `AuthenticationError` → `LLMAuthenticationError`
- OpenAI `BadRequestError` → 可能转换为 `LLMTokenLimitError` 或 `LLMContentFilterError`
- OpenAI `APIError` → `LLMAPIError`

**使用示例：**
```python
try:
    # 某个第三方 SDK 调用
    result = await openai_client.chat.completions.create(...)
except Exception as e:
    llm_error = classify_exception(e, model="gpt-4")
    raise llm_error  # 转换后的标准异常
```

---

## 错误处理最佳实践

### 1. 分层处理异常

```python
try:
    response = await request.send()
except LLMRateLimitError as e:
    # 处理速率限制：重试、等待
    await asyncio.sleep(e.retry_after or 5)
except LLMTimeoutError as e:
    # 处理超时：降级策略、改用其他模型
    logger.warning(f"请求超时: {e.message}")
except LLMAuthenticationError as e:
    # 处理认证失败：需要人工干预
    logger.error(f"认证失败，请检查配置: {e.message}")
except LLMError as e:
    # 处理其他 LLM 错误
    logger.error(f"LLM 操作失败: {e}")
```

### 2. 提取错误上下文

```python
try:
    response = await request.send()
except LLMTokenLimitError as e:
    print(f"处理过长的请求:")
    print(f"  最大 token: {e.max_tokens}")
    print(f"  请求 token: {e.requested_tokens}")
    print(f"  超出: {e.requested_tokens - e.max_tokens}")
except LLMRateLimitError as e:
    print(f"处理速率限制:")
    print(f"  建议等待: {e.retry_after}s")
    print(f"  模型: {e.model}")
```

### 3. 日志记录和监控

```python
import logging

logger = logging.getLogger(__name__)

try:
    response = await request.send()
except LLMError as e:
    logger.error(
        "LLM request failed",
        exc_info=True,
        extra={
            "error_type": type(e).__name__,
            "error_code": getattr(e, "error_code", None),
            "status_code": getattr(e, "status_code", None),
            "model": getattr(e, "model", None),
        }
    )
```

---

## 内部实现细节

### OpenAI SDK 异常映射

```python
# 在 classify_exception 函数中实现
from openai import (
    APITimeoutError,
    RateLimitError,
    AuthenticationError,
    BadRequestError,
    APIError,
)

# RateLimitError → LLMRateLimitError
# 提取 retry_after（如果可用）

# APITimeoutError → LLMTimeoutError
# 保留超时信息

# AuthenticationError → LLMAuthenticationError
# 标记为认证错误

# BadRequestError → 根据消息内容判断
# 可能是 TokenLimitError 或 ContentFilterError

# APIError → LLMAPIError
# 保留 HTTP 状态码和错误码
```

### 扩展异常系统

要支持新的异常类型：

1. 在此文件中定义新的异常类（继承 `LLMError`）
2. 在 `classify_exception` 中添加转换逻辑
3. 在模块的 `__init__.py` 中导出

---

## 常见问题

### Q: 为什么同一个异常被触发多次？

A: 这通常说明重试策略失效。检查 `policy` 配置是否正确，或查看 `max_retry` 是否为 0。

### Q: 如何区分临时错误和永久错误？

A: 临时错误（速率限制、超时）通常有 `retry_after` 或建议重试的策略。永久错误（认证失败、配置错误）通常需要修复配置。

### Q: 能否自定义异常类？

A: 可以。继承 `LLMError` 或特定子类，并在应用层捕获处理即可。

---

## 相关文档

- [Request 模块](./request.md)
- [Monitor 模块](./monitor.md)
- [Policy 模块](./policy/README.md)
