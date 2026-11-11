"""锁定供应商职责增量的声明、依赖及公共调用兼容约束。"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.kernel.llm import LLMRequest, LLMPayload, ROLE, Text
from src.kernel.llm.model_client.registry import ModelClientRegistry

ROOT = Path(__file__).resolve().parents[3]
NEW_MODULES = (
    "src/core/config/jev.py", "src/core/managers/llm_runtime.py",
    "src/kernel/llm/model_client/runtime.py", "src/kernel/llm/model_client/probe_contract.py",
    "test/core/config/test_jev_bridge.py", "test/core/managers/test_llm_runtime.py",
    "test/app/test_bot_supplier_boundary.py", "test/app/plugin_system/api/test_jev_boundary.py",
    "test/kernel/jev/test_transport_ownership.py", "test/kernel/llm/test_supplier_boundary.py",
    "test/kernel/llm/test_supplier_probe_resources.py", "test/kernel/llm/test_supplier_architecture_contract.py",
    "examples/src/core/llm_runtime_example.py",
)


@pytest.mark.parametrize("relative", NEW_MODULES)
def test_boundary_module_declarations(relative: str) -> None:
    """新增模块的类、函数及局部替身均有中文说明与完整参数返回注解。"""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    assert ast.get_docstring(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        documentation = ast.get_docstring(node) or ""
        assert any("\u4e00" <= char <= "\u9fff" for char in documentation), f"{relative}:{node.lineno}"
        if isinstance(node, ast.ClassDef):
            continue
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        arguments += [arg for arg in [node.args.vararg, node.args.kwarg] if arg is not None]
        assert all(arg.annotation is not None for arg in arguments if arg.arg not in {"self", "cls"})
        assert node.returns is not None


def test_boundary_import_direction_and_bot_responsibility() -> None:
    """职责约束配合行为替换测试，不把字符串消失当作唯一架构证明。"""
    for relative in ["src/kernel/llm/model_client/runtime.py", "src/kernel/llm/model_client/probe_contract.py"]:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("src.core", "src.app"))
    tree = ast.parse((ROOT / "src/app/runtime/bot.py").read_text(encoding="utf-8"))
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
               and node.name in {"_preflight_llm_providers", "shutdown"}]
    assert len(methods) == 2
    for method in methods:
        for node in ast.walk(method):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("google")
                assert "gemini_client" not in (node.module or "")
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"_pools", "_locks", "sdk", "get_api_key", "HttpOptions"}


async def test_public_request_accepts_create_only_client_without_lifecycle_introspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """替换仅实现 create 的客户端，公共请求及默认关闭不理解其 SDK/池。"""
    from src.kernel.llm.model_client import gemini_client, runtime

    class CreateOnlyClient:
        """没有 close、SDK 或池属性的外部聊天客户端。"""

        async def create(
            self, *, model_name: str, payloads: list[LLMPayload], tools: list[Any],
            request_name: str, model_set: Any, stream: bool,
        ) -> tuple[str, None, None]:
            """返回历史三元组，验证旧客户端不需新增任何方法。"""
            assert model_name == "local" and not stream
            return "本地结果", None, None

    model = {"api_provider": "offline", "base_url": "https://offline.invalid", "model_identifier": "local",
        "api_key": "unused-offline", "client_type": "openai", "max_retry": 0, "timeout": 2,
        "retry_interval": 0, "price_in": 0, "price_out": 0, "temperature": 0.7,
        "max_tokens": 16, "extra_params": {}}
    registry = ModelClientRegistry(openai=CreateOnlyClient())
    request = LLMRequest(model_set=[model], clients=registry, enable_metrics=False, context_manager=None)
    request.add_payload(LLMPayload(ROLE.USER, Text("离线")))
    response = await request.send(stream=False)
    assert await response == "本地结果"
    close = AsyncMock()
    monkeypatch.setattr(gemini_client, "close_gemini_clients", close)
    await runtime.close_default_model_clients()
    close.assert_awaited_once_with()
    assert registry.openai is not None


@pytest.mark.parametrize("extra", [[], {"gemini": []}])
def test_endpoint_parser_rejects_only_required_container_shapes(extra: Any) -> None:
    """端点解析拒绝必需容器错误，独立于生成参数形状检查。"""
    from src.kernel.llm.model_client.gemini_codec import resolve_gemini_endpoint
    from src.kernel.llm.exceptions import LLMConfigurationError

    with pytest.raises(LLMConfigurationError):
        resolve_gemini_endpoint({"model_identifier": "local", "extra_params": extra})


async def test_legacy_probe_transport_failure_is_safe_and_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧协议传输失败变为安全未验证，后一个条目仍能报告 HTTP 状态。"""
    import httpx
    from src.kernel.llm.model_client.runtime import get_provider_probe

    original = httpx.AsyncClient
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        """首请求失败且含脱敏哨兵，第二请求返回状态。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("sentinel-secret", request=request)
        return httpx.Response(200, json={})

    def make_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        """保持真实 HTTPX 上下文并禁用外部网络。"""
        kwargs["transport"] = httpx.MockTransport(handle)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    result = await get_provider_probe("openai").check([
        {"base_url": "https://offline.invalid", "api_key": "offline-key"},
        {"base_url": "https://offline.invalid", "api_key": "offline-key"},
    ], 1)
    assert [item.status for item in result] == ["unverified", "available"]
    assert "sentinel-secret" not in repr(result)
    assert calls == 2
