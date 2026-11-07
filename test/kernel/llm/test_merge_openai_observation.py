"""测试 openai_client dev 合并后的观测逻辑。

验证以下整合点：
- O1: reasoning_history_mode 保留 bool() 兼容性，支持字符串配置
- O2: HTTP rerank 保留核心实现，加入 trace 观测
- 观测路径：provider_attempt、start_provider、trace.raw/result
- 使用真实 OpenAIChatClient.create + mock SDK，不触发真实网络
- 使用已有 request_inspector 验证观测记录完整性
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from src.kernel.llm import ROLE, LLMPayload, ReasoningText, Text
from src.kernel.llm.model_client.openai_client import OpenAIChatClient
from src.kernel.llm.request_inspector import get_inspector


class MockTransport(httpx.AsyncBaseTransport):
    """返回确定的 HTTP rerank 响应并记录实际请求。"""

    def __init__(
        self, response_status: int = 200, response_body: dict[str, Any] | None = None
    ) -> None:
        """保存返回状态、响应体和请求记录。"""
        self.response_status = response_status
        self.response_body = response_body or {}
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """捕获真实 HTTP 请求并返回内存响应。"""
        self.last_request = request
        return httpx.Response(
            status_code=self.response_status,
            json=self.response_body,
        )


class TestReasoningHistoryCompatibility:
    """测试 reasoning_history_mode 字符串配置兼容性（O1）。

    通过真实 OpenAIChatClient.create() 调用，mock SDK 捕获最终发送的
    messages，验证 reasoning_content 字段的保留/去除逻辑。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("reasoning_mode", "expect_reasoning_in_messages"),
        [
            (True, True),  # bool(True) == True → 保留
            (False, False),  # bool(False) == False → 去除
            ("deepseek", True),  # bool("deepseek") == True → 保留
            ("kimi", True),  # bool("kimi") == True → 保留
            ("auto", True),  # bool("auto") == True → 保留
            ("", False),  # bool("") == False → 去除
            (None, False),  # bool(None) == False → 去除
        ],
    )
    async def test_reasoning_content_retention_with_real_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
        reasoning_mode: Any,
        expect_reasoning_in_messages: bool,
    ) -> None:
        """真实调用 create() 验证 reasoning_content 保留逻辑。

        Args:
            monkeypatch: pytest fixture 用于 mock。
            reasoning_mode: reasoning_history_mode 配置值。
            expect_reasoning_in_messages: 期望最终 messages 是否包含 reasoning_content。
        """
        # Mock OpenAI SDK client.chat.completions.create
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(
                message=MagicMock(
                    content="response text",
                    tool_calls=None,
                    reasoning_content=None,
                ),
                finish_reason="stop",
            )
        ]
        mock_resp.usage = MagicMock(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        )

        captured_params: dict[str, Any] | None = None

        async def mock_create(**kwargs: Any) -> Any:
            """记录实际 SDK 参数并返回受控结果。"""
            nonlocal captured_params
            captured_params = kwargs
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        # Patch _get_client 返回 mock client
        def mock_get_client(self: Any, **kwargs: Any) -> Any:
            """仅替换 SDK 客户端创建，不替换适配器逻辑。"""
            return mock_client

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.OpenAIChatClient._get_client",
            mock_get_client,
        )

        # 构造包含 reasoning_content 的历史
        payloads = [
            LLMPayload(role=ROLE.USER, content=[Text(text="user message")]),
            LLMPayload(
                role=ROLE.ASSISTANT,
                content=[
                    Text(text="assistant reply"),
                    ReasoningText(text="internal reasoning"),
                ],
            ),
        ]

        client = OpenAIChatClient()
        model_set = {
            "api_key": "test-key",
            "base_url": "https://test.example.invalid",
            "extra_params": {"reasoning_history_mode": reasoning_mode},
        }

        # 调用真实 create 方法
        await client.create(
            model_name="test-model",
            payloads=payloads,
            tools=[],
            request_name="test",
            model_set=model_set,
            stream=False,
        )

        # 验证最终发送的 messages
        assert captured_params is not None
        messages = captured_params["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assistant_msg = messages[1]
        assert assistant_msg["role"] == "assistant"

        if expect_reasoning_in_messages:
            # 应保留 reasoning_content
            assert "reasoning_content" in assistant_msg
            assert assistant_msg["reasoning_content"] == "internal reasoning"
        else:
            # 应去除 reasoning_content
            assert "reasoning_content" not in assistant_msg


class TestRerankHTTPWithObservation:
    """测试 HTTP rerank 保留核心实现并加入观测（O2）。

    验证：
    1. HTTP endpoint、认证、核心字段保护、排序、top_n 截断
    2. trace 观测链路：start_provider → trace.raw → trace.result
    3. Attempt 逻辑请求绑定
    4. 无重复记录
    """

    @pytest.mark.asyncio
    async def test_rerank_http_with_trace_observation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """真实调用 create_rerank，验证 HTTP + trace 完整链路。

        验证点：
        - HTTP 请求参数、endpoint、认证
        - trace.start_provider/raw/result 记录同一 trace
        - request_inspector 捕获最终 params
        - 响应排序和 top_n 截断
        """
        mock_response_body = {
            "results": [
                {"index": 1, "relevance_score": 0.6},
                {"index": 2, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.8},
            ]
        }
        mock_transport = MockTransport(
            response_status=200, response_body=mock_response_body
        )

        def mock_build_httpx_transport(force_ipv4: bool = False) -> httpx.AsyncBaseTransport:
            """用内存传输覆盖出站网络边界。"""
            return mock_transport

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.build_httpx_transport",
            mock_build_httpx_transport,
        )

        # 清空 inspector 历史
        inspector = get_inspector()
        inspector._records.clear()
        inspector._next_id = 1

        client = OpenAIChatClient()
        model_set = {
            "api_key": "test-key",
            "base_url": "https://test.example.invalid/v1",
            "extra_params": {"trust_env": False},
        }

        documents = ["doc A", "doc B", "doc C"]
        result = await client.create_rerank(
            model_name="test-rerank",
            query="test query",
            documents=documents,
            top_n=2,
            request_name="test_rerank_trace",
            model_set=model_set,
        )

        # ── 验证 HTTP 请求 ──
        assert mock_transport.last_request is not None
        assert mock_transport.last_request.method == "POST"
        assert (
            "https://test.example.invalid/v1/rerank"
            in str(mock_transport.last_request.url)
        )
        assert mock_transport.last_request.headers["Authorization"] == "Bearer test-key"
        assert (
            mock_transport.last_request.headers["Content-Type"] == "application/json"
        )

        request_body = json.loads(mock_transport.last_request.content.decode())
        assert request_body["model"] == "test-rerank"
        assert request_body["query"] == "test query"
        assert request_body["documents"] == documents
        assert request_body["top_n"] == 2

        # ── 验证响应排序和 top_n 截断 ──
        assert len(result) == 2
        assert result[0]["index"] == 2
        assert result[0]["score"] == 0.95
        assert result[0]["document"] == "doc C"
        assert result[1]["index"] == 0
        assert result[1]["score"] == 0.8
        assert result[1]["document"] == "doc A"

        # ── 验证 request_inspector 观测记录 ──
        summaries = inspector.list_summaries()
        assert len(summaries) >= 1
        # 最新记录应该是 rerank.create
        rerank_records = [s for s in summaries if s["api_name"] == "rerank.create"]
        assert len(rerank_records) == 1
        record_id = rerank_records[0]["id"]

        full_record = inspector.get_record(record_id)
        assert full_record is not None
        assert full_record["api_name"] == "rerank.create"
        assert full_record["model"] == "test-rerank"
        assert full_record["params"]["query"] == "test query"
        assert full_record["params"]["documents"] == documents
        # 原始响应没有 metadata，标准结果不能靠元数据存在与否证明。
        assert "metadata" not in mock_response_body
        expected = [
            {"index": 2, "score": 0.95, "document": "doc C"},
            {"index": 0, "score": 0.8, "document": "doc A"},
        ]
        assert result == expected
        assert full_record["status"] == "succeeded"
        assert full_record["response"]["kind"] == "rerank"
        assert full_record["response"]["results"] == expected
        assert full_record["response"]["usage"] is None
        assert full_record["tokens_in"] is None
        assert full_record["tokens_out"] is None
        assert full_record["raw_response"] == mock_response_body
        assert full_record["raw_response"]["results"] != expected
        assert [item["index"] for item in full_record["raw_response"]["results"]] == [1, 2, 0]

    @pytest.mark.asyncio
    async def test_rerank_http_custom_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证自定义 rerank_url 配置。"""
        mock_response = {"results": [{"index": 0, "relevance_score": 1.0}]}
        mock_transport = MockTransport(
            response_status=200, response_body=mock_response
        )

        def mock_build_httpx_transport(force_ipv4: bool = False) -> httpx.AsyncBaseTransport:
            """用内存传输覆盖出站网络边界。"""
            return mock_transport

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.build_httpx_transport",
            mock_build_httpx_transport,
        )

        client = OpenAIChatClient()
        model_set = {
            "api_key": "custom-key",
            "rerank_url": "https://custom.rerank.invalid/rank",
            "extra_params": {"trust_env": False},
        }

        await client.create_rerank(
            model_name="custom-model",
            query="q",
            documents=["d1"],
            top_n=None,
            request_name="test",
            model_set=model_set,
        )

        assert mock_transport.last_request is not None
        assert (
            str(mock_transport.last_request.url)
            == "https://custom.rerank.invalid/rank"
        )

    @pytest.mark.asyncio
    async def test_rerank_http_core_field_protection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证核心字段保护：extra_body 不能覆盖 model/query/documents。

        确保即使用户在 extra_params.body 中注入攻击值，核心字段仍由
        调用方参数决定。
        """
        mock_response = {"results": [{"index": 0, "relevance_score": 0.8}]}
        mock_transport = MockTransport(
            response_status=200, response_body=mock_response
        )

        def mock_build_httpx_transport(force_ipv4: bool = False) -> httpx.AsyncBaseTransport:
            """用内存传输覆盖出站网络边界。"""
            return mock_transport

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.build_httpx_transport",
            mock_build_httpx_transport,
        )

        client = OpenAIChatClient()
        model_set = {
            "api_key": "test-key",
            "extra_params": {
                "trust_env": False,
                "body": {
                    "model": "attacker-model",  # 应被忽略
                    "query": "attacker-query",  # 应被忽略
                    "documents": ["attacker-doc"],  # 应被忽略
                    "custom_param": "allowed",  # 应保留
                },
            },
        }

        await client.create_rerank(
            model_name="real-model",
            query="real-query",
            documents=["real-doc"],
            top_n=None,
            request_name="test",
            model_set=model_set,
        )

        request_body = json.loads(mock_transport.last_request.content.decode())
        assert request_body["model"] == "real-model"
        assert request_body["query"] == "real-query"
        assert request_body["documents"] == ["real-doc"]
        assert request_body["custom_param"] == "allowed"

    @pytest.mark.asyncio
    async def test_rerank_http_header_auth_protection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证认证头保护：extra_headers 不能覆盖 Authorization/Content-Type。

        确保即使用户在 extra_params.headers 中注入攻击值，认证头仍由
        api_key 控制，防止密钥被发往非预期主机。
        """
        mock_response = {"results": []}
        mock_transport = MockTransport(
            response_status=200, response_body=mock_response
        )

        def mock_build_httpx_transport(force_ipv4: bool = False) -> httpx.AsyncBaseTransport:
            """用内存传输覆盖出站网络边界。"""
            return mock_transport

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.build_httpx_transport",
            mock_build_httpx_transport,
        )

        client = OpenAIChatClient()
        model_set = {
            "api_key": "safe-key",
            "extra_params": {
                "trust_env": False,
                "headers": {
                    "Authorization": "Bearer attacker-key",  # 应被忽略
                    "Content-Type": "text/plain",  # 应被忽略
                    "X-Custom": "allowed",  # 应保留
                },
            },
        }

        await client.create_rerank(
            model_name="test-model",
            query="q",
            documents=["d"],
            top_n=None,
            request_name="test",
            model_set=model_set,
        )

        headers = mock_transport.last_request.headers
        assert headers["Authorization"] == "Bearer safe-key"
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Custom"] == "allowed"


class TestLogicalAttemptBinding:
    """验证逻辑请求观测关联（dev 观测路径）。

    验证 provider_attempt context manager 正确包裹请求，
    trace 对象在 start_provider/raw/result 链路中保持一致。
    """

    @pytest.mark.asyncio
    async def test_provider_attempt_binds_logical_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 provider_attempt 创建逻辑请求上下文，trace 记录绑定同一 attempt。

        通过 request_inspector 检查最终记录包含 logical_request 关联。
        """
        from src.kernel.llm.request_trace import current_attempt

        seen_attempts: list[Any] = []
        # Mock OpenAI SDK
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(
                message=MagicMock(
                    content="test response", tool_calls=None, reasoning_content=None
                ),
                finish_reason="stop",
            )
        ]
        mock_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        async def mock_create(**kwargs: Any) -> Any:
            """记录 SDK 调用持有的尝试句柄，并返回受控结果。"""
            trace = current_attempt()
            assert trace is not None
            seen_attempts.append(trace)
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        def mock_get_client(self: Any, **kwargs: Any) -> Any:
            """仅替换 SDK 客户端创建，不替换适配器逻辑。"""
            return mock_client

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.OpenAIChatClient._get_client",
            mock_get_client,
        )

        # 清空 inspector 历史
        inspector = get_inspector()
        inspector._records.clear()
        inspector._next_id = 1

        client = OpenAIChatClient()
        model_set = {
            "api_key": "test-key",
            "base_url": "https://test.example.invalid",
            "extra_params": {"trust_env": False},
        }

        payloads = [LLMPayload(role=ROLE.USER, content=[Text(text="test")])]

        # 调用 create，应触发 provider_attempt
        await client.create(
            model_name="test-model",
            payloads=payloads,
            tools=[],
            request_name="test_logical_binding",
            model_set=model_set,
            stream=False,
        )

        # 验证 request_inspector 捕获了记录
        summaries = inspector.list_summaries()
        chat_records = [
            s for s in summaries if s["api_name"] == "chat.completions.create"
        ]
        assert len(chat_records) >= 1
        record_id = chat_records[-1]["id"]

        full_record = inspector.get_record(record_id)
        assert full_record is not None
        assert full_record["api_name"] == "chat.completions.create"
        assert full_record["model"] == "test-model"
        # metadata 应包含 request_name
        assert full_record["metadata"]["request_name"] == "test_logical_binding"
        assert len(seen_attempts) == 1
        trace = seen_attempts[0]
        assert record_id == trace.record_id
        assert full_record["logical_request_id"] == trace.logical.id
        assert full_record["attempt"] == 1
        assert len(chat_records) == 1

    @pytest.mark.asyncio
    async def test_no_duplicate_observation_records(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证同一请求不会产生重复的观测记录。

        确认 provider_attempt 和 trace 配合后，request_inspector 只记录一次。
        """
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(
                message=MagicMock(
                    content="response", tool_calls=None, reasoning_content=None
                ),
                finish_reason="stop",
            )
        ]
        mock_resp.usage = MagicMock(prompt_tokens=3, completion_tokens=2, total_tokens=5)

        async def mock_create(**kwargs: Any) -> Any:
            """记录实际 SDK 参数并返回受控结果。"""
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        def mock_get_client(self: Any, **kwargs: Any) -> Any:
            """仅替换 SDK 客户端创建，不替换适配器逻辑。"""
            return mock_client

        monkeypatch.setattr(
            "src.kernel.llm.model_client.openai_client.OpenAIChatClient._get_client",
            mock_get_client,
        )

        inspector = get_inspector()
        initial_count = len(inspector.list_summaries())

        client = OpenAIChatClient()
        model_set = {
            "api_key": "test-key",
            "base_url": "https://test.example.invalid",
            "extra_params": {"trust_env": False},
        }

        payloads = [LLMPayload(role=ROLE.USER, content=[Text(text="test")])]

        await client.create(
            model_name="test-model",
            payloads=payloads,
            tools=[],
            request_name="dedup_test",
            model_set=model_set,
            stream=False,
        )

        # 应该只增加 1 条记录，不是 2 条
        final_count = len(inspector.list_summaries())
        assert final_count == initial_count + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
