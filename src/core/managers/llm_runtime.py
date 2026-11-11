"""为应用启动准备有效模型配置，并调用内核的非生成探测能力。

本模块只处理领域配置选择、合并及失败隔离，不构造 SDK、HTTP 请求或客户端池。
探测产生的临时资源属于内核能力；这里不接管外部注入客户端的生命周期。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from src.core.config.model_config import APIProviderSection, ModelConfig, ModelInfoSection
from src.kernel.llm.model_client.runtime import ProviderProbeResult, get_provider_probe


async def preflight_provider(
    model_config: ModelConfig,
    provider: APIProviderSection,
    *,
    timeout: float,
    trust_env: bool,
    models: list[ModelInfoSection | dict[str, Any]] | None = None,
) -> list[ProviderProbeResult]:
    """准备一个提供商的技术输入，普通配置失败逐项隔离，取消继续传播。

    Args:
        model_config: 已加载的领域配置，复用其生成链路的有效配置合并入口。
        provider: 要检查的提供商；密钥仅在准备输入时取一次快照。
        timeout: 每个技术探测分组的秒级预算，由能力负责技术校验。
        trust_env: 无模型时 provider 默认输入使用的全局代理策略。
        models: 可选配置条目；默认使用领域配置中的所有模型。

    Returns:
        不包含凭据或配置原文的检查结果。确无模型时标为 provider-only；
        模型全部合并失败时仍传空条目，不伪造默认配置可用。
    """
    results: list[ProviderProbeResult] = []
    try:
        probe = get_provider_probe(provider.client_type)
        if not probe.requires_model_entries and not probe.requires_provider_entry:
            return await probe.check([], timeout, provider_only=True)
        entries: list[dict[str, Any]] = []
        selected: list[ModelInfoSection | dict[str, Any]] = []
        if probe.requires_model_entries:
            for model in model_config.models if models is None else models:
                name = model.get("api_provider") if isinstance(model, dict) else model.api_provider
                if name == provider.name:
                    selected.append(model)
            for index, model in enumerate(selected, 1):
                try:
                    if isinstance(model, dict):
                        entries.append(model)
                    else:
                        entries.extend(model_config.get_model_set_by_name(model.name))
                except Exception as error:
                    results.append(ProviderProbeResult(
                        "unverified", f"配置项 {index} 合并失败 ({type(error).__name__})，该项未验证",
                    ))
        provider_only = not selected
        if not probe.requires_model_entries or provider_only:
            try:
                key = provider.get_api_key()
            except Exception:
                if not probe.allows_anonymous_probe:
                    raise
                # 仅能力明确声明的历史匿名探测可在密钥读取失败后继续。
                key = ""
            entries.append({
                "api_provider": provider.name,
                "client_type": provider.client_type,
                "api_key": key,
                "base_url": provider.base_url,
                "timeout": float(provider.timeout),
                "extra_params": {"trust_env": trust_env},
            })
        results.extend(await probe.check(entries, timeout, provider_only=provider_only))
    except Exception as error:
        results.append(ProviderProbeResult(
            "unverified", f"提供商预检失败 ({type(error).__name__})，未验证",
        ))
    return results


async def preflight_llm_providers(
    model_config: ModelConfig, *, timeout: float, trust_env: bool,
) -> list[ProviderProbeResult]:
    """并行检查领域配置中的提供商，不让一项普通失败阻断启动。

    Args:
        model_config: 已加载的配置，不在本入口读取真实配置文件。
        timeout: 传给技术能力的每组探测上限秒数。
        trust_env: provider-only 输入的全局代理配置。

    Returns:
        按配置顺序展开的安全结果；没有提供商时返回空列表。外部取消传播。
    """
    groups = await asyncio.gather(*(
        preflight_provider(model_config, provider, timeout=timeout, trust_env=trust_env)
        for provider in model_config.api_providers
    ))
    return [
        replace(result, detail=f"提供商 {provider_index}: {result.detail}")
        for provider_index, group in enumerate(groups, 1)
        for result in group
    ]
