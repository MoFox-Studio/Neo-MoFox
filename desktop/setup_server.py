"""Setup API 服务器 — 首次启动时接收来自前端的向导配置。

当 config/ 目录不存在时，后端启动一个临时 HTTP 服务器，
等待前端通过 POST /api/setup 提交向导配置 JSON。
收到配置后生成 TOML 文件并返回成功，然后退出让 launcher 重启。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_proj_root = Path(__file__).resolve().parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))


def _create_setup_app(config_dir: str, server_ref: list | None = None):
    """创建临时 FastAPI 应用，处理 /api/setup 端点。

    Args:
        config_dir: 配置目录路径。
        server_ref: 可变列表，用于延迟绑定 uvicorn.Server 实例，
                    以便端点在配置生成后触发服务器关闭。

    Returns:
        FastAPI: 配置好的应用实例。
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    app = FastAPI(title="MoFox Code Setup", docs_url=None, redoc_url=None)

    # CORS — 允许 Tauri webview 和浏览器访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/setup/status")
    async def get_setup_status():
        """返回 Setup API 状态，前端用于检测是否需要向导。"""
        config_core = Path(config_dir) / "core.toml"
        return {
            "status": "awaiting_config" if not config_core.exists() else "configured",
            "message": "等待向导配置" if not config_core.exists() else "已配置",
        }

    @app.get("/api/config")
    async def get_config():
        """返回 UI 配置，包括 desktop_mode 检测。"""
        return {
            "title": "MoFox Code",
            "default_theme": "light",
            "avatar_url": "/logo.png",
            "desktop_mode": os.environ.get("MOFOX_CODE_DESKTOP") == "1",
        }

    @app.post("/api/setup")
    async def submit_setup(config: dict):
        """接收向导配置 JSON，生成 TOML 文件。

        Args:
            config: 向导配置字典。

        Returns:
            JSONResponse: 包含状态和生成的文件列表。
        """
        try:
            from desktop.config_generator import generate_configs

            generated = generate_configs(config, config_dir)

            # 配置生成成功后，延迟关闭 Setup Server，
            # 让 launcher 继续执行 start_bot()
            if server_ref:
                asyncio.get_event_loop().call_later(
                    1.0, lambda: setattr(server_ref[0], "should_exit", True)
                )

            return JSONResponse(
                content={
                    "status": "ok",
                    "message": "配置已生成，后端即将重启",
                    "files": {k: str(v) for k, v in generated.items()},
                },
                status_code=200,
            )

        except Exception as e:
            import traceback

            traceback.print_exc()
            return JSONResponse(
                content={
                    "status": "error",
                    "message": f"配置生成失败: {e}",
                },
                status_code=500,
            )

    @app.post("/api/setup/import")
    async def import_existing_config(request: dict):
        """从已有 Neo-MoFox 实例的 config 目录导入配置。

        读取指定目录下的 core.toml、model.toml、mcp.toml，
        返回与向导前端相同格式的配置字典，供前端回填表单。

        Args:
            request: 包含 config_dir（配置文件目录路径）的请求体。

        Returns:
            JSONResponse: 包含 wizard 格式配置的响应。
        """
        import tomllib

        cfg_dir = Path(request.get("config_dir", ""))
        if not cfg_dir or not cfg_dir.exists():
            return JSONResponse(
                content={"status": "error", "message": f"目录不存在: {cfg_dir}"},
                status_code=400,
            )

        def _read_toml(filename: str) -> dict:
            p = cfg_dir / filename
            if not p.exists():
                return {}
            with open(p, "rb") as f:
                return tomllib.load(f)

        try:
            model_data = _read_toml("model.toml")
            core_data = _read_toml("core.toml")
            mcp_data = _read_toml("mcp.toml")

            # ── API Providers（全部导出）─────────────────────────
            raw_providers = model_data.get("api_providers", [])
            if not raw_providers:
                return JSONResponse(
                    content={"status": "error", "message": "model.toml 中未找到 api_providers"},
                    status_code=400,
                )
            # 兼容：如果 raw_providers 是 dict（单提供商的 [api_providers] 表），包装为列表
            if isinstance(raw_providers, dict):
                raw_providers = [raw_providers]
            api_providers = []
            for p in raw_providers:
                if not isinstance(p, dict):
                    continue
                # api_key 可能是字符串或列表（密钥轮询），统一为字符串
                raw_key = p.get("api_key", "")
                api_key = raw_key[0] if isinstance(raw_key, list) and raw_key else raw_key
                api_providers.append({
                    "name": p.get("name", ""),
                    "api_key": api_key if isinstance(api_key, str) else "",
                    "base_url": p.get("base_url", ""),
                    "client_type": p.get("client_type", "openai"),
                })

            # ── Models（全部导出）─────────────────────────────────
            raw_models = model_data.get("models", [])
            # 兼容：如果 raw_models 是 dict（单模型的 [models] 表），包装为列表
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
                })

            # ── Roles（从 model_tasks 映射，值为 "Provider/ModelId" 格式）──
            tasks = model_data.get("model_tasks", {})

            def _task_name(task_name: str) -> str:
                """提取 task 引用的 model name（已是 ProviderName/ModelId 格式）。"""
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

            # ── 人格信息 ──────────────────────────────────────────
            personality = core_data.get("personality", {})

            # ── MCP 服务器 ────────────────────────────────────────
            mcp_section = mcp_data.get("mcp", {})
            mcp_servers = []
            for name, cfg in mcp_section.get("stdio_servers", {}).items():
                if not isinstance(cfg, dict):
                    continue
                mcp_servers.append({
                    "name": name,
                    "command": cfg.get("command", ""),
                    "args": " ".join(cfg.get("args", [])),
                    "enabled": True,
                })

            result = {
                "api_providers": api_providers,
                "models": models_out,
                "roles": roles,
                "personality": {
                    "nickname": personality.get("nickname", ""),
                    "alias_names": personality.get("alias_names", []),
                    "personality_core": personality.get("personality_core", ""),
                    "personality_side": personality.get("personality_side", ""),
                    "reply_style": personality.get("reply_style", ""),
                    "identity": personality.get("identity", ""),
                    "background_story": personality.get("background_story", ""),
                },
                "mcp_servers": mcp_servers,
            }

            return JSONResponse(content={"status": "ok", "data": result}, status_code=200)

        except Exception as e:
            import traceback

            traceback.print_exc()
            return JSONResponse(
                content={"status": "error", "message": f"读取配置失败: {e}"},
                status_code=500,
            )

    # 静态文件回退：如果前端 SPA 存在则 serve，否则返回 404
    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve 前端 SPA（如果可用）。"""
        frontend_dist = Path("plugins/coding_agent_webui/dist")
        if frontend_dist.exists():
            from fastapi.staticfiles import StaticFiles

            # 懒挂载
            if not any(r.path == "/" for r in app.routes):
                app.mount("/", StaticFiles(directory=str(frontend_dist), html=True))
            from fastapi.responses import FileResponse

            file_path = frontend_dist / path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(frontend_dist / "index.html"))
        return JSONResponse(
            content={"error": "Frontend not built"},
            status_code=404,
        )

    return app


async def run_setup_server(
    host: str = "127.0.0.1",
    port: int = 8681,
    config_dir: str = "config",
) -> None:
    """启动临时 Setup API 服务器。

    该函数会阻塞直到收到有效配置或进程被终止。

    Args:
        host: 监听地址。
        port: 监听端口。
        config_dir: 配置目录路径。
    """
    import uvicorn

    server_ref: list = []
    app = _create_setup_app(config_dir, server_ref)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    server_ref.append(server)  # 绑定 server 实例，供端点触发关闭

    print(f"[Setup] Setup API 服务器已启动: http://{host}:{port}")
    print(f"[Setup] POST http://{host}:{port}/api/setup 提交配置")
    print(f"[Setup] GET  http://{host}:{port}/api/setup/status 检查状态")

    try:
        await server.serve()
    except asyncio.CancelledError:
        pass
    finally:
        print("[Setup] Setup API 服务器已关闭。")


if __name__ == "__main__":
    asyncio.run(run_setup_server())
