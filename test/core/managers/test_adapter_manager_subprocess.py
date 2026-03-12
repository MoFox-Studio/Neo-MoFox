import pytest


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
