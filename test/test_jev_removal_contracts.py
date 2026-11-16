"""固定 Jev 核心拆除边界，拒绝旧配置并保护其他客户端的现有分发。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from pydantic import ValidationError

from src.core.config import model_config as model_config_module
from src.core.config.model_config import APIProviderSection, ModelConfig
from src.kernel.llm import LLMProviderUnavailableError
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.model_client.runtime import get_provider_probe
from src.kernel.llm.types import ModelEntry


REMOVED_CLIENT_TYPES = (
    "jev", "typesafe", "type_safe", "type-safe", " JeV ", "TYPESAFE",
    " TYPE-SAFE ", "jev_api", "jev-api", "JEV_CLIENT", "typesafe_ai",
    "typesafe-ai", "typesafe_system_one", "TypeSafe-System-One",
)


@pytest.mark.parametrize("client_type", REMOVED_CLIENT_TYPES)
def test_removed_provider_rejected_by_config(client_type: str) -> None:
    """直接建模拒绝已移除类型，不再把旧选项视为有效核心配置。"""
    with pytest.raises(ValidationError, match="client_type"):
        APIProviderSection(client_type=client_type)


@pytest.mark.parametrize("client_type", REMOVED_CLIENT_TYPES)
@pytest.mark.parametrize("entrypoint", ["load", "auto_update", "init"])
def test_removed_provider_load_preserves_invalid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client_type: str, entrypoint: str,
) -> None:
    """普通加载、自动更新和初始化均先报错，不能回退、改写或缓存无效配置。"""
    path = tmp_path / "removed-provider.toml"
    path.write_text(
        '[[api_providers]]\nname = "removed"\n'
        'api_key = "offline-placeholder"\nbase_url = "https://offline.invalid"\n'
        f'client_type = "{client_type}"\n',
        encoding="utf-8",
    )
    original = path.read_bytes()
    modified_at = path.stat().st_mtime_ns
    monkeypatch.setattr(model_config_module, "_global_model_config", None)

    with pytest.raises(ValidationError, match="client_type"):
        if entrypoint == "init":
            model_config_module.init_model_config(str(path))
        else:
            ModelConfig.load(path, auto_update=entrypoint == "auto_update")

    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == modified_at
    assert model_config_module._global_model_config is None


def test_provider_schema_has_no_removed_choices() -> None:
    """界面和 TOML 字段说明均不再把已移除类型展示成可用选项。"""
    field = APIProviderSection.model_fields["client_type"]
    assert set(get_args(field.annotation)) == {
        "openai", "openai_response", "anthropic", "gemini", "aiohttp_gemini",
        "google", "gemini_v2", "bedrock",
    }
    description = (field.description or "").casefold()
    assert "jev" not in description and "typesafe" not in description
    assert field.json_schema_extra["strict_choices"] is True


@pytest.mark.parametrize("client_type", REMOVED_CLIENT_TYPES)
@pytest.mark.parametrize("kind", ["chat", "embedding", "rerank", "asr"])
def test_removed_provider_never_resolves_to_another_client(
    client_type: str, kind: str,
) -> None:
    """四种请求入口都在调用客户端之前拒绝旧类型，显式注入也不能改变此边界。"""
    sentinel = cast(Any, object())
    registry = ModelClientRegistry(
        openai=sentinel, anthropic=sentinel, responses=sentinel,
        gemini=sentinel, bedrock=sentinel,
    )
    resolve = {
        "chat": registry.get_client_for_model,
        "embedding": registry.get_embedding_client_for_model,
        "rerank": registry.get_rerank_client_for_model,
        "asr": registry.get_asr_client_for_model,
    }[kind]
    model = cast(ModelEntry, {"client_type": client_type})
    with pytest.raises(LLMProviderUnavailableError, match="已从核心移除") as caught:
        resolve(model)
    assert "jev_api" not in str(caught.value)


@pytest.mark.parametrize("client_type", REMOVED_CLIENT_TYPES)
def test_removed_probe_lookup_fails_before_transport(client_type: str) -> None:
    """预检选择阶段拒绝旧类型，不返回占位探针或借用其他协议发送请求。"""
    with pytest.raises(LLMProviderUnavailableError, match="已从核心移除"):
        get_provider_probe(client_type)


@pytest.mark.parametrize(
    "module_name",
    ["src.kernel.jev", "src.core.config.jev", "src.app.plugin_system.api.jev_api"],
)
def test_removed_modules_are_not_importable(module_name: str) -> None:
    """旧核心模块及门面均不存在，不用占位模块维持虚假的导入兼容。"""
    assert importlib.util.find_spec(module_name) is None


def test_removed_api_and_events_are_not_exported() -> None:
    """API 聚合、版本登记和事件枚举删除 Jev，同时保留共享的观测与流事件。"""
    from src.app.plugin_system import api
    from src.core.components.types import EventType

    assert not hasattr(api, "jev_api")
    assert "jev_api" not in api.__all__
    assert "jev_api" not in api.PLUGIN_API_VERSIONS
    assert "llm_api" in api.PLUGIN_API_VERSIONS
    assert "request_inspector_api" in api.PLUGIN_API_VERSIONS
    assert not any("jev" in event.value.casefold() for event in EventType)
    assert EventType.ON_LLM_STREAM_FINISHED.value == "on_llm_stream_finished"
    assert EventType.ON_LLM_REQUEST_FAILED.value == "on_llm_request_failed"
