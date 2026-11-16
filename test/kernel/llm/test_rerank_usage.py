"""PR181：HTTP rerank 的真实用量必须进入请求观测链路。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.kernel.llm.model_client import openai_client
from src.kernel.llm.model_client.openai_client import OpenAIChatClient
from src.kernel.llm.request_inspector import get_inspector
from src.kernel.llm.rerank_request import RerankRequest


@pytest.mark.parametrize("entrypoint", ["client", "request"])
@pytest.mark.parametrize(
    ("usage", "tokens_in", "tokens_out"),
    [
        pytest.param(
            {
                "prompt_tokens": 17,
                "completion_tokens": 3,
                "total_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 5},
            },
            17,
            3,
            id="actual-usage",
        ),
        pytest.param({"prompt_tokens": 17}, 17, None, id="partial-usage"),
        pytest.param(
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            0,
            0,
            id="actual-zero",
        ),
        pytest.param(None, None, None, id="missing-usage"),
    ],
)
@pytest.mark.asyncio
async def test_rerank_usage_reaches_inspector(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    usage: dict[str, Any] | None,
    tokens_in: int | None,
    tokens_out: int | None,
) -> None:
    """仅替换 HTTP 传输，验证结果、真实用量、单条记录与快照汇总。"""
    body: dict[str, Any] = {
        "results": [
            {"index": 0, "relevance_score": 0.3},
            {"index": 1, "relevance_score": 0.9},
        ]
    }
    if usage is not None:
        body["usage"] = usage

    def handle(request: httpx.Request) -> httpx.Response:
        """所有 HTTP 请求只在内存中响应，不访问供应商。"""
        assert str(request.url) == "https://rerank.example.invalid/v1/rerank"
        return httpx.Response(200, json=body)

    def build_transport(force_ipv4: bool = False) -> httpx.AsyncBaseTransport:
        """替换适配器传输边界，保留真实 httpx 请求与响应解析。"""
        return httpx.MockTransport(handle)

    monkeypatch.setattr(openai_client, "build_httpx_transport", build_transport)
    monkeypatch.setattr("src.kernel.llm.request_inspector._inspector", None)
    inspector = get_inspector()
    model: dict[str, Any] = {
        "model_identifier": "fixture-rerank",
        "api_provider": "fixture",
        "client_type": "openai",
        "timeout": 10,
        "retry_interval": 0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.0,
        "max_tokens": 100,
        "api_key": "fixture-key",
        "base_url": "https://rerank.example.invalid/v1",
        "max_retry": 0,
        "extra_params": {"trust_env": False},
    }
    expected = [{"index": 1, "score": 0.9, "document": "doc B"}]
    if entrypoint == "client":
        result = await OpenAIChatClient().create_rerank(
            model_name="fixture-rerank",
            query="fixture query",
            documents=["doc A", "doc B"],
            top_n=1,
            request_name="pr181-rerank",
            model_set=model,
        )
        assert result == expected
    else:
        response = await RerankRequest(
            model_set=[model],
            query="fixture query",
            documents=["doc A", "doc B"],
            top_n=1,
            request_name="pr181-rerank",
            enable_metrics=False,
        ).send()
        assert [(item.index, item.score, item.document) for item in response.results] == [
            (1, 0.9, "doc B")
        ]

    snapshot = inspector.snapshot()
    assert len(snapshot["records"]) == 1
    summary = snapshot["records"][0]
    record = inspector.get_record(summary["id"])
    assert record is not None
    assert record["status"] == "succeeded"
    assert record["raw_response"] == body
    assert record["response"]["results"] == expected
    assert record["tokens_in"] == tokens_in
    assert record["tokens_out"] == tokens_out
    assert snapshot["stats"]["tokens_in"] == (tokens_in or 0)
    assert snapshot["stats"]["tokens_out"] == (tokens_out or 0)
    normalized = record["response"]["usage"]
    if usage is None:
        assert normalized is None
    else:
        assert normalized is not None
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if field in usage:
                assert normalized[field] == usage[field]
        if "completion_tokens" not in usage:
            assert "completion_tokens" not in normalized
        if "prompt_tokens_details" in usage:
            assert normalized["cache_hit_tokens"] == 5
