"""在独立 pytest 会话中验证 handoff fixture 的失败、取消和 setup 清理。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


PROBE = r'''"""在子 pytest 会话观察 handoff 夹具的资源与异常终态。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from src.core.components.types import EventType
from src.kernel.concurrency import TaskInfo
from src.kernel.llm import LLMPayload, LLMRequest, ROLE, Text
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.llm.policy.round_robin import RoundRobinPolicy
from test.kernel.llm import test_pr181_response_handoff as target
from test.kernel.llm.test_gemini_client import model
from test.kernel.llm.test_pr181_response_handoff import handoff_case, closing_case

SCENARIO = __SCENARIO__
STATE = Path(__STATE__)
REPORTS = []

def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """记录子用例各阶段的结果，不改变原始报告。"""
    REPORTS.append({"when": report.when, "failed": report.failed, "detail": str(report.longrepr)})

@pytest.fixture(autouse=True)
def monitor(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """包裹真实创建与关闭入口，在夹具结束后记录资源状态。"""
    managers, clients, pools, leases, sources, tasks, wires = [], [], [], [], [], [], []
    real_manager, real_adapter = target.TaskManager, target._adapter
    bus = target.get_event_bus()
    names = (EventType.AFTER_LLM_REQUEST, "on_llm_stream_finished")
    previous = {name: list(bus.get_subscribers(name)) for name in names}
    real_subscribe = bus.subscribe
    real_finish = target._finish_tasks

    def make_manager(*args: Any, **kwargs: Any) -> target.TaskManager:
        """透传构造参数，登记本探针拥有的管理器和 executor。"""
        if SCENARIO == "closing_setup_failure":
            raise RuntimeError("setup-manager-sentinel")
        manager = real_manager(*args, **kwargs)
        managers.append(manager)
        pools.append(manager._process_pool)
        original_create = manager.create_task
        original_shutdown = manager.shutdown_process_pool
        def create(*args: Any, **kwargs: Any) -> TaskInfo:
            """保留真实任务创建，只登记待检查的任务句柄。"""
            info = original_create(*args, **kwargs)
            tasks.append(info.task)
            return info
        def shutdown(*args: Any, **kwargs: Any) -> None:
            """完成真实池关闭后，按场景注入清理故障。"""
            original_shutdown(*args, **kwargs)
            if SCENARIO == "primary_and_cleanup":
                raise RuntimeError("cleanup-sentinel")
        monkeypatch.setattr(manager, "create_task", create)
        monkeypatch.setattr(manager, "shutdown_process_pool", shutdown)
        return manager

    def make_adapter(*args: Any, **kwargs: Any) -> target.GeminiChatClient:
        """登记真实离线客户端及其响应 body，不替换产品释放逻辑。"""
        wires.extend(args[0])
        client = real_adapter(*args, **kwargs)
        clients.append(client)
        original_close = client.aclose
        async def close() -> None:
            """等待真实资源关闭，保留此回调登记的观测信息。"""
            leases.extend(lease for pool in client._pools.values() for lease in pool.values())
            await original_close()
        monkeypatch.setattr(client, "aclose", close)
        return client

    def subscribe(
        name: str, handler: Callable[..., Any], *args: Any, **kwargs: Any,
    ) -> Any:
        """透传订阅，在登记前或生效后注入指定 setup 故障。"""
        if SCENARIO == "setup_failure" and name == "on_llm_stream_finished":
            raise RuntimeError("setup-sentinel")
        result = real_subscribe(name, handler, *args, **kwargs)
        if SCENARIO == "setup_after_failure" and name == "on_llm_stream_finished":
            raise RuntimeError("setup-sentinel")
        return result

    async def finish(manager: target.TaskManager, **kwargs: Any) -> None:
        """运行原任务收尾，并可在清理阶段注入新的取消。"""
        if SCENARIO == "cleanup_cancel":
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel, "teardown-sentinel")
        await real_finish(manager, **kwargs)

    monkeypatch.setattr(target, "TaskManager", make_manager)
    monkeypatch.setattr(target, "_adapter", make_adapter)
    monkeypatch.setattr(target, "_finish_tasks", finish)
    monkeypatch.setattr(bus, "subscribe", subscribe)
    original_source = target.Source
    def source(*args: Any, **kwargs: Any) -> target.Source:
        """登记真实源，覆盖管理器构造失败前的资源交接。"""
        value = original_source(*args, **kwargs)
        sources.append(value)
        if SCENARIO == "closing_setup_failure":
            original_close = value.aclose
            async def close() -> None:
                """检查关闭窗口已放行，再等待原 Source 完成关闭。"""
                assert value.close_release.is_set(), "关闭前须释放测试窗口"
                await original_close()
            monkeypatch.setattr(value, "aclose", close)
        return value
    monkeypatch.setattr(target, "Source", source)
    yield
    state = {
        "manager_count": len(managers),
        "pool_closed": all(manager._process_pool is None for manager in managers),
        "executor_shutdown": all(pool._shutdown_thread for pool in pools),
        "tasks_done": all(task is not None and task.done() for task in tasks),
        "no_active": all(not manager.get_active_tasks() for manager in managers),
        "http_closed": all(lease.active == 0 and lease.http.is_closed for lease in leases),
        "client_pools_empty": all(all(not pool for pool in client._pools.values()) for client in clients),
        "subscriptions_restored": all(list(bus.get_subscribers(name)) == previous[name] for name in names),
        "source_close_calls": [source.closed for source in sources],
        "wire_close_calls": [wire.closed for wire in wires],
        "wire_reads": [wire.reads for wire in wires],
    }
    STATE.write_text(json.dumps(state), encoding="utf-8")
    # 审计后仍兜住本探针新建的资源；此处不改变已记录的失败事实。
    for manager in managers:
        original = manager.__class__.shutdown_process_pool
        original(manager, wait=False)

