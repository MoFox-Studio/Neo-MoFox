"""验证 TOML 回写保留用户字符串，且连续加载后仍可解析。"""

from pathlib import Path
import tomllib

import pytest

from src.core.config.model_config import ModelConfig
from src.kernel.config.core import _toml_format_value


def test_escaped_gemini_alias_survives_auto_update(tmp_path: Path) -> None:
    """首次加载识别带回车的别名，回写保留原值并可再次加载。"""
    path = tmp_path / "model.toml"
    # 显式提供可选数值，避免无关的 None 占位符影响第二次加载。
    task_sections = "\n".join(
        f"[model_tasks.{name}]\nembedding_dimension = 8\n"
        for name in ModelConfig.ModelTasksConfig.model_fields
    )
    path.write_text(
        '[[api_providers]]\nname = "fixture"\nclient_type = "google\\r"\n'
        '[[models]]\nname = "fixture-model"\nmodel_identifier = "fixture-id"\n'
        'api_provider = "fixture"\ncache_hit_price_in = 0.0\n' + task_sections,
        encoding="utf-8",
    )
    original = path.read_bytes()

    assert ModelConfig.load(path, auto_update=True).api_providers[0].client_type == "gemini"
    assert path.read_bytes() != original
    with path.open("rb") as saved:
        assert tomllib.load(saved)["api_providers"][0]["client_type"] == "google\r"
    assert ModelConfig.load(path, auto_update=True).api_providers[0].client_type == "gemini"


@pytest.mark.parametrize("multiline", [False, True], ids=["single-line", "multiline"])
def test_control_characters_round_trip(multiline: bool) -> None:
    """基本字符串和多行字符串都应保留控制字符、引号、反斜杠与中文。"""
    controls = "".join(chr(code) for code in range(32) if code != 10) + "\x7f"
    value = '中文\\"""' + controls + ('\r\n下一行' if multiline else '')
    rendered = _toml_format_value(value)

    assert tomllib.loads(f"value = {rendered}")["value"] == value
    assert "\t" not in rendered
