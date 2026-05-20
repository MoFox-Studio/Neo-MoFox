"""通用遥测 HTTP 面板。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.utils.security import VerifiedDep

from .database import get_telemetry_collector

_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Neo-MoFox Telemetry</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f3efe7;
      --panel: #fffaf1;
      --ink: #1f1d19;
      --muted: #756a5c;
      --accent: #b84c2a;
      --line: #dccdb7;
    }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
      background: radial-gradient(circle at top, #fffaf1 0%, var(--bg) 60%);
      color: var(--ink);
    }
    main {
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }
    .controls, .grid {
      display: grid;
      gap: 12px;
    }
    .controls {
      grid-template-columns: 1fr auto auto;
      margin: 20px 0;
    }
    .grid {
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 12px 30px rgba(82, 52, 31, 0.06);
      margin-top: 16px;
    }
    .metric {
      font-size: 28px;
      font-weight: 700;
      margin-top: 8px;
    }
    label {
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    input, button {
      width: 100%;
      border-radius: 12px;
      border: 1px solid var(--line);
      padding: 10px 12px;
      font: inherit;
      box-sizing: border-box;
      background: white;
    }
    button {
      width: auto;
      background: var(--accent);
      color: white;
      cursor: pointer;
      border: none;
      padding: 0 18px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }
    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      font-size: 14px;
      vertical-align: top;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <main>
    <h1>Telemetry</h1>
    <p>输入 API Key 后读取遥测摘要、运行时健康状态和近期事件。</p>
    <div class="controls">
      <div>
        <label for="api-key">X-API-Key</label>
        <input id="api-key" type="password" placeholder="输入 API Key" />
      </div>
      <button id="refresh">刷新</button>
      <button id="clear">清空事件</button>
    </div>
    <div class="grid" id="metrics"></div>
    <section class="card">
      <h2>运行时健康</h2>
      <pre id="runtime">{}</pre>
    </section>
    <section class="card">
      <h2>云端遥测状态</h2>
      <pre id="cloud-status">{}</pre>
    </section>
    <section class="card">
      <h2>分域摘要</h2>
      <table>
        <thead>
          <tr><th>Domain</th><th>Total</th><th>Error</th><th>Warning</th><th>Last Event</th></tr>
        </thead>
        <tbody id="domains"></tbody>
      </table>
    </section>
    <section class="card">
      <h2>近期事件</h2>
      <table>
        <thead>
          <tr><th>Time</th><th>Domain</th><th>Event</th><th>Severity</th><th>Summary</th></tr>
        </thead>
        <tbody id="events"></tbody>
      </table>
    </section>
  </main>
  <script>
    const apiKeyInput = document.getElementById('api-key');
    const metrics = document.getElementById('metrics');
    const domains = document.getElementById('domains');
    const events = document.getElementById('events');
    const runtime = document.getElementById('runtime');
    const cloudStatus = document.getElementById('cloud-status');

    apiKeyInput.value = localStorage.getItem('telemetry-api-key') || '';

    function headers() {
      const apiKey = apiKeyInput.value.trim();
      localStorage.setItem('telemetry-api-key', apiKey);
      return apiKey ? { 'X-API-Key': apiKey } : {};
    }

    function renderMetrics(summary, llmSummary) {
      const telemetryStatus = summary.enabled ? 'On' : 'Off';
      const cards = [
        ['Telemetry Events', summary.total_events],
        ['Telemetry Errors', summary.error_events],
        ['Telemetry Warnings', summary.warning_events],
        ['Telemetry Status', telemetryStatus],
        ['LLM Requests', llmSummary.total_requests || 0],
        ['LLM Tokens', llmSummary.total_tokens || 0],
        ['LLM Cache Hit Rate', ((llmSummary.cache_hit_rate || 0) * 100).toFixed(1) + '%'],
        ['LLM Window', (llmSummary.window_hours || 0) + 'h'],
      ];
      metrics.innerHTML = cards.map(([label, value]) => `
        <div class="card">
          <div>${label}</div>
          <div class="metric">${value}</div>
        </div>
      `).join('');
    }

    function renderDomains(items) {
      domains.innerHTML = items.map(item => `
        <tr>
          <td>${item.domain}</td>
          <td>${item.total_events}</td>
          <td>${item.error_events}</td>
          <td>${item.warning_events}</td>
          <td>${item.last_event_at ? new Date(item.last_event_at * 1000).toLocaleString() : '-'}</td>
        </tr>
      `).join('');
    }

    function renderEvents(items) {
      events.innerHTML = items.map(item => `
        <tr>
          <td>${item.timestamp ? new Date(item.timestamp * 1000).toLocaleString() : '-'}</td>
          <td>${item.domain}</td>
          <td>${item.event_name}</td>
          <td>${item.severity}</td>
          <td>${item.summary || '-'}</td>
        </tr>
      `).join('');
    }

    async function load() {
      const response = await fetch('./api/summary', { headers: headers() });
      const data = await response.json();
      if (!response.ok) {
        alert(data.detail || data.error || '加载失败');
        return;
      }
      renderMetrics(data.telemetry_summary, data.llm_summary);
      renderDomains(data.telemetry_domains);
      renderEvents(data.recent_events);
      runtime.textContent = JSON.stringify(data.runtime_health, null, 2);
      cloudStatus.textContent = JSON.stringify(data.cloud_telemetry_status, null, 2);
    }

    async function clearEvents() {
      const response = await fetch('./api/events', {
        method: 'DELETE',
        headers: headers(),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        alert(data.detail || data.error || '清空失败');
        return;
      }
      await load();
    }

    document.getElementById('refresh').addEventListener('click', load);
    document.getElementById('clear').addEventListener('click', clearEvents);
    load().catch(console.error);
  </script>
</body>
</html>
"""


