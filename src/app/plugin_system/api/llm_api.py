"""插件侧 LLM API 便捷入口。

除请求构造和模型配置查询外，本模块也导出统一 tool call 执行函数：
``exec_llm_usable`` 用于执行单个组件，``run_tool_call``
用于执行一次响应中的一批工具调用，并保持 TOOL_RESULT 写回顺序。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.llm import (
    EmbeddingRequest,
    LLMContextManager,
    LLMRequest,
    LLMUsable,
    ModelSet,
    RerankRequest,
    ToolRegistry,
)
from src.core.config import get_model_config
from src.core.utils.llm_tool_call import (
    exec_llm_usable,
    run_tool_call,
)

__all__ = [
    "create_llm_request",
    "create_embedding_request",
    "create_rerank_request",
    "get_model_set_by_task",
    "get_model_set_by_name",
    "create_tool_registry",
    "exec_llm_usable",
    "run_tool_call",
]

if TYPE_CHECKING:
    from src.core.prompt import SystemReminderBucket


def create_llm_request(
    model_set: ModelSet,
    request_name: str = "",
    context_manager: LLMContextManager | None = None,
    with_reminder: str | SystemReminderBucket | None = None,
) -> LLMRequest:
    """创建 LLMRequest 实例

    Args:
        model_set: 模型集
        request_name: 请求名称（可选）
        context_manager: 上下文管理器（可选）
        with_reminder: 可选的 system reminder bucket；传入后会自动登记到上下文管理器

    Returns:
        LLMRequest 实例
    """
    request = LLMRequest(
        model_set=model_set,
        request_name=request_name,
        context_manager=context_manager,
    )

    if with_reminder is not None and request.context_manager is not None:
        request.context_manager.reminder_bucket(str(with_reminder), wrap_with_system_tag=True)

    return request


def create_embedding_request(
    model_set: ModelSet,
    request_name: str = "",
    inputs: list[str] | None = None,
) -> EmbeddingRequest:
    """创建 EmbeddingRequest 实例。

    Args:
        model_set: 模型集
        request_name: 请求名称（可选）
        inputs: 输入文本列表（可选）

    Returns:
        EmbeddingRequest 实例
    """
    return EmbeddingRequest(
        model_set=model_set,
        request_name=request_name,
        inputs=list(inputs or []),
    )


def create_rerank_request(
    model_set: ModelSet,
    request_name: str = "",
    query: str = "",
    documents: list[Any] | None = None,
    top_n: int | None = None,
) -> RerankRequest:
    """创建 RerankRequest 实例。

    Args:
        model_set: 模型集
        request_name: 请求名称（可选）
        query: 查询文本
        documents: 文档列表（可选）
        top_n: 返回结果数量（可选）

    Returns:
        RerankRequest 实例
    """
    return RerankRequest(
        model_set=model_set,
        request_name=request_name,
        query=query,
        documents=list(documents or []),
        top_n=top_n,
    )

def get_model_set_by_task(name: str) -> ModelSet:
    """根据任务名称获取 ModelSet

    Args:
        name: 模型集名称

    Returns:
        ModelSet 实例
    """
    return get_model_config().get_task(name)


def get_model_set_by_name(
    model_name: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ModelSet:
    """根据模型名称获取 ModelSet
    
    通过模型内部标识符直接获取可用于 LLMRequest 的 ModelSet，
    无需预先配置任务。所有参数都是可选的，None 时使用合理的默认值。
    
    Args:
        model_name: 模型名称（config/model.toml 中 models 列表里的 name）
        temperature: 温度参数，None 时使用默认值 0.7
        max_tokens: 最大输出 token 数，None 时使用默认值 800
        
    Returns:
        ModelSet: 包含单个模型配置的列表
        
    Raises:
        KeyError: 如果模型或其提供商未找到
        
    Examples:
        ```python
        from src.app.plugin_system.api import llm_api
        
        model_set = llm_api.get_model_set_by_name("gpt-4")
        request = llm_api.create_llm_request(model_set, request_name="chat")
        ```
    """
    return get_model_config().get_model_set_by_name(
        model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def create_tool_registry(tools: list[type[LLMUsable]] | None = None) -> ToolRegistry:
    """创建工具注册表实例。

    Args:
        tools: 工具类列表，可选

    Returns:
        工具注册表实例
    """
    registry = ToolRegistry()
    if tools:
        for tool in tools:
            registry.register(tool)
    return registry
