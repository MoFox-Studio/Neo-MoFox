"""Rerank 请求模块。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Self

from .exceptions import LLMConfigurationError, classify_exception
from .model_client import ModelClientRegistry
from .monitor import RequestMetrics, RequestTimer, get_global_collector
from .policy import create_default_policy
from .policy.base import Policy
from .request import _validate_model_entry, _validate_model_set
from .rerank_response import RerankItem, RerankResponse
from .types import ModelSet, RequestType


@dataclass(slots=True)
class RerankRequest:
    """RerankRequest：构建排序输入并执行请求。"""

    model_set: ModelSet
    request_name: str = ""
    query: str = ""
    documents: list[Any] = field(default_factory=list)
    top_n: int | None = None
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    enable_metrics: bool = True
    request_type: RequestType = RequestType.RERANK

    def __post_init__(self) -> None:
        if self.policy is None:
            self.policy = create_default_policy()
        if self.clients is None:
            self.clients = ModelClientRegistry()

    def set_query(self, value: str) -> Self:
        """设置 rerank 查询文本。"""
        self.query = value
        return self

    def add_document(self, value: Any) -> Self:
        """追加待排序文档。"""
        self.documents.append(value)
        return self

    async def send(self) -> RerankResponse:
        """发送 rerank 请求。"""
        if not self.query:
            raise LLMConfigurationError("rerank query 不能为空")
        if not self.documents:
            raise LLMConfigurationError("rerank documents 不能为空")

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
            client = self.clients.get_rerank_client_for_model(model)
            timer = RequestTimer()

            try:
                with timer:
                    timeout_seconds = model.get("timeout")
                    create_task = client.create_rerank(
                        model_name=model_identifier,
                        query=self.query,
                        documents=list(self.documents),
                        top_n=self.top_n,
                        request_name=self.request_name,
                        model_set=model,
                    )
                    if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
                        results = await asyncio.wait_for(
                            create_task,
                            timeout=float(timeout_seconds),
                        )
                    else:
                        results = await create_task

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
                items = [
                    RerankItem(
                        index=int(item.get("index", 0)),
                        score=float(item.get("score", 0.0)),
                        document=item.get("document"),
                    )
                    for item in results
                ]
                return RerankResponse(
                    results=items,
                    model_name=model_identifier,
                    request_name=self.request_name,
                )
            except BaseException as e:
                classified_error = classify_exception(e, model=model_identifier)
                last_error = classified_error

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
