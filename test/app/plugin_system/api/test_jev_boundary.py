"""插件 Jev 门面的稳定签名与领域配置职责边界回归。"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.app.plugin_system.api import jev_api
from src.kernel.jev import JevClientConfig, JevResponse, JevUsage


def test_public_signature_and_version_are_unchanged() -> None:
    """固定插件可见参数顺序、默认值及 API 版本。"""
    create = inspect.signature(jev_api.create_jev_client)
    assert list(create.parameters) == ["config", "provider_name", "configured_model_name", "overrides"]
    assert create.parameters["config"].default is None
    assert create.parameters["provider_name"].kind is inspect.Parameter.KEYWORD_ONLY
    assert create.parameters["configured_model_name"].default is None
    assert create.parameters["overrides"].kind is inspect.Parameter.VAR_KEYWORD
    evaluate = inspect.signature(jev_api.evaluate_jev)
    assert list(evaluate.parameters) == [
        "state", "questions", "client", "model", "request_name", "meta_data", "client_overrides",
    ]
    assert evaluate.parameters["request_name"].default == "jev"
    assert jev_api.API_VERSION == "1.0.0"


def test_configuration_names_delegate_without_field_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """门面只传名称与 overrides 给领域桥接，不读取 provider 或解释传输字段。"""
    from src.core.config import jev as bridge

    config = JevClientConfig(api_key="fake-key", base_url="https://offline.invalid")
    resolver = Mock(return_value=config)
    monkeypatch.setattr(bridge, "resolve_jev_config", resolver)
    client = jev_api.create_jev_client(configured_model_name="judge", timeout_seconds=4)
    assert client.config is config
    resolver.assert_called_once_with(
        provider_name=None, configured_model_name="judge", overrides={"timeout_seconds": 4},
    )


@pytest.mark.parametrize("kwargs", [
    {"provider_name": "typed"}, {"configured_model_name": "judge"}, {"model": "override"},
])
def test_explicit_config_still_rejects_mixed_sources(kwargs: dict[str, Any]) -> None:
    """显式配置与名称或 overrides 的互斥错误类型保持为 ValueError。"""
    with pytest.raises(ValueError, match="config 不能与"):
        jev_api.create_jev_client(JevClientConfig(api_key="fake-key"), **kwargs)


def test_names_remain_mutually_exclusive() -> None:
    """两个配置名称的原有互斥消息不变。"""
    with pytest.raises(ValueError, match="provider_name 不能与 configured_model_name"):
        jev_api.create_jev_client(provider_name="typed", configured_model_name="judge")


def test_environment_path_keeps_override_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """无配置名称时仍走 from_env，显式密钥和地址高于环境。"""
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-env")
    assert jev_api.create_jev_client().config.api_key == "fake-env"
    client = jev_api.create_jev_client(api_key="fake-explicit", base_url="https://offline.invalid")
    assert client.config.api_key == "fake-explicit"
    assert client.config.base_url == "https://offline.invalid"


class EvaluationOnlyClient:
    """只实现 evaluate 的可替换客户端，任何 SDK、池或资源探测都应失败。"""

    def __init__(self, response: JevResponse) -> None:
        """以给定返回值创建异步调用记录器，无返回值。"""
        self.evaluate = AsyncMock(return_value=response)

    def __getattr__(self, name: str) -> Any:
        """拒绝读取 evaluate 之外的属性，参数为属性名且始终抛错。"""
        raise AssertionError(f"门面越界读取客户端字段: {name}")


async def test_evaluate_only_client_is_forwarded_without_sdk_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """符合 evaluate 接口的对象无需 SDK、配置或资源池也可完整透传。"""
    response = JevResponse(model="offline", answers={}, usage=JevUsage(0, 0))
    client = EvaluationOnlyClient(response)
    factory = Mock(side_effect=AssertionError("不应创建默认客户端"))
    monkeypatch.setattr(jev_api, "create_jev_client", factory)
    questions = {"q": {"type": "noul", "instructions": "ok?"}}
    meta = {"scope": "offline"}
    result = await jev_api.evaluate_jev(
        state={"value": 1}, questions=questions, client=client,
        model="fixed", request_name="boundary", meta_data=meta,
    )
    assert result is response
    client.evaluate.assert_awaited_once_with(
        state={"value": 1}, questions=questions, model="fixed",
        request_name="boundary", meta_data=meta,
    )
    factory.assert_not_called()
