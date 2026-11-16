"""PR181：自动更新必须接受模型已支持的 Gemini 别名。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
import tomllib

import pytest
from pydantic import field_validator, model_validator

from src.core.config import model_config as model_config_module
from src.core.config.model_config import APIProviderSection, ModelConfig
from src.kernel.config import ConfigBase, Field, SectionBase


@pytest.mark.parametrize(
    "alias",
    ["Gemini", " GOOGLE ", "AIOHTTP_GEMINI", " GeMiNi_V2 ", "google", "gemini_v2"],
)
@pytest.mark.parametrize("entrypoint", ["load", "init"])
def test_auto_update_accepts_existing_gemini_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str, entrypoint: str
) -> None:
    """直接建模、普通加载与自动更新入口必须接受同一组已有别名。"""
    path = tmp_path / "provider-alias.toml"
    # 显式提供无关可选数值，避免混入既有 None 序列化为空字符串的问题。
    task_sections = "\n".join(
        f"[model_tasks.{name}]\nembedding_dimension = 8\n"
        for name in ModelConfig.ModelTasksConfig.model_fields
    )
    path.write_text(
        f'[[api_providers]]\nname = "fixture"\nclient_type = "{alias}"\n'
        '\n[[models]]\nname = "fixture-model"\nmodel_identifier = "fixture-id"\n'
        'api_provider = "fixture"\ncache_hit_price_in = 0.0\n'
        + task_sections,
        encoding="utf-8",
    )
    assert APIProviderSection(client_type=alias).client_type == "gemini"
    assert ModelConfig.load(path, auto_update=False).api_providers[0].client_type == "gemini"
    monkeypatch.setattr(model_config_module, "_global_model_config", None)

    if entrypoint == "init":
        config = model_config_module.init_model_config(str(path))
    else:
        config = ModelConfig.load(path, auto_update=True)

    assert config.api_providers[0].client_type == "gemini"
    assert ModelConfig.load(path, auto_update=False).api_providers[0].client_type == "gemini"
    saved = tomllib.loads(path.read_text(encoding="utf-8"))
    assert saved["api_providers"][0]["client_type"] == alias
    original = path.read_bytes()
    assert ModelConfig.load(path, auto_update=True).api_providers[0].client_type == "gemini"
    assert path.read_bytes() == original


@pytest.mark.parametrize("value", ["gemni", "unknown", "vertex_ai", "OpenAI", " ANTHROPIC ", 123])
@pytest.mark.parametrize("entrypoint", ["load", "init"])
def test_invalid_or_unpromised_alias_does_not_rewrite_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | int, entrypoint: str
) -> None:
    """非法值不回退、不改写；不扩展旧枚举大小写兼容范围。"""
    path = tmp_path / "invalid-provider.toml"
    literal = f'"{value}"' if isinstance(value, str) else str(value)
    source = f"[[api_providers]]\nclient_type = {literal}\n"
    path.write_text(source, encoding="utf-8")
    original = path.read_bytes()

    monkeypatch.setattr(model_config_module, "_global_model_config", None)
    with pytest.raises(ValueError, match="client_type"):
        if entrypoint == "init":
            model_config_module.init_model_config(str(path))
        else:
            ModelConfig.load(path, auto_update=True)

    assert path.read_bytes() == original
    assert model_config_module._global_model_config is None


class _StrictSection(SectionBase):
    """与供应商无关的严格枚举和普通数值约束。"""

    mode: Literal["fast", "safe"] = Field(default="safe", strict_choices=True)
    strategy: Literal["direct", "queued"] = Field(default="direct", strict_choices=True)
    limit: int = Field(default=1, ge=1)

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: Any) -> Any:
        """模拟由配置模型自行声明的别名，不要求内核了解枚举语义。"""
        return "fast" if value == " FAST " else value


class _StrictConfig(ConfigBase):
    """用于验证通用严格字段的合成配置。"""

    policy: _StrictSection = Field(default_factory=_StrictSection)


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ('mode = "invalid"', "mode"),
        ('mode = 123', "mode"),
        ('mode = " FAST "\nstrategy = "invalid"', "strategy"),
        ('mode = " FAST "\nlimit = 0', "limit"),
    ],
)
def test_full_model_rejects_invalid_fields_before_rewrite(
    tmp_path: Path, fields: str, error_field: str
) -> None:
    """其它严格字段和完整模型约束失败时，同样不得回退或改写文件。"""
    path = tmp_path / "strict-invalid.toml"
    path.write_text(f"[policy]\n{fields}\n", encoding="utf-8")
    original = path.read_bytes()

    with pytest.raises(ValueError, match=error_field):
        _StrictConfig.load(path, auto_update=True)

    assert path.read_bytes() == original


def test_generic_before_validator_keeps_original_toml_value(tmp_path: Path) -> None:
    """非供应商字段也能在运行时接受别名，文件仍保留原值。"""
    path = tmp_path / "strict-alias.toml"
    path.write_text('[policy]\nmode = " FAST "\n', encoding="utf-8")

    config = _StrictConfig.load(path, auto_update=True)

    assert config.policy.mode == "fast"
    assert tomllib.loads(path.read_text(encoding="utf-8"))["policy"]["mode"] == " FAST "


def test_mutating_model_validator_does_not_change_rendered_value(tmp_path: Path) -> None:
    """模型前置校验器即便原地归一化输入，写回也必须使用原始值。"""
    class MutatingConfig(_StrictConfig):
        """通过完整模型的 before-validator 归一化别名。"""

        @model_validator(mode="before")
        @classmethod
        def normalize_policy(cls, data: Any) -> Any:
            """原地改写传入映射，验证写回不依赖已归一化的模型数据。"""
            if isinstance(data, dict) and data.get("policy", {}).get("mode") == "QUICK":
                data["policy"]["mode"] = "fast"
            return data

    path = tmp_path / "model-alias.toml"
    path.write_text('[policy]\nmode = "QUICK"\n', encoding="utf-8")

    config = MutatingConfig.load(path, auto_update=True)

    assert config.policy.mode == "fast"
    assert tomllib.loads(path.read_text(encoding="utf-8"))["policy"]["mode"] == "QUICK"


@pytest.mark.parametrize("auto_update", [False, True])
def test_failed_validation_does_not_create_missing_file(
    tmp_path: Path, auto_update: bool
) -> None:
    """缺失文件也要先完成模型校验，失败时不创建文件或父目录。"""
    class RejectedConfig(_StrictConfig):
        """始终拒绝加载的合成模型，用于检查文件创建顺序。"""

        @model_validator(mode="before")
        @classmethod
        def reject_config(cls, data: Any) -> Any:
            """在任何文件创建之前拒绝当前配置。"""
            raise ValueError("fixture validation failed")

    path = tmp_path / "missing" / "rejected.toml"

    with pytest.raises(ValueError, match="fixture validation failed"):
        RejectedConfig.load(path, auto_update=auto_update)

    assert not path.exists()
    assert not path.parent.exists()


@pytest.mark.parametrize("auto_update", [False, True])
def test_valid_missing_file_can_still_be_created(tmp_path: Path, auto_update: bool) -> None:
    """模型校验通过时仍支持加载并创建缺失文件。"""
    path = tmp_path / "missing" / "valid.toml"

    config = _StrictConfig.load(path, auto_update=auto_update)

    assert config.policy.mode == "safe"
    assert path.is_file()
    assert _StrictConfig.load(path).policy.mode == "safe"
