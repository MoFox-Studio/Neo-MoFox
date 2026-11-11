"""Jev System One 的独立异步 HTTP 客户端。

设计约束：
- 不接入 ``kernel.llm`` 的 ModelClientRegistry，也不实现 ChatModelClient：
  Jev 返回 typed 判断而非聊天文本，强行接入会让监控与重试语义失真。
- 只使用官方契约 ``POST {base_url}/v1/systemone``，请求体
  ``{"model", "state", "questions"}``，认证走 ``Authorization: Bearer``。
- API key 只从构造参数或 ``TYPESAFE_API_KEY`` 环境变量读取，不出现在 repr、
  异常消息与日志中；endpoint 必须是 HTTPS 且不得内嵌 userinfo，避免把密钥
  发到明文或钓鱼地址。
"""

from __future__ import annotations

import asyncio
import math
import os
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.kernel.network import build_httpx_timeout, build_httpx_transport

from .events import (
    AFTER_JEV_REQUEST,
    BEFORE_JEV_REQUEST,
    ON_JEV_REQUEST_FAILED,
)
from .exceptions import (
    JevAPIError,
    JevAuthenticationError,
    JevConfigurationError,
    JevOverloadedError,
    JevPayloadTooLargeError,
    JevRateLimitError,
    JevResponseError,
    JevTimeoutError,
    JevTransportError,
    JevUnsupportedMediaTypeError,
    JevValidationError,
)
from .types import (
    JEV_QUESTION_TYPES,
    JEV_SCORE_CRITERIA_RANGE,
    JevAnswer,
    JevInstruction,
    JevQuestion,
    JevResponse,
    JevState,
    JevUsage,
)

#: TypeSafe 官方服务地址，可替换为企业网关。
DEFAULT_BASE_URL = "https://api.typesafe.ai"
#: System One 的固定路由。
ENDPOINT_PATH = "/v1/systemone"
#: 读取 API key 的环境变量名。
API_KEY_ENV = "TYPESAFE_API_KEY"
#: 允许重试的 HTTP 状态码；其余 4xx 属于请求本身有问题，重试无意义。
RETRYABLE_STATUS = frozenset({429, 529})
#: Core ``api_providers.client_type`` 中代表 Jev provider 的合法取值。
#: 三个名字完全等价，配置层、registry 与插件 API 共用这一份定义，
#: 避免各处各写一份集合导致「配置能过、运行时却不认」的错位。
JEV_CLIENT_TYPES = frozenset({"jev", "typesafe", "type_safe"})

#: 上游 error 字段回显到异常消息时的最大长度。
#: 上游返回的 error 字符串属于不可信输入：无限制地拼进异常消息会把它带进
#: 日志、失败事件和上层监控，因此这里截断并清洗控制字符。
MAX_UPSTREAM_ERROR_LENGTH = 200


def _sanitize_upstream_detail(value: str) -> str:
    """清洗上游返回的 error 字符串：截断长度并去掉控制字符。"""
    cleaned = "".join(
        char for char in value if char.isprintable() or char == " "
    )
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_UPSTREAM_ERROR_LENGTH:
        cleaned = cleaned[:MAX_UPSTREAM_ERROR_LENGTH] + "…"
    return cleaned or "provider rejected request"


