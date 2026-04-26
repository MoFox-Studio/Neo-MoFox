"""Embedding 请求模块。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Self

from src.kernel.logger import get_logger

from .exceptions import LLMAPIError, LLMConfigurationError, LLMRateLimitError, LLMTimeoutError, classify_exception
from .model_client import ModelClientRegistry
from .monitor import RequestMetrics, RequestTimer, get_global_collector
from .policy import create_default_policy
from .policy.base import Policy
from .request import _validate_model_entry, _validate_model_set
from .types import ModelSet, RequestType
from .embedding_response import EmbeddingResponse

logger = get_logger("kernel.llm.embedding", display="LLM Embedding")


@dataclass(slots=True)
class EmbeddingRequest:
    """EmbeddingRequest：构建输入并执行向量请求。"""

    model_set: ModelSet
    request_name: str = ""
    inputs: list[str] = field(default_factory=list)
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.EMBEDDINGS

    def __post_init__(self) -> None:
        if self.policy is None:
            self.policy = create_default_policy()
        if self.clients is None:
            self.clients = ModelClientRegistry()

    def add_input(self, value: str) -> Self:
        """追加 embedding 输入文本。"""
        self.inputs.append(value)
        return self

    async def send(self) -> EmbeddingResponse:
        """发送 embedding 请求。"""
        if not self.inputs:
            raise LLMConfigurationError("embedding inputs 不能为空")

        model_set = _validate_model_set(self.model_set)
        assert self.policy is not None
        session = self.policy.new_session(model_set=model_set, request_name=self.request_name)

        last_error: BaseException | None = None
        retry_count = 0
        step = session.first()

        while step.model is not None:
            model = _validate_model_entry(step.model)
            model_identifier = model.get("model_identifier")
            if not isinstance(model_identifier, str) or not model_identifier:
                raise LLMConfigurationError("model.model_identifier 必须是非空字符串")

            if step.delay_seconds and step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)

            assert self.clients is not None
            client = self.clients.get_embedding_client_for_model(model)
            timer = RequestTimer()

            try:
                with timer:
                    timeout_seconds = model.get("timeout")
                    create_task = client.create_embedding(
                        model_name=model_identifier,
                        inputs=list(self.inputs),
                        request_name=self.request_name,
                        model_set=model,
                    )
                    if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
                        embeddings = await asyncio.wait_for(
                            create_task,
                            timeout=float(timeout_seconds),
                        )
                    else:
                        embeddings = await create_task

                if self.enable_metrics:
                    metrics = RequestMetrics(
                        model_name=model_identifier,
                        request_name=self.request_name,
                        latency=timer.elapsed,
                        success=True,
                        stream=False,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                    get_global_collector().record_request(metrics)

                session.record_success(latency=timer.elapsed)
                return EmbeddingResponse(
                    embeddings=embeddings,
                    model_name=model_identifier,
                    request_name=self.request_name,
                )
            except BaseException as e:
                classified_error = classify_exception(e, model=model_identifier)
                last_error = classified_error

                _err_type = type(classified_error).__name__
                if isinstance(classified_error, asyncio.CancelledError):
                    logger.debug(
                        f"Embedding 请求被取消: model={model_identifier}, request={self.request_name or '__default__'}",
                        exc_info=True,
                    )
                elif (
                    isinstance(classified_error, (LLMTimeoutError, LLMRateLimitError, TimeoutError))
                    or (isinstance(classified_error, LLMAPIError) and classified_error.status_code is None)
                ):
                    logger.warning(
                        f"Embedding 请求暂时失败: model={model_identifier}, "
                        f"request={self.request_name or '__default__'}, error_type={_err_type}"
                    )
                    logger.debug(
                        f"Embedding 请求暂时失败（详情）: model={model_identifier}, "
                        f"request={self.request_name or '__default__'}, reason={classified_error}",
                        exc_info=True,
                    )
                else:
                    logger.error(
                        f"Embedding 请求失败: model={model_identifier}, request={self.request_name or '__default__'}, "
                        f"error_type={_err_type}, reason={classified_error}",
                        exc_info=True,
                    )

                if self.enable_metrics:
                    metrics = RequestMetrics(
                        model_name=model_identifier,
                        request_name=self.request_name,
                        latency=timer.elapsed,
                        success=False,
                        error=str(classified_error),
                        error_type=type(classified_error).__name__,
                        stream=False,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                    get_global_collector().record_request(metrics)

                retry_count += 1
                step = session.next_after_error(classified_error)

        assert last_error is not None
        raise last_error
