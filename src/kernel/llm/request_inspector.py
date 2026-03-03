"""LLM 请求体调试检视器。

提供一个基于 FastAPI + SSE 的 Web 界面，实时记录并展示每次
OpenAI 兼容 API 的完整请求体，方便调试 payload 结构。

使用方式：
    # 在 HTTP 服务器启动后调用一次
    from src.kernel.llm.request_inspector import get_inspector
    get_inspector().mount(fastapi_app)

    # 在发起请求前调用
    from src.kernel.llm.request_inspector import capture
    capture("chat.completions.create", params)

WebUI 地址：http://<host>:<port>/_inspector/
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass
class CapturedRequest:
    """单条捕获记录。"""

    id: int
    ts: float                     # unix timestamp
    api_name: str                 # 如 "chat.completions.create"
    model: str                    # 从 params["model"] 摘出，方便列表展示
    params: dict[str, Any]        # 完整请求体

    def to_summary(self) -> dict[str, Any]:
        """返回列表展示用的摘要（不含完整 params）。"""
        msg_count = len(self.params.get("messages", []))
        tool_count = len(self.params.get("tools", []))
        return {
            "id": self.id,
            "ts": self.ts,
            "ts_str": time.strftime("%H:%M:%S", time.localtime(self.ts)),
            "api_name": self.api_name,
            "model": self.model,
            "msg_count": msg_count,
            "tool_count": tool_count,
        }

    def to_full(self) -> dict[str, Any]:
        """返回完整记录（含 params）。"""
        summary = self.to_summary()
        summary["params"] = self.params
        return summary


# --------------------------------------------------------------------------- #
# 单例检视器
# --------------------------------------------------------------------------- #

class RequestInspector:
    """LLM 请求体检视器，保存最近 N 条请求并通过 Web 界面展示。"""

    def __init__(self, max_records: int = 200) -> None:
        """初始化检视器。

        Args:
            max_records: 内存中最多保留的请求条数。
        """
        self._max_records = max_records
        self._records: deque[CapturedRequest] = deque(maxlen=max_records)
        self._counter: int = 0
        self._subscribers: list[asyncio.Queue[CapturedRequest | None]] = []
        self._mounted: bool = False

    # ------------------------------------------------------------------ #
    # 捕获
    # ------------------------------------------------------------------ #

    def capture(self, api_name: str, params: dict[str, Any]) -> None:
        """捕获一条请求体，存入内存并推送给所有 SSE 订阅者。

        Args:
            api_name: API 名称（如 "chat.completions.create"）。
            params: 完整请求参数 dict（会做深拷贝，避免被外部修改）。
        """
        self._counter += 1
        try:
            copied = json.loads(json.dumps(params, default=str))
        except Exception:
            copied = {"_raw": str(params)}

        model = str(copied.get("model", "—"))
        record = CapturedRequest(
            id=self._counter,
            ts=time.time(),
            api_name=api_name,
            model=model,
            params=copied,
        )
        self._records.append(record)

        # 广播给所有 SSE 客户端
        for q in list(self._subscribers):
            try:
                q.put_nowait(record)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------ #
    # FastAPI 挂载
    # ------------------------------------------------------------------ #

    def mount(self, app: Any, prefix: str = "/_inspector") -> None:
        """将 WebUI 路由挂载到 FastAPI 应用。

        Args:
            app: FastAPI 实例。
            prefix: 挂载路径前缀，默认 ``/_inspector``。
        """
        if self._mounted:
            return
        self._mounted = True
        router = self._build_router()
        app.include_router(router, prefix=prefix)

    def _build_router(self) -> APIRouter:
        """构建 FastAPI router，包含 WebUI、REST 与 SSE 端点。"""
        router = APIRouter()

        # ---- WebUI ----
        @router.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def webui() -> HTMLResponse:  # type: ignore[return-value]
            return HTMLResponse(_WEBUI_HTML)

        # ---- 获取全部摘要列表 ----
        @router.get("/api/requests")
        async def list_requests() -> JSONResponse:
            return JSONResponse([r.to_summary() for r in self._records])

        # ---- 获取单条完整记录 ----
        @router.get("/api/requests/{req_id}")
        async def get_request(req_id: int) -> JSONResponse:
            for r in reversed(self._records):
                if r.id == req_id:
                    return JSONResponse(r.to_full())
            return JSONResponse({"error": "not found"}, status_code=404)

        # ---- 清空记录 ----
        @router.delete("/api/requests")
        async def clear_requests() -> JSONResponse:
            self._records.clear()
            return JSONResponse({"ok": True})

        # ---- SSE 实时推送 ----
        @router.get("/api/stream", include_in_schema=False)
        async def sse_stream() -> StreamingResponse:
            queue: asyncio.Queue[CapturedRequest | None] = asyncio.Queue(maxsize=50)
            self._subscribers.append(queue)

            async def generate() -> AsyncIterator[str]:
                try:
                    # 先推送当前已有列表（摘要）
                    snapshot = [r.to_summary() for r in self._records]
                    yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
                    while True:
                        try:
                            record = await asyncio.wait_for(queue.get(), timeout=25)
                        except asyncio.TimeoutError:
                            yield ": heartbeat\n\n"
                            continue
                        if record is None:
                            break
                        yield f"event: new\ndata: {json.dumps(record.to_summary())}\n\n"
                finally:
                    try:
                        self._subscribers.remove(queue)
                    except ValueError:
                        pass

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        return router


# --------------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------------- #

_inspector: RequestInspector | None = None


def get_inspector() -> RequestInspector:
    """获取全局 RequestInspector 单例。

    Returns:
        RequestInspector 实例。
    """
    global _inspector
    if _inspector is None:
        _inspector = RequestInspector()
    return _inspector


def capture(api_name: str, params: dict[str, Any]) -> None:
    """捕获一条 OpenAI 请求体（快捷函数）。

    Args:
        api_name: API 名称。
        params: 请求体 dict。
    """
    get_inspector().capture(api_name, params)


# --------------------------------------------------------------------------- #
# 内嵌 WebUI HTML
# --------------------------------------------------------------------------- #

_WEBUI_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM 请求检视器</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0f1117; --panel: #1a1d27; --border: #2a2d3e;
    --accent: #7c6af7; --accent2: #56cfb2;
    --text: #cdd6f4; --muted: #6c7086; --red: #f38ba8;
    --yellow: #f9e2af; --green: #a6e3a1; --blue: #89dceb;
    --purple: #cba6f7; --orange: #fab387;
  }
  body { background: var(--bg); color: var(--text); font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 13px; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
  header { padding: 10px 18px; background: var(--panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
  header h1 { font-size: 15px; color: var(--accent); flex: 1; }
  .badge { background: var(--accent); color: #fff; border-radius: 10px; padding: 1px 8px; font-size: 11px; }
  .badge.red { background: var(--red); color: #000; }
  #status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
  #status-dot.live { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .toolbar { display: flex; gap: 8px; align-items: center; }
  button { background: var(--border); border: 1px solid var(--border); color: var(--text); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  button:hover { background: var(--accent); color: #fff; border-color: var(--accent); }
  button.danger:hover { background: var(--red); border-color: var(--red); color: #000; }
  input[type=text] { background: var(--border); border: 1px solid var(--border); color: var(--text); padding: 4px 10px; border-radius: 6px; font-size: 12px; width: 200px; outline: none; }
  input[type=text]:focus { border-color: var(--accent); }
  .main { display: flex; flex: 1; overflow: hidden; }
  /* ---- 左侧列表 ---- */
  .list-panel { width: 340px; flex-shrink: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
  .list-panel .list-scroll { flex: 1; overflow-y: auto; }
  .req-item { padding: 9px 14px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background .1s; }
  .req-item:hover { background: var(--border); }
  .req-item.active { background: color-mix(in srgb, var(--accent) 20%, transparent); border-left: 3px solid var(--accent); }
  .req-item .row1 { display: flex; justify-content: space-between; align-items: center; }
  .req-item .api { color: var(--accent2); font-size: 11px; }
  .req-item .ts { color: var(--muted); font-size: 11px; }
  .req-item .model { color: var(--yellow); font-size: 12px; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .req-item .meta { color: var(--muted); font-size: 11px; margin-top: 2px; }
  /* ---- 右侧详情 ---- */
  .detail-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .detail-toolbar { padding: 8px 14px; border-bottom: 1px solid var(--border); display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
  .detail-title { flex: 1; color: var(--muted); font-size: 12px; }
  .detail-body { flex: 1; overflow: auto; padding: 14px; }
  pre { white-space: pre-wrap; word-break: break-all; line-height: 1.55; }
  /* JSON 语法高亮 */
  .json-key { color: var(--blue); }
  .json-str { color: var(--green); }
  .json-num { color: var(--orange); }
  .json-bool { color: var(--purple); }
  .json-null { color: var(--muted); }
  .empty-tip { color: var(--muted); padding: 30px; text-align: center; }
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted); }
  .new-flash { animation: flash .5s ease; }
  @keyframes flash { 0%,100% { background: transparent; } 50% { background: color-mix(in srgb, var(--accent) 30%, transparent); } }
</style>
</head>
<body>
<header>
  <span id="status-dot"></span>
  <h1>🔍 LLM 请求检视器</h1>
  <span class="badge" id="total-badge">0</span>
  <div class="toolbar">
    <input type="text" id="filter-input" placeholder="过滤 model / api…">
    <button id="auto-scroll-btn" title="自动滚动到最新">↓ 跟随</button>
    <button id="clear-btn" class="danger">清空</button>
  </div>
</header>
<div class="main">
  <div class="list-panel">
    <div class="list-scroll" id="list-scroll">
      <div class="empty-tip" id="empty-tip">等待请求捕获…</div>
    </div>
  </div>
  <div class="detail-panel">
    <div class="detail-toolbar">
      <span class="detail-title" id="detail-title">选择左侧记录查看完整请求体</span>
      <button id="copy-btn">复制 JSON</button>
      <button id="raw-btn">原始 / 高亮</button>
    </div>
    <div class="detail-body" id="detail-body">
      <div class="empty-tip">← 点击左侧记录</div>
    </div>
  </div>
</div>
<script>
const listScroll = document.getElementById('list-scroll');
const emptyTip = document.getElementById('empty-tip');
const detailBody = document.getElementById('detail-body');
const detailTitle = document.getElementById('detail-title');
const totalBadge = document.getElementById('total-badge');
const filterInput = document.getElementById('filter-input');
const statusDot = document.getElementById('status-dot');

let requests = [];       // [{id, ts_str, api_name, model, msg_count, tool_count}]
let activeId = null;
let autoScroll = true;
let rawMode = false;
let fullCache = {};      // id -> full params

// ---- SSE ----
function connectSSE() {
  const es = new EventSource('/_inspector/api/stream');
  statusDot.classList.remove('live');
  es.addEventListener('snapshot', e => {
    statusDot.classList.add('live');
    requests = JSON.parse(e.data);
    renderList();
  });
  es.addEventListener('new', e => {
    const r = JSON.parse(e.data);
    requests.push(r);
    appendItem(r, true);
    totalBadge.textContent = requests.length;
    if (autoScroll) listScroll.scrollTop = listScroll.scrollHeight;
  });
  es.onerror = () => {
    statusDot.classList.remove('live');
    es.close();
    setTimeout(connectSSE, 3000);
  };
}
connectSSE();

// ---- 渲染列表 ----
function filterText() { return filterInput.value.trim().toLowerCase(); }
function matchFilter(r) {
  const f = filterText();
  if (!f) return true;
  return r.model.toLowerCase().includes(f) || r.api_name.toLowerCase().includes(f);
}
function renderList() {
  listScroll.innerHTML = '';
  const filtered = requests.filter(matchFilter);
  if (filtered.length === 0) {
    listScroll.appendChild(Object.assign(document.createElement('div'), { className: 'empty-tip', textContent: requests.length ? '无匹配记录' : '等待请求捕获…' }));
    totalBadge.textContent = requests.length;
    return;
  }
  filtered.forEach(r => appendItem(r, false));
  totalBadge.textContent = requests.length;
  if (autoScroll) listScroll.scrollTop = listScroll.scrollHeight;
}
function appendItem(r, flash) {
  if (!matchFilter(r)) return;
  emptyTip.remove && emptyTip.remove();
  const div = document.createElement('div');
  div.className = 'req-item' + (r.id === activeId ? ' active' : '') + (flash ? ' new-flash' : '');
  div.dataset.id = r.id;
  div.innerHTML = `<div class="row1"><span class="api">${escHtml(r.api_name)}</span><span class="ts">${escHtml(r.ts_str)}</span></div>
    <div class="model">${escHtml(r.model)}</div>
    <div class="meta">msgs: ${r.msg_count}  tools: ${r.tool_count}</div>`;
  div.addEventListener('click', () => selectItem(r.id));
  listScroll.appendChild(div);
}
filterInput.addEventListener('input', renderList);

// ---- 选择记录 ----
async function selectItem(id) {
  activeId = id;
  document.querySelectorAll('.req-item').forEach(el => el.classList.toggle('active', +el.dataset.id === id));
  if (fullCache[id]) { showDetail(id, fullCache[id]); return; }
  detailTitle.textContent = '加载中…';
  const res = await fetch(`/_inspector/api/requests/${id}`);
  const data = await res.json();
  fullCache[id] = data;
  showDetail(id, data);
}
function showDetail(id, data) {
  const r = requests.find(x => x.id === id);
  detailTitle.textContent = r ? `#${id}  ${r.api_name}  ${r.ts_str}` : `#${id}`;
  renderDetail(data.params);
}
function renderDetail(params) {
  detailBody.innerHTML = '';
  const pre = document.createElement('pre');
  const jsonStr = JSON.stringify(params, null, 2);
  pre.innerHTML = rawMode ? escHtml(jsonStr) : syntaxHighlight(jsonStr);
  detailBody.appendChild(pre);
}

// ---- 工具栏 ----
document.getElementById('auto-scroll-btn').addEventListener('click', function() {
  autoScroll = !autoScroll;
  this.textContent = autoScroll ? '↓ 跟随' : '⏸ 暂停';
  this.style.background = autoScroll ? '' : 'var(--accent)';
  this.style.color = autoScroll ? '' : '#fff';
});
document.getElementById('clear-btn').addEventListener('click', async () => {
  if (!confirm('确定清空所有记录？')) return;
  await fetch('/_inspector/api/requests', { method: 'DELETE' });
  requests = []; fullCache = {}; activeId = null;
  renderList();
  detailBody.innerHTML = '<div class="empty-tip">← 点击左侧记录</div>';
  detailTitle.textContent = '选择左侧记录查看完整请求体';
  totalBadge.textContent = '0';
});
document.getElementById('copy-btn').addEventListener('click', () => {
  if (activeId == null) return;
  const data = fullCache[activeId];
  if (!data) return;
  navigator.clipboard.writeText(JSON.stringify(data.params, null, 2)).then(() => alert('已复制到剪贴板'));
});
document.getElementById('raw-btn').addEventListener('click', function() {
  rawMode = !rawMode;
  this.textContent = rawMode ? '高亮' : '原始 / 高亮';
  if (activeId != null && fullCache[activeId]) renderDetail(fullCache[activeId].params);
});

// ---- 工具函数 ----
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function syntaxHighlight(json) {
  return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\\s*:)?|\\b(true|false|null)\\b|-?\\d+(?:\\.\\d*)?(?:[eE][+\\-]?\\d+)?)/g, match => {
    let cls = 'json-num';
    if (/^"/.test(match)) cls = /:$/.test(match) ? 'json-key' : 'json-str';
    else if (/true|false/.test(match)) cls = 'json-bool';
    else if (/null/.test(match)) cls = 'json-null';
    return `<span class="${cls}">${escHtml(match)}</span>`;
  });
}
</script>
</body>
</html>"""