@pytest.mark.parametrize("close_error", [False])
@pytest.mark.parametrize("managed", [__MANAGED__])
@pytest.mark.asyncio
async def test_case(
    __CASE_FIXTURE__: target.HandoffCase | target.ClosingCase,
    close_error: bool, managed: bool,
) -> None:
    """按场景触发正常退出、体失败或取消，交给原夹具执行清理。"""
    case = __CASE_FIXTURE__
    if SCENARIO.startswith("closing"):

        if SCENARIO == "closing_active":
            closing = case.manager.create_task(case.response.aclose(), name="probe-close").task
            await case.source.close_entered.wait()
            assert not closing.done()
        assert False, "primary-sentinel"
    if SCENARIO in {"active_failure", "body_cancel", "cleanup_cancel"}:
        entry = model()
        entry["max_retry"] = 0
        req = LLMRequest(model_set=[entry], clients=ModelClientRegistry(gemini=case.client),
                         policy=RoundRobinPolicy(), enable_metrics=False)
        req.context_manager = None
        req.payloads = [LLMPayload(ROLE.USER, Text("offline"))]
        case.manager.create_task(req.send(stream=True), name="probe-handoff")
        await case.entered.wait()
    if SCENARIO == "body_cancel":
        asyncio.current_task().cancel("body-sentinel")
        await asyncio.Event().wait()
    if SCENARIO not in {"success", "cleanup_cancel"}:
        assert False, "primary-sentinel"
'''


@pytest.mark.parametrize("scenario, managed", [
    ("success", True), ("early_failure", True), ("active_failure", True),
    ("body_cancel", True), ("setup_failure", True), ("setup_after_failure", True), ("primary_and_cleanup", True),
    ("cleanup_cancel", True), ("closing_unstarted", True), ("closing_unstarted", False),
    ("closing_active", True), ("closing_active", False), ("closing_setup_failure", True),
])
def test_fixture_teardown_covers_abnormal_exits(
    tmp_path: Path, scenario: str, managed: bool,
) -> None:
    """真实 pytest teardown 后检查自有池、任务、订阅和 HTTP 终态，不读取宿主资源。"""
    root = Path(__file__).resolve().parents[3]
    state_path = tmp_path / "state.json"
    probe = tmp_path / "test_probe.py"
    # pytest 必须从 conftest 加载报告 hook；测试函数及 fixture 在同一模块中注册。
    source = textwrap.dedent(PROBE).replace("__SCENARIO__", repr(scenario))
    source = source.replace("__STATE__", repr(str(state_path))).replace("__MANAGED__", repr(managed))
    source = source.replace("__CASE_FIXTURE__", "closing_case" if scenario.startswith("closing") else "handoff_case")
    probe.write_text(source, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        '"""记录 handoff 子会话的阶段报告。"""\n'
        "from __future__ import annotations\n\n"
        "import json\nfrom pathlib import Path\n\nimport pytest\n\n"
        "reports=[]\ndef pytest_runtest_logreport(report: pytest.TestReport) -> None:\n"
        '    """保留原始测试阶段及错误详情。"""\n'
        "    reports.append({'when':report.when,'failed':report.failed,'detail':str(report.longrepr)})\n"
        "def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:\n"
        '    """会话结束后保存报告，不更改退出状态。"""\n'
        "    Path(__file__).with_name('reports.json').write_text(json.dumps(reports),encoding='utf-8')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(root), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
        TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path),
    )
    run = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "pytest", str(probe), "-q", "-o", "addopts=",
         "--assert=plain", "-p", "pytest_asyncio.plugin", "-p", "pytest_mock", "-p", "no:cacheprovider"],
        cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=20, check=False,
    )
    assert run.returncode == (0 if scenario == "success" else 1), run.stdout + run.stderr
    assert state_path.is_file(), run.stdout + run.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["manager_count"] == (0 if scenario == "closing_setup_failure" else 1)
    for key in ("pool_closed", "executor_shutdown", "tasks_done", "no_active", "http_closed",
                "client_pools_empty", "subscriptions_restored"):
        assert state[key], (scenario, key, state)
    if scenario.startswith("closing"):
        assert state["source_close_calls"] == [1]
        assert state["wire_close_calls"] == []
    elif scenario in {"active_failure", "body_cancel", "cleanup_cancel"}:
        assert state["wire_reads"] == [1]
        assert state["wire_close_calls"] == [1]
    else:
        # 未发出请求的内存 wire 未取得 HTTP body，不制造额外关闭责任。
        assert state["wire_reads"] == [0]
        assert state["wire_close_calls"] == [0]
    reports = json.loads((tmp_path / "reports.json").read_text(encoding="utf-8"))
    failures = [report for report in reports if report["failed"]]
    if scenario == "primary_and_cleanup":
        assert any(report["when"] == "call" and "primary-sentinel" in report["detail"] for report in failures)
        assert any(report["when"] == "teardown" and "cleanup-sentinel" in report["detail"] for report in failures)
    elif scenario in {"setup_failure", "setup_after_failure"}:
        assert any("setup-sentinel" in report["detail"] for report in failures)
    elif scenario == "closing_setup_failure":
        assert any("setup-manager-sentinel" in report["detail"] for report in failures)
    elif scenario == "cleanup_cancel":
        assert any(report["when"] == "teardown" and "teardown-sentinel" in report["detail"] for report in failures)
    elif scenario != "success":
        assert all(report["when"] == "call" for report in failures)
