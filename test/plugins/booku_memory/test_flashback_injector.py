"""Booku Memory 闪回机制测试。

覆盖：
- 纯函数：概率裁剪、层级选择、按权重抽取
- 事件处理器：仅对 default_chatter_user_prompt 生效、按概率注入 extra
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from plugins.booku_memory.flashback import (
    activation_weight,
    clamp_probability,
    pick_layer,
    should_trigger,
    weighted_choice,
)
from plugins.booku_memory.plugin import BookuMemoryAgentPlugin
from plugins.booku_memory.service.metadata_repository import BookuMemoryMetadataRepository
from src.core.prompt import get_system_reminder_store, reset_system_reminder_store


def test_clamp_probability() -> None:
    assert clamp_probability(-1.0) == 0.0
    assert clamp_probability(0.0) == 0.0
    assert clamp_probability(0.3) == 0.3
    assert clamp_probability(1.0) == 1.0
    assert clamp_probability(99.0) == 1.0


def test_should_trigger() -> None:
    assert should_trigger(trigger_probability=0.0, u=0.0) is False
    assert should_trigger(trigger_probability=1.0, u=0.999) is True
    assert should_trigger(trigger_probability=0.5, u=0.49) is True
    assert should_trigger(trigger_probability=0.5, u=0.5) is False


def test_pick_layer() -> None:
    assert pick_layer(archived_probability=1.0, u=0.999) == "archived"
    assert pick_layer(archived_probability=0.0, u=0.0) == "emergent"
    assert pick_layer(archived_probability=0.8, u=0.79) == "archived"
    assert pick_layer(archived_probability=0.8, u=0.81) == "emergent"


def test_weighted_choice_prefers_by_threshold() -> None:
    items = ["a", "b", "c"]
    weights = [3.0, 1.0, 6.0]  # total=10
    assert weighted_choice(items, weights, u=0.0) == "a"  # threshold=0
    assert weighted_choice(items, weights, u=0.35) == "b"  # threshold=3.5 falls into b
    assert weighted_choice(items, weights, u=0.95) == "c"  # threshold=9.5 falls into c


def test_activation_weight_inverse() -> None:
    w0 = activation_weight(activation_count=0, exponent=1.0)
    w3 = activation_weight(activation_count=3, exponent=1.0)
    assert w0 > w3


@pytest.mark.asyncio
async def test_flashback_injector_injects_into_extra(tmp_path: Path) -> None:
    from plugins.booku_memory.config import BookuMemoryConfig
    from plugins.booku_memory.event_handler import MemoryFlashbackInjector
    from src.kernel.event import EventDecision

    db_path = str(tmp_path / "flashback.db")
    repo = BookuMemoryMetadataRepository(db_path)
    await repo.initialize()

    await repo.upsert_record(
        memory_id="m_low",
        title="t",
        folder_id="folder_a",
        bucket="archived",
        content="低激活记忆",
        source="unit_test",
        novelty_energy=0.1,
        tags=[],
        core_tags=[],
        diffusion_tags=[],
        opposing_tags=[],
    )
    await repo.upsert_record(
        memory_id="m_high",
        title="t",
        folder_id="folder_a",
        bucket="archived",
        content="高激活记忆",
        source="unit_test",
        novelty_energy=0.1,
        tags=[],
        core_tags=[],
        diffusion_tags=[],
        opposing_tags=[],
    )
    # 提升 m_high 的激活次数，使其权重更低
    for _ in range(5):
        await repo.update_activated("m_high")

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = db_path
    cfg.flashback.enabled = True
    cfg.flashback.trigger_probability = 1.0
    cfg.flashback.archived_probability = 1.0
    cfg.flashback.folder_id = "folder_a"
    cfg.flashback.candidate_limit = 50
    cfg.flashback.activation_weight_exponent = 1.0

    class _DummyPlugin:
        config = cfg

    handler = MemoryFlashbackInjector(plugin=_DummyPlugin())

    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": ""},
        "policies": {},
        "strict": False,
    }

    # 让随机序列可控：
    # - 触发判定：u=0.0（必触发）
    # - 选层：u=0.0（必归档）
    # - 抽取：u=0.01（落在第一个候选的区间，期望偏向低激活）
    import plugins.booku_memory.event_handler as eh

    seq = iter([0.0, 0.0, 0.01])

    def _rand() -> float:
        return next(seq)

    old = eh.random.random
    eh.random.random = _rand
    try:
        decision, out = await handler.execute("on_prompt_build", params)
    finally:
        eh.random.random = old
        await repo.close()
        repo_from_handler = cast(
            BookuMemoryMetadataRepository | None,
            getattr(handler, "_repo", None),
        )
        if repo_from_handler is not None:
            await repo_from_handler.close()

    assert decision is EventDecision.SUCCESS
    extra = out["values"]["extra"]
    assert "## 记忆闪回" in extra
    assert "就在刚才" in extra
    # 至少应注入某条记忆内容
    assert ("低激活记忆" in extra) or ("高激活记忆" in extra)


@pytest.mark.asyncio
async def test_flashback_injector_skips_other_templates() -> None:
    from plugins.booku_memory.config import BookuMemoryConfig
    from plugins.booku_memory.event_handler import MemoryFlashbackInjector
    from src.kernel.event import EventDecision

    cfg = BookuMemoryConfig()
    cfg.flashback.enabled = True
    cfg.flashback.trigger_probability = 1.0

    class _DummyPlugin:
        config = cfg

    handler = MemoryFlashbackInjector(plugin=_DummyPlugin())
    params: dict[str, Any] = {
        "name": "other_prompt",
        "template": "{extra}",
        "values": {"extra": "keep"},
        "policies": {},
        "strict": False,
    }

    decision, out = await handler.execute("on_prompt_build", params)
    assert decision is EventDecision.SUCCESS
    assert out["values"]["extra"] == "keep"


@pytest.mark.asyncio
async def test_flashback_injector_dedup_in_cooldown_window(tmp_path: Path) -> None:
    """同一条记忆在冷却期内不会重复闪回。"""

    from plugins.booku_memory.config import BookuMemoryConfig
    from plugins.booku_memory.event_handler import MemoryFlashbackInjector
    from src.kernel.event import EventDecision

    db_path = str(tmp_path / "flashback_cooldown.db")
    repo = BookuMemoryMetadataRepository(db_path)
    await repo.initialize()

    await repo.upsert_record(
        memory_id="m_only",
        title="t",
        folder_id="folder_a",
        bucket="archived",
        content="唯一候选记忆",
        source="unit_test",
        novelty_energy=0.1,
        tags=[],
        core_tags=[],
        diffusion_tags=[],
        opposing_tags=[],
    )

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = db_path
    cfg.flashback.enabled = True
    cfg.flashback.trigger_probability = 1.0
    cfg.flashback.archived_probability = 1.0
    cfg.flashback.folder_id = "folder_a"
    cfg.flashback.candidate_limit = 50
    cfg.flashback.activation_weight_exponent = 1.0
    cfg.flashback.cooldown_seconds = 60

    class _DummyPlugin:
        config = cfg

    handler = MemoryFlashbackInjector(plugin=_DummyPlugin())

    params: dict[str, Any] = {
        "name": "default_chatter_user_prompt",
        "template": "{extra}",
        "values": {"extra": ""},
        "policies": {},
        "strict": False,
    }

    import plugins.booku_memory.event_handler as eh

    rand_seq = iter([0.0, 0.0, 0.01, 0.0, 0.0, 0.01])

    def _rand() -> float:
        return next(rand_seq)

    time_values = [1000.0, 1030.0]

    def _time() -> float:
        if time_values:
            return time_values.pop(0)
        return 1030.0

    old_rand = eh.random.random
    old_time = eh.time.time
    eh.random.random = _rand
    eh.time.time = _time
    try:
        decision1, out1 = await handler.execute("on_prompt_build", params)
        extra1 = out1["values"]["extra"]
        decision2, out2 = await handler.execute("on_prompt_build", params)
        extra2 = out2["values"]["extra"]
    finally:
        eh.random.random = old_rand
        eh.time.time = old_time
        await repo.close()
        repo_from_handler = cast(
            BookuMemoryMetadataRepository | None,
            getattr(handler, "_repo", None),
        )
        if repo_from_handler is not None:
            await repo_from_handler.close()

    assert decision1 is EventDecision.SUCCESS
    assert "## 记忆闪回" in extra1
    assert "唯一候选记忆" in extra1

    assert decision2 is EventDecision.SUCCESS
    assert extra2 == extra1


@pytest.mark.asyncio
async def test_sync_booku_memory_actor_reminder_writes_actor_reminder(tmp_path: Path) -> None:
    from plugins.booku_memory.config import BookuMemoryConfig
    from plugins.booku_memory.service import sync_booku_memory_actor_reminder

    db_path = str(tmp_path / "system_inherent.db")
    repo = BookuMemoryMetadataRepository(db_path)
    await repo.initialize()
    reset_system_reminder_store()

    await repo.upsert_record(
        memory_id="inherent_1",
        title="固有记忆",
        folder_id="global",
        bucket="inherent",
        content="这是固有记忆内容",
        source="unit_test",
        novelty_energy=0.1,
        tags=[],
        core_tags=[],
        diffusion_tags=[],
        opposing_tags=[],
    )

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = db_path
    cfg.plugin.inject_system_prompt = True

    class _DummyPlugin:
        config = cfg

    try:
        reminder = await sync_booku_memory_actor_reminder(_DummyPlugin())
    finally:
        await repo.close()

    stored = get_system_reminder_store().get("actor", names=["booku_memory"])
    assert "## 记忆引导语" in reminder
    assert "## 固有记忆" in reminder
    assert "这是固有记忆内容" in reminder
    assert stored == "[booku_memory]\n" + reminder


@pytest.mark.asyncio
async def test_sync_booku_memory_actor_reminder_clears_when_disabled(tmp_path: Path) -> None:
    from plugins.booku_memory.config import BookuMemoryConfig
    from plugins.booku_memory.service import sync_booku_memory_actor_reminder

    reset_system_reminder_store()
    get_system_reminder_store().set("actor", name="booku_memory", content="old")

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "disabled.db")
    cfg.plugin.inject_system_prompt = False

    class _DummyPlugin:
        config = cfg

    reminder = await sync_booku_memory_actor_reminder(_DummyPlugin())

    assert reminder == ""
    assert get_system_reminder_store().get("actor", names=["booku_memory"]) == ""


@pytest.mark.asyncio
async def test_booku_memory_plugin_load_and_unload_manage_actor_reminder(tmp_path: Path) -> None:
    from plugins.booku_memory.config import BookuMemoryConfig

    db_path = str(tmp_path / "plugin_lifecycle.db")
    repo = BookuMemoryMetadataRepository(db_path)
    await repo.initialize()
    reset_system_reminder_store()

    await repo.upsert_record(
        memory_id="inherent_2",
        title="固有记忆",
        folder_id="global",
        bucket="inherent",
        content="插件生命周期固有记忆",
        source="unit_test",
        novelty_energy=0.1,
        tags=[],
        core_tags=[],
        diffusion_tags=[],
        opposing_tags=[],
    )

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = db_path
    cfg.plugin.inject_system_prompt = True

    plugin = BookuMemoryAgentPlugin(config=cfg)

    try:
        await plugin.on_plugin_loaded()
        stored = get_system_reminder_store().get("actor", names=["booku_memory"])
        assert "## 记忆引导语" in stored
        assert "插件生命周期固有记忆" in stored

        await plugin.on_plugin_unloaded()
        assert get_system_reminder_store().get("actor", names=["booku_memory"]) == ""
    finally:
        await repo.close()
