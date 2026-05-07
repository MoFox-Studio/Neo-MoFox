"""Tests for booku_memory memory_command behavior.

此文件沿用原文件名，当前覆盖 memory_command 的关键语义：
- search/read/create/update/delete 的分发
- && 串联执行与失败短路
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from plugins.booku_memory.agent.tools import BookuMemoryCommandTool


@dataclass
class _DummyPlugin:
    """最小插件桩对象。"""

    config: Any = None


class _FakeService:
    """用于替换真实服务，记录调用并返回固定结果。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search_memory_entries(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search", kwargs))
        return {"action": "search_memory_entries", "total": 1, "items": [{"id": "m1", "title": "t1", "metadata": {}}]}

    async def read_full_content(self, *, memory_ids: list[str]) -> dict[str, Any]:
        self.calls.append(("read", {"memory_ids": memory_ids}))
        return {"action": "read_full_content", "requested": len(memory_ids), "total": len(memory_ids), "items": []}

    async def create_memory(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create", kwargs))
        return {"action": "create_memory", "mode": "created", "total": 1, "items": [{"id": "m2"}]}

    async def update_memory_by_id(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("update", kwargs))
        return {"action": "update_memory_by_id", "updated": 1, "items": [{"id": kwargs.get("memory_id", "")}]}

    async def delete_memories(self, *, memory_ids: list[str], hard: bool = False) -> dict[str, Any]:
        self.calls.append(("delete", {"memory_ids": memory_ids, "hard": hard}))
        return {"action": "delete_memories", "mode": "hard" if hard else "soft", "deleted": len(memory_ids)}


@pytest.mark.asyncio
async def test_memory_command_dispatch_and_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_command 应支持多命令串联并保持顺序执行。"""

    fake_service = _FakeService()
    monkeypatch.setattr("plugins.booku_memory.agent.tools._service", lambda _plugin: fake_service)

    tool = BookuMemoryCommandTool(plugin=cast(Any, _DummyPlugin()))
    ok, payload = await tool.execute(
        command=(
            "search -type person -person_id qq:10001 -topn 3 "
            "&& read -ids m1,m2 "
            "&& delete -id m2 -hard true"
        )
    )

    assert ok is True
    assert isinstance(payload, dict)
    assert payload.get("ok") is True
    assert payload.get("executed") == 3

    assert [name for name, _ in fake_service.calls] == ["search", "read", "delete"]
    assert fake_service.calls[0][1]["memory_type"] == "person"
    assert fake_service.calls[0][1]["person_id"] == "qq:10001"


@pytest.mark.asyncio
async def test_memory_command_folder_option_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """folder_id/folder 参数应被工具层忽略，不影响下游调用。"""

    fake_service = _FakeService()
    monkeypatch.setattr("plugins.booku_memory.agent.tools._service", lambda _plugin: fake_service)

    tool = BookuMemoryCommandTool(plugin=cast(Any, _DummyPlugin()))
    ok, payload = await tool.execute(
        command=(
            "search -query 复盘 -folder_id events "
            "&& create -type event -title 年会 -content 内容 -folder archive"
        )
    )

    assert ok is True
    assert isinstance(payload, dict)
    assert payload.get("ok") is True
    assert [name for name, _ in fake_service.calls] == ["search", "create"]
    assert "folder_id" not in fake_service.calls[0][1]
    assert "folder_id" not in fake_service.calls[1][1]


@pytest.mark.asyncio
async def test_memory_command_requires_person_id_for_person_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """人物记忆创建必须携带 person_id。"""

    fake_service = _FakeService()
    monkeypatch.setattr("plugins.booku_memory.agent.tools._service", lambda _plugin: fake_service)

    tool = BookuMemoryCommandTool(plugin=cast(Any, _DummyPlugin()))
    ok, payload = await tool.execute(
        command="create -type person -title 张三 -content 测试人物"
    )

    assert ok is False
    assert isinstance(payload, dict)
    assert payload.get("ok") is False
    first = payload.get("results", [])[0]
    assert "person_id" in str(first.get("error", ""))


@pytest.mark.asyncio
async def test_memory_command_stops_on_first_failed_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令链出现失败时应短路，不继续执行后续命令。"""

    fake_service = _FakeService()
    monkeypatch.setattr("plugins.booku_memory.agent.tools._service", lambda _plugin: fake_service)

    tool = BookuMemoryCommandTool(plugin=cast(Any, _DummyPlugin()))
    ok, payload = await tool.execute(
        command="read -ids m1 && unknown -x 1 && delete -id m1"
    )

    assert ok is False
    assert isinstance(payload, dict)
    assert payload.get("executed") == 2
    # 只执行了 read，第二段报错后 short-circuit
    assert [name for name, _ in fake_service.calls] == ["read"]
