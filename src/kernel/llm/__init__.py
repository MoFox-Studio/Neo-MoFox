"""LLM request framework.

对齐《MoFox 重构指导总览》中 kernel/llm 设计稿：
- `LLMRequest`：构建 payloads 并发起请求
- `LLMResponse`：同时支持 `await`（收集全量）与 `async for`（流式）
- `LLMPayload`：`role + content` 的标准消息单元

本模块不依赖 core/config 的实现细节；上层需要传入 `model_set`：
- `list[dict]`，每个元素表示一个“模型实例”的完整配置（api_provider/base_url/model_identifier/api_key/...）。

负载均衡与重试策略由 `kernel.llm.policy` 承担。
"""

from .roles import ROLE
from .context import LLMContextManager
from .request import LLMRequest
from .response import LLMResponse
from .types import ModelEntry, ModelSet

from .payload import (
	Audio,
	Content,
	Image,
	LLMPayload,
	LLMUsable,
	Text,
	Tool,
	ToolCall,
	ToolResult,
	ToolRegistry,
	ToolExecutor,
)

from .monitor import (
	RequestMetrics,
	ModelStats,
	MetricsCollector,
	RequestTimer,
	get_global_collector,
)

from .exceptions import (
	LLMError,
	LLMConfigurationError,
	LLMResponseConsumedError,
	LLMRateLimitError,
	LLMTimeoutError,
	LLMContentFilterError,
	LLMTokenLimitError,
	LLMAuthenticationError,
	LLMAPIError,
	classify_exception,
)

__all__ = [
	# 核心类
	"ROLE",
	"LLMRequest",
	"LLMContextManager",
	"LLMResponse",
	"LLMPayload",
	# 类型定义
	"ModelEntry",
	"ModelSet",
	# 内容类型
	"Content",
	"Text",
	"Image",
	"Audio",
	# 工具相关
	"Tool",
	"ToolResult",
	"ToolCall",
	"LLMUsable",
	"ToolRegistry",
	"ToolExecutor",
	# 监控相关
	"RequestMetrics",
	"ModelStats",
	"MetricsCollector",
	"RequestTimer",
	"get_global_collector",
	# 异常相关
	"LLMError",
	"LLMConfigurationError",
	"LLMResponseConsumedError",
	"LLMRateLimitError",
	"LLMTimeoutError",
	"LLMContentFilterError",
	"LLMTokenLimitError",
	"LLMAuthenticationError",
	"LLMAPIError",
	"classify_exception",
]
