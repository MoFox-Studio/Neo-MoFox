"""模型客户端注册表测试。

测试覆盖：
1. ModelClientRegistry 的各种客户端类型
2. get_client_for_model 方法的不同路径
"""

from typing import Any, cast

import pytest

from src.kernel.llm import LLMConfigurationError, LLMProviderUnavailableError
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.model_client.anthropic_client import AnthropicChatClient
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from src.kernel.llm.model_client.openai_client import OpenAIChatClient
from src.kernel.llm.model_client.runtime import get_provider_probe
from src.kernel.llm.types import ModelEntry


def _model_entry(**overrides: Any) -> ModelEntry:
    """构造最小可用的 ModelEntry。"""
    base: dict[str, Any] = {
        "api_provider": "test",
        "base_url": "https://example.com/v1",
        "model_identifier": "test-model",
        "api_key": "sk-test",
        "client_type": "openai",
        "max_retry": 0,
        "timeout": 10.0,
        "retry_interval": 1.0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.0,
        "max_tokens": 256,
        "max_context": 8192,
        "tool_call_compat": False,
        "extra_params": {},
    }
    base.update(overrides)
    return cast(ModelEntry, base)


def test_registry_default_openai_client():
    """测试注册表默认创建OpenAI客户端。"""
    registry = ModelClientRegistry()

    assert registry.openai is not None
    assert isinstance(registry.openai, OpenAIChatClient)


def test_registry_custom_openai_client():
    """测试注册表使用自定义OpenAI客户端。"""
    custom_client = OpenAIChatClient()
    registry = ModelClientRegistry(openai=custom_client)

    assert registry.openai is custom_client


@pytest.mark.parametrize("client_type", ["jev", "typesafe", "type-safe"])
def test_registry_rejects_removed_provider(client_type: str) -> None:
    """已移除类型必须明确报错，不能回退到现有聊天客户端。"""
    registry = ModelClientRegistry()
    with pytest.raises(LLMConfigurationError, match="不支持的 client_type"):
        registry.get_client_for_model(_model_entry(client_type=client_type))


def test_registry_get_openai_client():
    """测试获取OpenAI客户端。"""
    registry = ModelClientRegistry()
    model = _model_entry(client_type="openai", api_provider="OpenAI")

    client = registry.get_client_for_model(model)

    assert client is registry.openai


def test_registry_get_anthropic_client():
    """测试获取 Anthropic 客户端。"""
    registry = ModelClientRegistry()
    model = _model_entry(client_type="anthropic", api_provider="Anthropic")

    client = registry.get_client_for_model(model)

    assert client is registry.anthropic
    assert isinstance(client, AnthropicChatClient)


def test_registry_get_gemini_client():
    """测试获取Gemini客户端（当配置时）。"""
    # 创建一个假的gemini客户端
    class FakeGeminiClient:
        async def create(self, **kwargs: Any):
            raise NotImplementedError

    registry = ModelClientRegistry(gemini=cast(Any, FakeGeminiClient()))
    model = _model_entry(client_type="gemini", api_provider="Google")

    client = registry.get_client_for_model(model)

    assert isinstance(client, FakeGeminiClient)


def test_registry_get_aiohttp_gemini_client():
    """测试获取aiohttp_gemini客户端（当配置时）。"""
    # 创建一个假的gemini客户端
    class FakeGeminiClient:
        async def create(self, **kwargs: Any):
            raise NotImplementedError

    registry = ModelClientRegistry(gemini=cast(Any, FakeGeminiClient()))
    model = _model_entry(client_type="aiohttp_gemini", api_provider="Google")

    client = registry.get_client_for_model(model)

    assert isinstance(client, FakeGeminiClient)


@pytest.mark.parametrize(
    "client_type",
    ["gemini", "Gemini", "GEMINI", "gemini_v2", "Gemini-V2", "google", "aiohttp_gemini"],
)
def test_registry_default_gemini_client(client_type: str) -> None:
    """已支持的 Gemini 别名默认路由到真实 Gemini 客户端。"""
    registry = ModelClientRegistry()
    model = _model_entry(client_type=client_type, api_provider="Google")

    client = registry.get_client_for_model(model)

    assert client is registry.gemini
    assert isinstance(client, GeminiChatClient)


@pytest.mark.parametrize(
    "client_type",
    ["gemini", "Gemini", "GEMINI", "gemini_v2", "Gemini-V2", "google", "aiohttp_gemini"],
)
def test_registry_unregistered_gemini_does_not_fallback_to_openai(client_type: str):
    """已识别的 Gemini 别名未注册时必须报错，不能回退到 OpenAI。"""
    registry = ModelClientRegistry()
    registry.gemini = None
    model = _model_entry(client_type=client_type, api_provider="Google")

    with pytest.raises(LLMConfigurationError, match="Gemini client 未注册"):
        registry.get_client_for_model(model)


def test_registry_unsupported_vertex_ai_does_not_fallback_to_openai() -> None:
    """Vertex AI 需要独立认证与路由，不能使用默认 Gemini 或 OpenAI。"""
    registry = ModelClientRegistry()
    model = _model_entry(client_type="vertex_ai", api_provider="Google")

    with pytest.raises(
        LLMProviderUnavailableError,
        match="Vertex AI requires a separate authentication/route adapter",
    ):
        registry.get_client_for_model(model)


