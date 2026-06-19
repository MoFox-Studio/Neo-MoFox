"""配置解析器 — 将现有的 TOML 配置文件解析为向导配置 JSON。"""

import tomllib
from pathlib import Path
from typing import Any

def parse_configs(config_dir: str = "config") -> dict[str, Any]:
    """从 TOML 配置文件读取配置，并转换为 wizard_config 格式。"""
    cfg_dir = Path(config_dir)
    if not cfg_dir.exists():
        return {"status": "not_configured"}

    def _read_toml(filename: str) -> dict:
        p = cfg_dir / filename
        if not p.exists():
            return {}
        with open(p, "rb") as f:
            return tomllib.load(f)

    model_data = _read_toml("model.toml")
    core_data = _read_toml("core.toml")
    mcp_data = _read_toml("mcp.toml")
    
    # 也尝试读取 coding_agent 的配置以获取 temperature 和 max_tokens
    ca_data = _read_toml("plugins/coding_agent/config.toml")

    # ── API Providers ─────────────────────────
    raw_providers = model_data.get("api_providers", [])
    if isinstance(raw_providers, dict):
        raw_providers = [raw_providers]
    api_providers = []
    for p in raw_providers:
        if not isinstance(p, dict):
            continue
        raw_key = p.get("api_key", "")
        api_key = raw_key[0] if isinstance(raw_key, list) and raw_key else raw_key
        api_providers.append({
            "name": p.get("name", ""),
            "api_key": api_key if isinstance(api_key, str) else "",
            "base_url": p.get("base_url", ""),
            "client_type": p.get("client_type", "openai"),
            "max_retry": p.get("max_retry", 2),
            "timeout": p.get("timeout", 30),
            "retry_interval": p.get("retry_interval", 10),
        })

    # ── Models ─────────────────────────────────
    raw_models = model_data.get("models", [])
    if isinstance(raw_models, dict):
        raw_models = [raw_models]
    models_out = []
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        models_out.append({
            "model_id": m.get("model_identifier", ""),
            "api_provider": m.get("api_provider", api_providers[0]["name"] if api_providers else ""),
            "max_context": m.get("max_context", ""),
            "price_in": m.get("price_in", 0.0),
            "price_out": m.get("price_out", 0.0),
            "cache_hit_price_in": m.get("cache_hit_price_in"),
            "force_stream_mode": m.get("force_stream_mode", False),
            "tool_call_compat": m.get("tool_call_compat", False),
            "anti_truncation": m.get("anti_truncation", False),
            "extra_params": m.get("extra_params", {}),
        })

    # ── Roles ──────────────────────────────────
    tasks = model_data.get("model_tasks", {})
    def _task_name(task_name: str) -> str:
        t = tasks.get(task_name, {})
        ml = t.get("model_list", [])
        return ml[0] if ml else ""

    main_name = _task_name("coding_main") or _task_name("utils")
    coder_name = _task_name("coding_coder") or main_name
    researcher_name = _task_name("coding_researcher") or main_name
    reviewer_name = _task_name("coding_reviewer") or main_name
    title_name = _task_name("coding_title") or main_name

    roles = {
        "main": main_name,
        "coder": coder_name,
        "researcher": researcher_name,
        "reviewer": reviewer_name,
        "title": title_name,
    }

    # ── Personality ────────────────────────────
    personality = core_data.get("personality", {})

    # ── MCP Servers ────────────────────────────
    mcp_section = mcp_data.get("mcp", {})
    mcp_servers = []
    # 从 coding_agent 的配置中获取哪些是激活的
    ca_mcp = ca_data.get("mcp", {})
    active_mcp = set(ca_mcp.get("main_mcp_servers", []))
    
    for name, cfg in mcp_section.get("stdio_servers", {}).items():
        if not isinstance(cfg, dict):
            continue
        mcp_servers.append({
            "name": name,
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []),
            "enabled": name in active_mcp or not active_mcp, # 如果 active_mcp 为空，默认认为是刚导入或者未配置，默认开启
        })

    # ── Model Profiles ─────────────────────────
    model_profiles = ca_data.get("model_profiles", [])
    if not model_profiles:
        model_profiles = [
            {
                "profile_name": "Default",
                "model_name": main_name,
                "temperature": 0.5,
                "max_tokens": 16384,
            },
            {
                "profile_name": "Coder",
                "model_name": coder_name,
                "temperature": 0.2,
                "max_tokens": 16384,
            }
        ]
    elif len(model_profiles) == 1:
        # 兼容老配置：如果只有一个 Default，补全 Coder
        model_profiles.append({
            "profile_name": "Coder",
            "model_name": coder_name,
            "temperature": 0.2,
            "max_tokens": 16384,
        })

    return {
        "status": "ok",
        "api_providers": api_providers,
        "models": models_out,
        "roles": roles,
        "personality": {
            "nickname": personality.get("nickname", "MoFox"),
            "alias_names": personality.get("alias_names", []),
            "personality_core": personality.get("personality_core", ""),
            "personality_side": personality.get("personality_side", ""),
            "reply_style": personality.get("reply_style", ""),
            "identity": personality.get("identity", ""),
            "background_story": personality.get("background_story", ""),
        },
        "mcp_servers": mcp_servers,
        "model_profiles": model_profiles,
        "coding_agent": {
            "tui_username": ca_data.get("ws", {}).get("tui_username", "User"),
            "preferred_terminal": ca_data.get("console", {}).get("preferred_terminal", ""),
            "default_timeout": ca_data.get("console", {}).get("default_timeout", 30),
            "max_output_lines": ca_data.get("console", {}).get("max_output_lines", 200),
            "cache_ttl_hours": ca_data.get("context", {}).get("cache_ttl_hours", 24),
            "max_parallel_researchers": ca_data.get("context", {}).get("max_parallel_researchers", 6),
        },
    }
