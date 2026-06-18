"""配置生成器 — 从向导配置 JSON 生成所有 TOML 配置文件。

生成的配置文件：
- config/core.toml
- config/model.toml
- config/mcp.toml
- config/plugins/coding_agent/config.toml
- config/plugins/coding_agent_webui/config.toml

使用方式：
    from desktop.config_generator import generate_configs
    generate_configs(wizard_config_dict, "config")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_configs(wizard_config: dict[str, Any], config_dir: str = "config") -> dict[str, Path]:
    """从向导配置生成所有 TOML 配置文件。

    Args:
        wizard_config: 向导配置字典，包含 api_provider、personality、model_profiles。
        config_dir: 配置目录路径，默认为 "config"。

    Returns:
        dict[str, Path]: 生成的文件路径映射（config_type -> path）。
    """
    base = Path(config_dir)
    base.mkdir(parents=True, exist_ok=True)

    generated: dict[str, Path] = {}

    # 1. core.toml
    core_path = base / "core.toml"
    _generate_core_toml(wizard_config, core_path)
    generated["core"] = core_path

    # 2. model.toml
    model_path = base / "model.toml"
    _generate_model_toml(wizard_config, model_path)
    generated["model"] = model_path

    # 3. mcp.toml
    mcp_path = base / "mcp.toml"
    _generate_mcp_toml(wizard_config, mcp_path)
    generated["mcp"] = mcp_path

    # 4. plugins/coding_agent/config.toml
    mcp_server_names = _get_all_mcp_server_names(wizard_config)
    ca_path = base / "plugins" / "coding_agent" / "config.toml"
    _generate_coding_agent_config(wizard_config, mcp_server_names, ca_path)
    generated["coding_agent"] = ca_path

    # 5. plugins/coding_agent_webui/config.toml
    webui_path = base / "plugins" / "coding_agent_webui" / "config.toml"
    _generate_webui_config(webui_path)
    generated["coding_agent_webui"] = webui_path

    return generated


# ─────────────────────────────────────────────────────────────
# 内部辅助函数
# ─────────────────────────────────────────────────────────────


def _generate_core_toml(wizard_config: dict[str, Any], path: Path) -> None:
    """生成 core.toml 配置文件。

    基于 CoreConfig.default() 修改关键配置项后渲染为 TOML。
    """
    from src.core.config.core_config import CoreConfig
    from src.kernel.config.core import _render_toml_with_signature

    data = CoreConfig.default()

    personality = wizard_config.get("personality", {})

    # Bot 节 — 桌面模式精简配置
    data["bot"]["ui_level"] = "minimal"
    data["bot"]["plugins_dir"] = "plugins"
    data["bot"]["logs_dir"] = "logs"
    data["bot"]["data_dir"] = "data"
    data["bot"]["shutdown_timeout"] = 5.0
    data["bot"]["llm_preflight_check"] = False
    data["bot"]["enable_watchdog"] = True

    # 数据库 — 使用 SQLite
    data["database"]["database_type"] = "sqlite"
    data["database"]["sqlite_path"] = "data/mofox_code.db"

    # 人格配置
    data["personality"]["nickname"] = personality.get("nickname", "小狐狸")
    data["personality"]["personality_core"] = personality.get("personality_core", "友好、活泼、乐于助人")
    data["personality"]["reply_style"] = personality.get("reply_style", "自然口语化")
    data["personality"]["identity"] = personality.get("identity", "AI助手")
    data["personality"]["background_story"] = personality.get("background_story", "")
    data["personality"]["personality_side"] = personality.get("personality_side", "")
    data["personality"]["alias_names"] = personality.get("alias_names", [])
    data["personality"]["safety_guidelines"] = personality.get("safety_guidelines", [])
    data["personality"]["negative_behaviors"] = personality.get("negative_behaviors", [])

    # 权限 — 桌面模式无平台用户
    data["permissions"]["owner_list"] = []

    # HTTP 路由 — 桌面模式不需要主 HTTP 服务器
    data["http_router"]["enable_http_router"] = False

    # 遥测 — 全部关闭
    data["telemetry"]["enabled"] = False
    data["cloud_telemetry"]["client_enabled"] = False

    # 插件依赖 — 关闭自动安装
    data["plugin_deps"]["enabled"] = False

    # 插件市场 — 关闭
    data["plugin_market"]["enabled"] = False

    # LLM 统计 — 关闭
    data["llm_stats"]["enabled"] = False

    toml_content = _render_toml_with_signature(CoreConfig, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(toml_content, encoding="utf-8")


def _generate_model_toml(wizard_config: dict[str, Any], path: Path) -> None:
    """生成 model.toml 配置文件。

    支持新格式（多提供商 + 多模型）以及旧格式（单提供商）向后兼容。
    """
    from src.core.config.model_config import (
        APIProviderSection,
        ModelConfig,
        ModelInfoSection,
        TaskConfigSection,
    )
    from src.kernel.config.core import _render_toml_with_signature

    model_profiles = wizard_config.get("model_profiles", [])
    data: dict[str, Any] = {}

    # ── 兼容旧格式（单 api_provider）与新格式（api_providers 数组）──
    if "api_providers" in wizard_config and isinstance(wizard_config["api_providers"], list):
        raw_providers = wizard_config["api_providers"]
        raw_models = wizard_config.get("models", [])
        roles = wizard_config.get("roles", {})
        _base_url_defaults = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}

        # 构建提供商列表
        data["api_providers"] = []
        for p in raw_providers:
            ct = p.get("client_type", "openai")
            raw_url = p.get("base_url", "").strip()
            data["api_providers"].append({
                "name": p.get("name", ""),
                "base_url": raw_url if raw_url else _base_url_defaults.get(ct, ""),
                "api_key": p.get("api_key", ""),
                "client_type": ct,
                "max_retry": 2,
                "timeout": 30,
                "retry_interval": 10,
            })

        # 构建模型列表 — name 由 "ProviderName/ModelId" 程序化生成
        data["models"] = []
        for m in raw_models:
            model_id = m.get("model_id", "")
            provider = m.get("api_provider", "")
            data["models"].append({
                "model_identifier": model_id,
                "name": f"{provider}/{model_id}",
                "api_provider": provider,
                "price_in": 0.0,
                "price_out": 0.0,
                "max_context": m.get("max_context", 131072),
            })

        # 角色 → 任务映射（roles 中的值已经是 "ProviderName/ModelId" 格式的 name）
        fallback = data["models"][0]["name"] if data["models"] else "OpenAI/gpt-4o"
        main_name = roles.get("main", fallback)
        coder_name = roles.get("coder", main_name)
        researcher_name = roles.get("researcher", main_name)
        reviewer_name = roles.get("reviewer", main_name)
        title_name = roles.get("title", main_name)

    else:
        # 旧格式向后兼容
        api_provider_data = wizard_config.get("api_provider", {})
        provider_name = api_provider_data.get("name", "OpenAI")
        client_type = api_provider_data.get("client_type", "openai")
        models_map = api_provider_data.get("models", {})
        _base_url_defaults = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
        raw_base_url = api_provider_data.get("base_url", "").strip()
        base_url = raw_base_url if raw_base_url else _base_url_defaults.get(client_type, "")

        data["api_providers"] = [{
            "name": provider_name, "base_url": base_url,
            "api_key": api_provider_data.get("api_key", ""),
            "client_type": client_type, "max_retry": 2, "timeout": 30, "retry_interval": 10,
        }]

        main_id = models_map.get("main", "gpt-4o")
        for mid in dict.fromkeys([
            main_id,
            models_map.get("coder", main_id),
            models_map.get("researcher", main_id),
            models_map.get("reviewer", main_id),
            models_map.get("title", main_id),
        ]):
            data["models"].append({
                "model_identifier": mid, "name": f"{provider_name}/{mid}",
                "api_provider": provider_name, "price_in": 0.0, "price_out": 0.0, "max_context": 131072,
            })

        def _n(mid: str) -> str:
            return f"{provider_name}/{mid}"

        main_name = _n(main_id)
        coder_name = _n(models_map.get("coder", main_id))
        researcher_name = _n(models_map.get("researcher", main_id))
        reviewer_name = _n(models_map.get("reviewer", main_id))
        title_name = _n(models_map.get("title", main_id))
        fallback = main_name

    # ── 任务配置 ──────────────────────────────────────────────────
    default_profile = model_profiles[0] if model_profiles else {}

    def _make_task(model_name: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
        p = profile or default_profile
        return {
            "model_list": [model_name],
            "max_tokens": p.get("max_tokens", 16384),
            "temperature": p.get("temperature", 0.5),
            "concurrency_count": 1,
        }

    data["model_tasks"] = {
        "coding_main": _make_task(main_name),
        "coding_coder": _make_task(coder_name),
        "coding_researcher": _make_task(researcher_name),
        "coding_reviewer": _make_task(reviewer_name),
        "coding_title": _make_task(title_name),
        "utils": _make_task(main_name),
        "utils_small": _make_task(main_name),
        "actor": _make_task(main_name),
        "sub_actor": _make_task(main_name),
        "vlm": _make_task(main_name),
        "voice": _make_task(main_name),
        "video": _make_task(main_name),
        "tool_use": _make_task(main_name),
        "embedding": {"model_list": [main_name], "max_tokens": 800, "temperature": 0.7, "concurrency_count": 1, "embedding_dimension": 1024},
    }

    toml_content = _render_toml_with_signature(ModelConfig, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(toml_content, encoding="utf-8")


def _generate_mcp_toml(wizard_config: dict[str, Any], path: Path) -> None:
    """生成 mcp.toml — 预置 fetch 和 bing-search，并合并用户在向导中配置的 MCP 服务器。"""
    from src.core.config.mcp_config import MCPConfig
    from src.kernel.config.core import _render_toml_with_signature

    data = MCPConfig.default()

    # 预置 fetch 服务器
    data["mcp"]["stdio_servers"] = {
        "fetch": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "env": {},
            "instructions": "Fetch URLs and extract their contents as markdown. Use for reading web pages, documentation, and API responses.",
            "defer_loading": False,
        },
        "bing-search": {
            "command": "uvx",
            "args": ["mcp-server-bing-search"],
            "env": {},
            "instructions": "Search the web using Bing. Use for finding up-to-date information, documentation, and references.",
            "defer_loading": False,
        },
    }

    # 合并用户在向导中配置的 MCP 服务器（覆盖同名预设）
    user_mcp_servers = wizard_config.get("mcp_servers", [])
    for server in user_mcp_servers:
        if not server.get("name") or not server.get("command"):
            continue
        if server.get("enabled") is False:
            continue
        data["mcp"]["stdio_servers"][server["name"]] = {
            "command": server["command"],
            "args": server.get("args", []),
            "env": server.get("env", {}),
            "instructions": server.get("instructions", ""),
            "defer_loading": False,
        }

    toml_content = _render_toml_with_signature(MCPConfig, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(toml_content, encoding="utf-8")


def _get_all_mcp_server_names(wizard_config: dict[str, Any]) -> list[str]:
    """汇总所有 MCP 服务器名称（预设 + 用户配置），用于 coding_agent 的 MCP 引用。"""
    server_names = {"fetch", "bing-search"}  # 预设服务器
    for server in wizard_config.get("mcp_servers", []):
        name = server.get("name", "").strip()
        if name and server.get("enabled", True):
            server_names.add(name)
    return sorted(server_names)


def _generate_coding_agent_config(
    wizard_config: dict[str, Any],
    mcp_server_names: list[str],
    path: Path,
) -> None:
    """生成 plugins/coding_agent/config.toml。"""
    from plugins.coding_agent.config import CodingAgentConfig, CoderModelProfile
    from src.kernel.config.core import _render_toml_with_signature

    data = CodingAgentConfig.default()
    model_profiles = wizard_config.get("model_profiles", [])

    # WebSocket 配置
    data["ws"]["host"] = "127.0.0.1"
    data["ws"]["port"] = 8766

    # Console 工具 — 自动检测终端
    data["console"]["preferred_terminal"] = ""

    # 模型任务名保持默认
    # coding_main, coding_coder, coding_researcher, coding_reviewer, coding_title

    # MCP 服务器 — 使用所有可用的服务器名（预设 + 用户配置）
    data["mcp"]["main_mcp_servers"] = list(mcp_server_names)
    data["mcp"]["coder_mcp_servers"] = list(mcp_server_names)
    data["mcp"]["researcher_mcp_servers"] = list(mcp_server_names)

    # 模型名统一使用 "ProviderName/ModelId" 格式，与 model.toml 中的 name 一致
    if "roles" in wizard_config:
        # 新格式：roles.main 已经是 "ProviderName/ModelId"
        main_model_name = wizard_config["roles"].get("main", "OpenAI/gpt-4o")
    else:
        # 旧格式兼容
        api_provider = wizard_config.get("api_provider", {})
        provider_name = api_provider.get("name", "OpenAI")
        models_map = api_provider.get("models", {})
        main_model_id = models_map.get("main", "gpt-4o")
        main_model_name = f"{provider_name}/{main_model_id}"

    # 模型 Profile 列表
    if model_profiles:
        data["model_profiles"] = []
        for mp in model_profiles:
            data["model_profiles"].append({
                "profile_name": mp.get("profile_name", "Default"),
                "model_name": mp.get("model_name", ""),
                "tags": mp.get("tags", []),
                "description": mp.get("description", ""),
                "temperature": mp.get("temperature"),
                "max_tokens": mp.get("max_tokens"),
            })
    else:
        # 默认 profile — 引用新的 model name 格式
        data["model_profiles"] = [
            {
                "profile_name": "Default",
                "model_name": main_model_name,
                "tags": ["通用"],
                "description": "默认模型配置",
                "temperature": 0.5,
                "max_tokens": 16384,
            }
        ]

    toml_content = _render_toml_with_signature(CodingAgentConfig, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(toml_content, encoding="utf-8")


def _generate_webui_config(path: Path) -> None:
    """生成 plugins/coding_agent_webui/config.toml。"""
    from plugins.coding_agent_webui.config import WebUIConfig
    from src.kernel.config.core import _render_toml_with_signature

    data = WebUIConfig.default()

    # 服务器配置
    data["server"]["host"] = "127.0.0.1"
    data["server"]["port"] = 8681

    # WebSocket 后端
    data["ws"]["host"] = "127.0.0.1"
    data["ws"]["port"] = 8766

    # UI 配置
    data["ui"]["title"] = "MoFox Code"

    toml_content = _render_toml_with_signature(WebUIConfig, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(toml_content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# CLI 入口（用于测试）
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python config_generator.py <wizard_config.json> [config_dir]")
        sys.exit(1)

    config_path = Path(sys.argv[1])
    if not config_path.exists():
        print(f"Wizard config file not found: {config_path}")
        sys.exit(1)

    wizard_config = json.loads(config_path.read_text(encoding="utf-8"))
    config_dir = sys.argv[2] if len(sys.argv) > 2 else "config"

    generated = generate_configs(wizard_config, config_dir)
    for key, path in generated.items():
        print(f"  ✓ {key}: {path}")
    print(f"\nDone! Generated {len(generated)} config files.")
