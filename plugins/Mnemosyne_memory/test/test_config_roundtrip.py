"""Exercise configuration generation, WebUI validation and legacy-file repair."""

from __future__ import annotations

import copy
import tomllib

import pytest
from pydantic import ValidationError

from src.kernel.config.core import _render_toml_with_signature

from ..config import MnemosyneMemoryConfig


DIARY_VALUES = {
    "database_path": "data/custom-diary/记忆.sqlite",
    "timezone": "UTC",
    "model_task": "diary-model",
    "episode_export_enabled": False,
    "max_concurrency": 2,
    "batch_messages": 43,
    "context_messages": 11,
    "retry_limit": 4,
}

LEGACY_TOML = '''[plugin]
enabled = false
[storage]
vnext_db_path = "data/custom-memory.db"
vector_db_path = "data/custom-vectors"
[vnext.feedforward]
max_memories = 6
[diary.group]
enabled = false
message_threshold = 89
[diary.private]
enabled = true
trigger_mode = "either"
message_threshold = 37
database_path = "data/custom-diary/记忆.sqlite"
timezone = "UTC"
model_task = "diary-model"
episode_export_enabled = false
max_concurrency = 2
batch_messages = 43
context_messages = 11
retry_limit = 4
'''


def expected_config() -> MnemosyneMemoryConfig:
    return MnemosyneMemoryConfig(
        plugin={"enabled": False},
        storage={"vnext_db_path": "data/custom-memory.db", "vector_db_path": "data/custom-vectors"},
        vnext={"feedforward": {"max_memories": 6}},
        diary={**DIARY_VALUES, "group": {"enabled": False, "message_threshold": 89},
               "private": {"enabled": True, "trigger_mode": "either", "message_threshold": 37}},
    )


def assert_diary_layout(raw: dict) -> None:
    assert {key: raw["diary"][key] for key in DIARY_VALUES} == DIARY_VALUES
    assert not set(DIARY_VALUES).intersection(raw["diary"]["private"])
    assert not set(DIARY_VALUES).intersection(raw["diary"]["group"])


def test_framework_render_preserves_parent_fields_and_custom_values():
    expected = expected_config()
    text = _render_toml_with_signature(MnemosyneMemoryConfig, expected.model_dump())
    raw = tomllib.loads(text)
    assert_diary_layout(raw)
    assert MnemosyneMemoryConfig.model_validate(raw).model_dump() == expected.model_dump()


def test_generated_default_file_is_valid_without_config_merging(tmp_path):
    path = tmp_path / "config.toml"
    MnemosyneMemoryConfig.generate_default(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "database_path" in raw["diary"]
    assert not set(DIARY_VALUES).intersection(raw["diary"]["private"])
    assert MnemosyneMemoryConfig.model_validate(raw).model_dump() == MnemosyneMemoryConfig().model_dump()


@pytest.mark.parametrize("method", ["from_dict", "model_validate"])
def test_webui_validation_recovers_known_misplaced_fields_without_mutating_input(method):
    raw = tomllib.loads(LEGACY_TOML)
    before = copy.deepcopy(raw)
    config = getattr(MnemosyneMemoryConfig, method)(raw)
    assert config.model_dump() == expected_config().model_dump()
    assert raw == before


def test_explicit_parent_value_takes_precedence_over_legacy_duplicate():
    raw = tomllib.loads(LEGACY_TOML)
    raw["diary"]["batch_messages"] = 71
    config = MnemosyneMemoryConfig.model_validate(raw)
    assert config.diary.batch_messages == 71
    assert config.diary.private.message_threshold == 37


@pytest.mark.parametrize("change,location", [
    ({"database_pth": "typo.db"}, ("diary", "private", "database_pth")),
    ({"max_concurrency": 0}, ("diary", "max_concurrency")),
])
def test_recovery_keeps_unknown_field_and_value_validation_strict(change, location):
    raw = tomllib.loads(LEGACY_TOML)
    raw["diary"]["private"].update(change)
    with pytest.raises(ValidationError) as error:
        MnemosyneMemoryConfig.model_validate(raw)
    assert location in {item["loc"] for item in error.value.errors()}


def test_readonly_load_recovers_layout_without_rewriting_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_TOML, encoding="utf-8")
    before = path.read_bytes()
    assert MnemosyneMemoryConfig.load(path, auto_update=False).model_dump() == expected_config().model_dump()
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.bak")) == []


def test_auto_update_repairs_existing_file_preserves_values_and_keeps_exact_backup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_TOML, encoding="utf-8")
    before = path.read_bytes()
    loaded = MnemosyneMemoryConfig.load(path, auto_update=True)
    assert loaded.model_dump() == expected_config().model_dump()
    repaired = path.read_bytes()
    assert_diary_layout(tomllib.loads(repaired.decode("utf-8")))
    backups = list(tmp_path.glob("config.toml.diary-layout-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert MnemosyneMemoryConfig.load(path, auto_update=False).model_dump() == loaded.model_dump()
    assert MnemosyneMemoryConfig.load(path, auto_update=True).model_dump() == loaded.model_dump()
    assert path.read_bytes() == repaired
    assert list(tmp_path.glob("config.toml.diary-layout-*.bak")) == backups


def test_invalid_legacy_value_is_not_silently_reset_or_written(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(LEGACY_TOML.replace("max_concurrency = 2", "max_concurrency = 0"), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        MnemosyneMemoryConfig.load(path, auto_update=True)
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.bak")) == []