def _validate_number(name: str, value: Any, *, allow_zero: bool = False) -> float:
    """校验配置数值必须是有限非负（或正）数。

    Args:
        name: 配置字段名，用于错误消息。
        value: 待校验的值。
        allow_zero: 是否允许 0。

    Returns:
        校验后的 float。

    Raises:
        JevConfigurationError: 类型不对或取值非法时抛出。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevConfigurationError(f"{name} 必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise JevConfigurationError(f"{name} 必须是有限数")
    if number < 0 or (number == 0 and not allow_zero):
        raise JevConfigurationError(f"{name} 必须是{'非负' if allow_zero else '正'}数")
    return number


@dataclass(frozen=True, slots=True)
class JevClientConfig:
    """Jev 连接与重试配置。

    ``api_key`` 使用 ``repr=False``，保证密钥不会随配置对象泄漏到日志、
    异常栈或调试输出中。
    """

    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = "jev-latest"
    timeout_seconds: float = 20.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    max_retry_backoff_seconds: float = 8.0
    trust_env: bool = True
    force_ipv4: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict, repr=False)
    extra_query: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """校验配置边界值，非法配置在建立连接前就失败。

        Raises:
            JevConfigurationError: 任一字段不满足约束时抛出。
        """
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise JevConfigurationError("Jev api_key 不能为空")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise JevConfigurationError("Jev base_url 不能为空")
        if not isinstance(self.model, str) or not self.model.strip():
            raise JevConfigurationError("Jev model 不能为空")

        # 密钥会随请求头发往 endpoint，因此只允许无 userinfo 的 HTTPS 地址。
        parsed_url = urlsplit(self.base_url.strip())
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise JevConfigurationError("Jev base_url 必须是合法的 HTTPS URL")
        if parsed_url.username or parsed_url.password:
            raise JevConfigurationError("Jev base_url 不允许包含用户名或密码")

        _validate_number("timeout_seconds", self.timeout_seconds)
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or self.max_retries < 0
        ):
            raise JevConfigurationError("max_retries 必须是非负整数")
        _validate_number("retry_backoff_seconds", self.retry_backoff_seconds, allow_zero=True)
        _validate_number("max_retry_backoff_seconds", self.max_retry_backoff_seconds, allow_zero=True)

    @property
    def endpoint(self) -> str:
        """返回完整的 System One 地址。

        base_url 既可以是 host 根地址，也可以已经带完整路由；query string
        必须保留在路由之后。
        """
        base = self.base_url.strip().rstrip("/")
        parts = urlsplit(base)
        path = parts.path.rstrip("/")
        if not path.endswith(ENDPOINT_PATH):
            path = f"{path}{ENDPOINT_PATH}"
        return parts._replace(path=path).geturl()


class _BorrowedAsyncTransport(httpx.AsyncBaseTransport):
    """向请求上下文借出传输能力，但不接管调用方的底层资源。"""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        """保存调用方拥有的底层传输，不创建连接或修改其生命周期。"""
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """把 HTTP 请求原样交给底层传输，并返回其响应或传播异常。"""
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        """请求上下文退出时不回收借用资源；底层仅由调用方显式关闭。"""
        return None


class JevClient:
    """发送结构化 Jev 判断请求的异步客户端。"""

    def __init__(
        self,
        config: JevClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """创建客户端。

        Args:
            config: 连接与重试配置。
            transport: 可选的调用方传输层，用于测试或受控网络环境；客户端
                仅借用，不会关闭底层，调用方须负责最终回收。
        """
        self.config = config
        self._transport = transport

    @classmethod
    def from_env(cls, **overrides: Any) -> "JevClient":
        """以环境变量中的 API key 创建客户端。

        Args:
            **overrides: 覆盖 JevClientConfig 字段；显式传入的 api_key 优先于
                ``TYPESAFE_API_KEY``。

        Returns:
            配置完成的 JevClient。
        """
        api_key = overrides.pop("api_key", None) or os.getenv(API_KEY_ENV, "")
        return cls(JevClientConfig(api_key=api_key, **overrides))

    async def evaluate(
        self,
        *,
        state: JevState,
        questions: Mapping[str, JevQuestion | Mapping[str, Any]],
        model: str | None = None,
        request_name: str = "jev",
        meta_data: Mapping[str, Any] | None = None,
    ) -> JevResponse:
        """把一个 state 与一组 typed question 发送到 System One。

        请求生命周期会发布 Jev 事件：``before_jev_request``（可改写 state 与
        questions）、``after_jev_request``（可改写 answers 与 usage）、
        ``on_jev_request_failed``（只读）。事件发布失败不会影响请求本身。

        Args:
            state: 被评估状态，string / object / array 皆可。
            questions: question ID 到 JevQuestion 或原始 dict 的映射。
            model: 覆盖配置中的默认模型。
            request_name: 请求名称，用于事件与日志标识。
            meta_data: 附带的调用方元数据，随事件透传。

        Returns:
            校验通过的 JevResponse。

        Raises:
            JevConfigurationError: 本地定义不合法或 state/questions 不可序列化。
            JevAuthenticationError/JevValidationError/JevRateLimitError/
            JevOverloadedError/JevPayloadTooLargeError/JevUnsupportedMediaTypeError:
            对应的 provider HTTP 错误。
            JevTimeoutError/JevTransportError: 超过重试预算的传输层失败。
            JevResponseError: 响应不符合契约。
        """
        state, questions = await self._publish_before_request(
            state=state,
            questions=questions,
            model=model,
            request_name=request_name,
            meta_data=meta_data,
        )
        body = self._build_request_body(state=state, questions=questions, model=model)
        # 以事件改写后实际发送的规范化问题为准，而非调用方的原始定义。
        expected_types: dict[str, str] = {
            question_id: question["type"]
            for question_id, question in body["questions"].items()
        }

        async with httpx.AsyncClient(
            timeout=build_httpx_timeout(self.config.timeout_seconds),
            trust_env=self.config.trust_env,
            transport=(
                _BorrowedAsyncTransport(self._transport)
                if self._transport is not None
                else self._build_transport()
            ),
        ) as client:
            headers = dict(self.config.extra_headers)
            # Provider-specific headers may be added, but cannot replace the
            # authentication and media-type contract of System One.
            headers["Authorization"] = f"Bearer {self.config.api_key}"
            headers["Content-Type"] = "application/json"
            for attempt in range(self.config.max_retries + 1):
                retry_error, response = await self._post_once(client, headers, body)
                if response is not None:
                    if response.status_code in RETRYABLE_STATUS:
                        retry_error = self._status_error(response)
                    elif not response.is_success:
                        error = self._status_error(response)
                        await self._publish_failed(
                            error=error,
                            state=state,
                            questions=questions,
                            request_name=request_name,
                            meta_data=meta_data,
                            retry_count=attempt,
                        )
                        raise error
                    else:
                        try:
                            parsed = self._parse_response(
                                response, expected_types=expected_types,
                            )
                        except JevResponseError as error:
                            # 已收到成功 HTTP 响应，但契约解析失败不进入重试。
                            await self._publish_failed(
                                error=error,
                                state=state,
                                questions=questions,
                                request_name=request_name,
                                meta_data=meta_data,
                                retry_count=attempt,
                            )
                            raise
                        return await self._publish_after_request(
                            response=parsed,
                            state=state,
                            questions=questions,
                            request_name=request_name,
                            meta_data=meta_data,
                            retry_count=attempt,
                        )

                if retry_error is not None:
                    # 与 LLM 链路一致：每次尝试失败都发布失败事件，
                    # 订阅者可通过 retry_count 自行判断是否已耗尽预算。
                    await self._publish_failed(
                        error=retry_error,
                        state=state,
                        questions=questions,
                        request_name=request_name,
                        meta_data=meta_data,
                        retry_count=attempt,
                    )
                if attempt >= self.config.max_retries:
                    raise retry_error or JevResponseError("Jev 请求未产生响应")
                await asyncio.sleep(self._retry_delay(attempt, response))

        raise JevResponseError("Jev 请求未产生响应")  # pragma: no cover - 循环必然返回或抛出

    async def _publish_before_request(
        self,
        *,
        state: JevState,
        questions: Mapping[str, JevQuestion | Mapping[str, Any]],
        model: str | None,
        request_name: str,
        meta_data: Mapping[str, Any] | None,
    ) -> tuple[JevState, Mapping[str, JevQuestion | Mapping[str, Any]]]:
        """发布发送前事件，允许订阅者改写 state 与 questions。"""
        params: dict[str, Any] = {
            "request_name": request_name,
            "model_identifier": model or self.config.model,
            "state": state,
            "questions": dict(questions),
            "retry_count": 0,
            "meta_data": dict(meta_data or {}),
        }
        final = await self._publish(BEFORE_JEV_REQUEST, params)
        new_state = final.get("state", state)
        new_questions = final.get("questions", questions)
        if not isinstance(new_questions, Mapping):
            new_questions = questions
        return new_state, new_questions

    async def _publish_after_request(
        self,
        *,
        response: JevResponse,
        state: JevState,
        questions: Mapping[str, JevQuestion | Mapping[str, Any]],
        request_name: str,
        meta_data: Mapping[str, Any] | None,
        retry_count: int,
    ) -> JevResponse:
        """发布成功事件，允许订阅者改写 answers 与 usage。"""
        params: dict[str, Any] = {
            "request_name": request_name,
            "model_identifier": response.model,
            "state": state,
            "questions": dict(questions),
            "answers": dict(response.answers),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            "retry_count": retry_count,
            "meta_data": dict(meta_data or {}),
        }
        final = await self._publish(AFTER_JEV_REQUEST, params)

        answers = final.get("answers", response.answers)
        parsed_answers: dict[str, JevAnswer] = response.answers
        if isinstance(answers, Mapping) and answers is not response.answers:
            # 订阅者可能给出原始 dict 形态，需要重新解析为 typed answer。
            try:
                parsed_answers = {
                    str(key): (
                        value
                        if isinstance(value, JevAnswer)
                        else JevAnswer.from_payload(value)
                    )
                    for key, value in answers.items()
                }
            except ValueError:
                # 改写后的 answers 不合法时保留原始答案，避免把脏数据交给业务。
                parsed_answers = response.answers

        usage = final.get("usage")
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        if isinstance(usage, Mapping):
            raw_input = usage.get("input_tokens")
            raw_output = usage.get("output_tokens")
            if isinstance(raw_input, int) and not isinstance(raw_input, bool):
                input_tokens = raw_input
            if isinstance(raw_output, int) and not isinstance(raw_output, bool):
                output_tokens = raw_output

        return JevResponse(
            model=response.model,
            answers=parsed_answers,
            usage=JevUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    async def _publish_failed(
        self,
        *,
        error: BaseException,
        state: JevState,
        questions: Mapping[str, JevQuestion | Mapping[str, Any]],
        request_name: str,
        meta_data: Mapping[str, Any] | None,
        retry_count: int,
    ) -> None:
        """发布失败事件；该事件只读，不允许改写任何字段。"""
        await self._publish(
            ON_JEV_REQUEST_FAILED,
            {
                "request_name": request_name,
                "model_identifier": self.config.model,
                "state": state,
                "questions": dict(questions),
                "error": error,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "retry_count": retry_count,
                "meta_data": dict(meta_data or {}),
            },
        )

    @staticmethod
    async def _publish(event_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """发布事件并返回最终参数；事件链路故障不影响主请求。"""
        try:
            from src.kernel.event import get_event_bus

            _, final_params = await get_event_bus().publish(event_name, params)
            if isinstance(final_params, dict):
                return final_params
        except Exception:
            pass
        return params

    async def _post_once(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> tuple[Exception | None, httpx.Response | None]:
        """发起一次请求，把传输层异常转换成可重试的 Jev 异常。"""
        try:
            # 先合并 query，避免 httpx 的 params 参数整体替换 URL 中的原值。
            endpoint = httpx.URL(self.config.endpoint).copy_merge_params(self.config.extra_query)
            return None, await client.post(
                endpoint,
                headers=headers,
                json=body,
            )
        except (TypeError, ValueError) as exc:
            # httpx 用 TypeError/ValueError 表示请求体无法序列化。
            raise JevConfigurationError("Jev state/questions 必须可序列化为 JSON") from exc
        except httpx.TimeoutException:
            return JevTimeoutError("Jev 请求超时"), None
        except httpx.TransportError as exc:
            return JevTransportError(f"Jev 网络请求失败：{type(exc).__name__}"), None

    def _build_transport(self) -> httpx.AsyncBaseTransport | None:
        """按配置选择共享的传输层。"""
        return build_httpx_transport(force_ipv4=self.config.force_ipv4)

    def _build_request_body(
        self,
        *,
        state: JevState,
        questions: Mapping[str, JevQuestion | Mapping[str, Any]],
        model: str | None,
    ) -> dict[str, Any]:
        """校验输入并构造官方 wire body。

        Raises:
            JevConfigurationError: state/questions/question 定义不合法。
        """
        if not isinstance(state, (str, dict, list)):
            raise JevConfigurationError("state 必须是 string、object 或 array")
        if not isinstance(questions, Mapping) or not questions:
            raise JevConfigurationError("questions 必须是非空 mapping")

        serialized: dict[str, dict[str, Any]] = {}
        for question_id, question in questions.items():
            if not isinstance(question_id, str) or not question_id:
                raise JevConfigurationError("question ID 必须是非空字符串")
            if isinstance(question, JevQuestion):
                try:
                    item = question.to_payload()
                except ValueError as exc:
                    raise JevConfigurationError(
                        f"question {question_id!r} 定义无效：{exc}"
                    ) from exc
            elif isinstance(question, Mapping):
                item = dict(question)
            else:
                raise JevConfigurationError(f"question {question_id!r} 格式无效")
            self._validate_question(question_id, item)
            serialized[question_id] = item

        return {"model": model or self.config.model, "state": state, "questions": serialized}

    @staticmethod
    def _validate_question(question_id: str, question: dict[str, Any]) -> None:
        """按 TypeSafe 约定校验单个 question，尽早暴露本地定义错误。

        Raises:
            JevConfigurationError: type/instructions/criteria 不合法。
        """
        question_type = question.get("type")
        if question_type not in JEV_QUESTION_TYPES:
            raise JevConfigurationError(f"question {question_id!r} type 无效")
        if not isinstance(question.get("instructions"), (str, dict, list)):
            raise JevConfigurationError(f"question {question_id!r} instructions 无效")

        criteria = question.get("criteria")
        if question_type == "choice" and (not isinstance(criteria, dict) or not criteria):
            raise JevConfigurationError(
                f"question {question_id!r} 的 choice criteria 必须是非空 object"
            )
        if question_type == "score":
            minimum, maximum = JEV_SCORE_CRITERIA_RANGE
            if not isinstance(criteria, list) or not minimum <= len(criteria) <= maximum:
                raise JevConfigurationError(
                    f"question {question_id!r} 的 score criteria 数量必须为 "
                    f"{minimum} 到 {maximum}"
                )
        if question_type == "noul" and criteria is not None and not isinstance(criteria, dict):
            raise JevConfigurationError(
                f"question {question_id!r} 的 noul criteria 必须是 object"
            )

    @staticmethod
    def _status_error(response: httpx.Response) -> Exception:
        """把 provider HTTP 状态码映射成可区分的异常类型。"""
        status = response.status_code
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = None
        error_type = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error_type, dict):
            error_type = error_type.get("type") or error_type.get("code")
        detail = (
            f"error={_sanitize_upstream_detail(error_type)}"
            if isinstance(error_type, str)
            else "provider rejected request"
        )
        message = f"Jev API HTTP {status}: {detail}"

        if status in {401, 403}:
            return JevAuthenticationError(message)
        if status in {400, 422}:
            return JevValidationError(message)
        if status == 413:
            return JevPayloadTooLargeError(message)
        if status == 415:
            return JevUnsupportedMediaTypeError(message)
        if status == 429:
            return JevRateLimitError(message)
        if status == 529:
            return JevOverloadedError(message)
        return JevAPIError(message, status_code=status)

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """计算退避时长，优先遵守合法的 Retry-After 响应头。"""
        server_delay = self._retry_after_seconds(response)
        if server_delay is not None:
            return min(server_delay, self.config.max_retry_backoff_seconds)
        ceiling = min(
            self.config.retry_backoff_seconds * (2**attempt),
            self.config.max_retry_backoff_seconds,
        )
        # 抖动避免多个协程同步重试形成尖峰。
        return random.uniform(0.0, ceiling) if ceiling else 0.0

    def _retry_after_seconds(self, response: httpx.Response | None) -> float | None:
        """解析 Retry-After（秒数或 HTTP 日期），非法或过期返回 None。"""
        if response is None:
            return None
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            seconds = float(retry_after)
        except ValueError:
            seconds = -1.0
        if math.isfinite(seconds) and seconds >= 0:
            return seconds

        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
        if math.isfinite(delay) and delay >= 0:
            # 将上限统一应用于解析后的服务端等待时间。
            return delay
        return None

    @staticmethod
    def _parse_response(
        response: httpx.Response,
        *,
        expected_types: Mapping[str, str],
    ) -> JevResponse:
        """校验并解析成功响应。

        Args:
            response: 上游成功 HTTP 响应。
            expected_types: 实际请求体中的问题 ID 到预期题型的映射。

        Raises:
            JevResponseError: JSON、model、answers 或 usage 不符合契约。
        """
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise JevResponseError("Jev 响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise JevResponseError("Jev 响应必须是 JSON object")

        model = payload.get("model")
        answers_raw = payload.get("answers")
        if not isinstance(model, str) or not isinstance(answers_raw, dict):
            raise JevResponseError("Jev 响应缺少合法的 model 或 answers")
        if set(answers_raw) != set(expected_types):
            # 宁可整体失败，也不把错位的答案交给业务。
            raise JevResponseError("Jev answers 的 question ID 与请求不一致")

        usage_raw = payload.get("usage")
        if not isinstance(usage_raw, dict):
            raise JevResponseError("Jev 响应缺少 usage")
        input_tokens = usage_raw.get("input_tokens")
        output_tokens = usage_raw.get("output_tokens")
        for value in (input_tokens, output_tokens):
            if isinstance(value, bool) or not isinstance(value, int):
                raise JevResponseError("Jev usage token 字段必须是整数")

        try:
            answers = {
                question_id: JevAnswer.from_payload(answer)
                for question_id, answer in answers_raw.items()
            }
        except (ValueError, OverflowError) as exc:
            raise JevResponseError(f"Jev answer 结构非法：{exc}") from exc

        for question_id, answer in answers.items():
            if answer.type != expected_types[question_id]:
                raise JevResponseError(f"Jev answer {question_id!r} 的 type 与请求不一致")

        return JevResponse(
            model=model,
            answers=answers,
            usage=JevUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )


def noul(instructions: JevInstruction, *, criteria: dict[str, Any] | None = None) -> JevQuestion:
    """构造 0 到 1 的 yes 概率问题。

    Args:
        instructions: 问题指令。
        criteria: 可选参考信息。

    Returns:
        Noul 类型的 JevQuestion。
    """
    return JevQuestion("noul", instructions, criteria)


def choice(instructions: JevInstruction, criteria: dict[str, Any]) -> JevQuestion:
    """构造有限选项判断。

    Args:
        instructions: 问题指令。
        criteria: 非空的候选项到说明的映射。

    Returns:
        Choice 类型的 JevQuestion。
    """
    return JevQuestion("choice", instructions, criteria)


def score(instructions: JevInstruction, criteria: list[Any]) -> JevQuestion:
    """构造按有序 rubric 打分的问题。

    Args:
        instructions: 问题指令。
        criteria: 2 到 10 项的有序评分标准。

    Returns:
        Score 类型的 JevQuestion。
    """
    return JevQuestion("score", instructions, criteria)