class TelemetryDashboard:
    """通用遥测 HTTP 面板。"""

    def __init__(self) -> None:
        self._mounted_apps: set[int] = set()

    def mount(self, app: Any, prefix: str = "/_telemetry") -> None:
        """将遥测页面和 API 挂载到 FastAPI 应用。"""

        app_id = id(app)
        if app_id in self._mounted_apps:
            return

        self._mounted_apps.add(app_id)
        app.include_router(self._build_router(), prefix=prefix)

    def _build_router(self) -> APIRouter:
        """构建遥测面板路由。"""
        router = APIRouter()
        api_router = APIRouter(dependencies=[VerifiedDep])

        @router.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def dashboard_page() -> HTMLResponse:
            return HTMLResponse(_DASHBOARD_HTML)

        @api_router.get("/summary")
        async def get_summary() -> JSONResponse:
            from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager
            from src.app.cloud_telemetry import get_cloud_telemetry_status_summary
            from src.kernel.concurrency import get_task_manager, get_watchdog
            from src.kernel.llm.stats import get_llm_stats_collector

            collector = get_telemetry_collector()
            telemetry_summary = await collector.get_summary()
            telemetry_domains = await collector.get_domain_summary()
            recent_events = await collector.get_recent(limit=30)
            llm_summary = await get_llm_stats_collector().get_summary()

            runtime_health = {
                "watchdog": get_watchdog().get_stats(),
                "task_manager": get_task_manager().get_stats(),
                "stream_loop_manager": get_stream_loop_manager().get_stats(),
            }
            cloud_telemetry_status = await get_cloud_telemetry_status_summary()
            return JSONResponse(
                {
                    "telemetry_summary": telemetry_summary,
                    "telemetry_domains": telemetry_domains,
                    "recent_events": recent_events,
                    "llm_summary": llm_summary,
                "telemetry_enabled": collector.enabled,
                "cloud_telemetry_status": cloud_telemetry_status,
                    "runtime_health": runtime_health,
                }
            )

        @api_router.get("/events")
        async def get_events(
            domain: str | None = Query(default=None),
            limit: int = Query(default=100, ge=1, le=500),
        ) -> JSONResponse:
            collector = get_telemetry_collector()
            return JSONResponse(await collector.get_recent(domain=domain, limit=limit))

        @api_router.delete("/events")
        async def clear_events() -> JSONResponse:
            collector = get_telemetry_collector()
            await collector.clear()
            return JSONResponse({"ok": True})

        router.include_router(api_router, prefix="/api")
        return router


_dashboard: TelemetryDashboard | None = None


def get_telemetry_dashboard() -> TelemetryDashboard:
    """获取全局 TelemetryDashboard 单例。"""
    global _dashboard
    if _dashboard is None:
        _dashboard = TelemetryDashboard()
    return _dashboard