@pytest.mark.parametrize(
    "client_type", ["bedrock", "Bedrock", "amazon_bedrock", "bedrock_converse"]
)
def test_registry_unregistered_bedrock_does_not_fallback_to_openai(client_type: str):
    """已识别的 Bedrock 别名未注册时必须报错，不能回退到 OpenAI。"""
    registry = ModelClientRegistry()
    model = _model_entry(client_type=client_type, api_provider="AWS")

    with pytest.raises(LLMConfigurationError, match="Bedrock client 未注册"):
        registry.get_client_for_model(model)


def test_registry_get_bedrock_client():
    """测试获取Bedrock客户端（当配置时）。"""
    # 创建一个假的bedrock客户端
    class FakeBedrockClient:
        async def create(self, **kwargs: Any):
            raise NotImplementedError

    registry = ModelClientRegistry(bedrock=cast(Any, FakeBedrockClient()))
    model = _model_entry(client_type="bedrock", api_provider="AWS")

    client = registry.get_client_for_model(model)

    assert isinstance(client, FakeBedrockClient)


@pytest.mark.parametrize("client_type", ["unknown_provider", "gemni", "", "  ", 123, None])
@pytest.mark.parametrize("kind", ["chat", "embedding", "rerank", "asr"])
def test_registry_rejects_invalid_client_type(client_type: object, kind: str) -> None:
    """四种请求分发均拒绝未知或无效类型，不选择默认客户端。"""
    registry = ModelClientRegistry()
    resolve = {
        "chat": registry.get_client_for_model,
        "embedding": registry.get_embedding_client_for_model,
        "rerank": registry.get_rerank_client_for_model,
        "asr": registry.get_asr_client_for_model,
    }[kind]
    with pytest.raises(LLMProviderUnavailableError, match="不支持的 client_type"):
        resolve(_model_entry(client_type=client_type))


def test_registry_rejects_missing_client_type() -> None:
    """技术条目缺少类型时，不应用配置层的默认值。"""
    with pytest.raises(LLMProviderUnavailableError, match="不支持的 client_type"):
        ModelClientRegistry().get_client_for_model(cast(ModelEntry, {}))


@pytest.mark.parametrize(("client_type", "attribute"), [
    (" OPENAI ", "openai"),
    (" Anthropic ", "anthropic"),
    ("openai_response", "responses"),
    (" OpenAI-Response ", "responses"),
    ("openai.responses", "responses"),
    ("responses", "responses"),
    ("gemini", "gemini"),
    (" AIOHTTP-GEMINI ", "gemini"),
    ("gemini-v2", "gemini"),
    ("google", "gemini"),
    ("bedrock", "bedrock"),
    ("amazon-bedrock", "bedrock"),
    ("BEDROCK_CONVERSE", "bedrock"),
])
def test_supported_alias_preserves_client_and_probe(client_type: str, attribute: str) -> None:
    """已识别的别名保留各自的客户端和非生成探测能力。"""
    bedrock = cast(Any, object())
    registry = ModelClientRegistry(bedrock=bedrock)

    assert registry.get_client_for_model(_model_entry(client_type=client_type)) is getattr(registry, attribute)
    probe = get_provider_probe(client_type)
    assert probe.requires_model_entries is (attribute == "gemini")
    assert probe.allows_anonymous_probe is (attribute != "gemini")


@pytest.mark.parametrize(("client_type", "attribute"), [
    ("anthropic", "anthropic"), ("openai.responses", "responses"),
])
def test_unregistered_client_never_uses_openai(client_type: str, attribute: str) -> None:
    """类型合法但未装配客户端时，不能跨协议使用 OpenAI。"""
    registry = ModelClientRegistry()
    setattr(registry, attribute, None)

    with pytest.raises(LLMProviderUnavailableError, match=f"{attribute.capitalize()} client 未注册"):
        registry.get_client_for_model(_model_entry(client_type=client_type))


def test_registry_openai_none_raises_error():
    """测试OpenAI客户端为None时抛出异常。"""
    # 通过直接设置openai为None来测试
    registry = ModelClientRegistry()
    registry.openai = None

    model = _model_entry(client_type="openai", api_provider="OpenAI")

    with pytest.raises(LLMConfigurationError, match="OpenAI client 未配置"):
        registry.get_client_for_model(model)


def test_registry_get_asr_client_for_model():
    """测试 get_asr_client_for_model 返回支持 ASR 的 client。"""
    registry = ModelClientRegistry()
    model = _model_entry(client_type="openai", api_key="sk-test")

    client = registry.get_asr_client_for_model(model)

    assert hasattr(client, "create_transcription")


def test_registry_get_asr_client_raises_when_not_supported():
    """测试 client 不支持 ASR 时抛出异常。"""
    from unittest.mock import MagicMock

    registry = ModelClientRegistry()
    # 替换 openai client 为不支持 create_transcription 的 mock
    mock_client = MagicMock(spec=[])  # spec=[] 表示无任何属性
    registry.openai = mock_client  # type: ignore[assignment]

    model = _model_entry(client_type="openai", api_key="sk-test")

    with pytest.raises(LLMConfigurationError, match="不支持 ASR"):
        registry.get_asr_client_for_model(model)
