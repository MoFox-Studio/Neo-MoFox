import asyncio
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_adapter_manager_subprocess_mode_is_rejected(monkeypatch):
    """子进程适配器支持已移除：声明 run_in_subprocess=True 的适配器应被拒绝启动。"""

    from src.core.managers.adapter_manager import AdapterManager

    class DummyAdapter:
        run_in_subprocess = True
        platform = "dummy"

    class DummyRegistry:
        def get(self, sig):
            return DummyAdapter

    # 替换 registry（应在 run_in_subprocess 检测处直接返回，无需 state_manager 参与）
    monkeypatch.setattr("src.core.managers.adapter_manager.get_global_registry", lambda: DummyRegistry())

    manager = AdapterManager()
    ok = await manager.start_adapter("p:adapter:x")
    assert ok is False

    adapter = manager.get_adapter("p:adapter:x")
    assert adapter is None


@pytest.mark.asyncio
async def test_on_all_plugins_loaded_schedules_background_start(monkeypatch):
    """所有适配器都应立即调度到后台启动并返回。"""

    from src.core.managers.adapter_manager import on_all_plugins_loaded
    from src.kernel.event import EventDecision

    class DummyAdapter:
        run_in_subprocess = False

    class DummyRegistry:
        def get_by_type(self, _component_type):
            return {"onebot:adapter:onebot_adapter": DummyAdapter}

    class DummyTaskManager:
        def __init__(self) -> None:
            self.tasks = []

        def create_task(self, coro, name=None, daemon=False, timeout=None, group_name=None, metadata=None):
            task = asyncio.create_task(coro, name=name)
            self.tasks.append(task)
            return type("TaskInfo", (), {"task": task, "task_id": "task-id"})()

    task_manager = DummyTaskManager()
    start_adapter = AsyncMock(return_value=True)
    mock_manager = SimpleNamespace(start_adapter=start_adapter)

    monkeypatch.setattr("src.core.managers.adapter_manager.get_global_registry", lambda: DummyRegistry())
    monkeypatch.setattr("src.core.managers.adapter_manager.get_task_manager", lambda: task_manager)
    monkeypatch.setattr("src.core.managers.adapter_manager.get_adapter_manager", lambda: mock_manager)

    decision, out = await on_all_plugins_loaded("", {})

    assert decision == EventDecision.SUCCESS
    assert out == {}
    assert len(task_manager.tasks) == 1

    await task_manager.tasks[0]

    start_adapter.assert_awaited_once_with("onebot:adapter:onebot_adapter")
