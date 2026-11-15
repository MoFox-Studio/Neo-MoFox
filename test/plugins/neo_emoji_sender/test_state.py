"""``state.json`` 状态存储测试。"""

from __future__ import annotations

import json
from pathlib import Path

from plugins.neo_emoji_sender.manager.state import IndexMeta, StateStore


async def test_read_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """文件不存在时应返回空状态而不是抛错。"""
    store = StateStore(tmp_path / "state.json")

    assert await store.read() == {}
    assert await store.get_index_meta() is None
    assert await store.get_migration() is None


async def test_index_and_migration_groups_do_not_overwrite_each_other(
    tmp_path: Path,
) -> None:
    """两个分组互不影响，各自只替换自己那一块。"""
    store = StateStore(tmp_path / "state.json")

    await store.set_index_meta(
        IndexMeta(embedding_model="model-a", collection="neo_emoji_sender")
    )
    await store.set_migration(
        {
            "marker": "legacy_emoji_sender_v1",
            "migrated": 3,
            "skipped": 1,
            "finished_at": 1.0,
        }
    )

    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(data) == {"index", "migration"}
    assert data["index"]["embedding_model"] == "model-a"

    # 再写 index 不能冲掉 migration
    await store.set_index_meta(
        IndexMeta(embedding_model="model-b", collection="neo_emoji_sender")
    )
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert data["index"]["embedding_model"] == "model-b"
    assert data["migration"]["migrated"] == 3


async def test_corrupted_file_is_treated_as_empty_state(tmp_path: Path) -> None:
    """JSON 损坏时按空状态处理，让调用方走首次运行逻辑。"""
    path = tmp_path / "state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = StateStore(path)

    assert await store.read() == {}
    assert await store.get_index_meta() is None


async def test_non_dict_json_is_treated_as_empty_state(tmp_path: Path) -> None:
    """顶层不是对象的 JSON 同样按空状态处理。"""
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = StateStore(path)

    assert await store.read() == {}


async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    """写入时应自动创建父目录。"""
    store = StateStore(tmp_path / "nested" / "deeper" / "state.json")

    await store.set_migration({"marker": "m"})

    assert (tmp_path / "nested" / "deeper" / "state.json").is_file()


async def test_path_property_exposes_target_file(tmp_path: Path) -> None:
    """路径属性应原样暴露目标文件。"""
    target = tmp_path / "state.json"
    store = StateStore(target)

    assert store.path == target
