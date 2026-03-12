"""模型客户端注册表测试。

测试覆盖：
1. ModelClientRegistry 的各种客户端类型
2. get_client_for_model 方法的不同路径
"""

import pytest

from src.kernel.llm import LLMConfigurationError
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.model_client.openai_client import OpenAIChatClient


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


def test_registry_get_openai_client():
    """测试获取OpenAI客户端。"""
    registry = ModelClientRegistry()
    model = {
        "client_type": "openai",
        "api_provider": "OpenAI",
    }

    client = registry.get_client_for_model(model)

    assert client is registry.openai


def test_registry_get_gemini_client():
    """测试获取Gemini客户端（当配置时）。"""
    # 创建一个假的gemini客户端
    class FakeGeminiClient:
        pass

    registry = ModelClientRegistry(gemini=FakeGeminiClient())
    model = {
        "client_type": "gemini",
        "api_provider": "Google",
    }

    client = registry.get_client_for_model(model)

    assert isinstance(client, FakeGeminiClient)


def test_registry_get_aiohttp_gemini_client():
    """测试获取aiohttp_gemini客户端（当配置时）。"""
    # 创建一个假的gemini客户端
    class FakeGeminiClient:
        pass

    registry = ModelClientRegistry(gemini=FakeGeminiClient())
    model = {
        "client_type": "aiohttp_gemini",
        "api_provider": "Google",
    }

    client = registry.get_client_for_model(model)

    assert isinstance(client, FakeGeminiClient)


def test_registry_get_bedrock_client():
    """测试获取Bedrock客户端（当配置时）。"""
    # 创建一个假的bedrock客户端
    class FakeBedrockClient:
        pass

    registry = ModelClientRegistry(bedrock=FakeBedrockClient())
    model = {
        "client_type": "bedrock",
        "api_provider": "AWS",
    }

    client = registry.get_client_for_model(model)

    assert isinstance(client, FakeBedrockClient)


def test_registry_invalid_client_type_fallback_to_openai():
    """测试无效的client_type回退到OpenAI。"""
    registry = ModelClientRegistry()
    model = {
        "client_type": "unknown_provider",
        "api_provider": "Unknown",
    }

    client = registry.get_client_for_model(model)

    # 应该回退到OpenAI
    assert client is registry.openai


def test_registry_non_string_client_type_fallback_to_openai():
    """测试client_type不是字符串时回退到OpenAI。"""
    registry = ModelClientRegistry()
    model = {
        "client_type": 123,  # 不是字符串
        "api_provider": "Unknown",
    }

    client = registry.get_client_for_model(model)

    # 应该回退到OpenAI
    assert client is registry.openai


def test_registry_openai_none_raises_error():
    """测试OpenAI客户端为None时抛出异常。"""
    # 通过直接设置openai为None来测试
    registry = ModelClientRegistry()
    registry.openai = None

    model = {
        "client_type": "unknown",
        "api_provider": "Unknown",
    }

    with pytest.raises(LLMConfigurationError, match="OpenAI client 未配置"):
        registry.get_client_for_model(model)
