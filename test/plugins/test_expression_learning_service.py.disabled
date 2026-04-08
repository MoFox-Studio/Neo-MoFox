"""expression_learning service 测试。"""

from __future__ import annotations

import pytest

from plugins.expression_learning.service import get_expression_learning_service
from src.core.components.base.plugin import BasePlugin


class _Plugin(BasePlugin):
    """最小插件桩。"""

    plugin_name = "expression_learning"

    def get_components(self) -> list[type]:
        return []


@pytest.mark.asyncio
async def test_create_record_if_distinct_skips_similar_regex(tmp_path) -> None:
    """规则相似时不应重复写入。"""

    from plugins.expression_learning.config import ExpressionLearningConfig

    plugin = _Plugin()
    config = ExpressionLearningConfig()
    config.storage.db_path = str(tmp_path / "expression_learning.db")
    plugin.config = config

    service = get_expression_learning_service(plugin)
    await service.initialize()

    created = await service.create_record_if_distinct(
        scene_types=["吐槽"],
        regex_patterns=[r"你这也太离谱了吧"],
        description="吐槽离谱行为",
        source_context="A: 你这也太离谱了吧",
    )
    duplicate = await service.create_record_if_distinct(
        scene_types=["吐槽"],
        regex_patterns=[r"你这也太离谱了吧\s*"],
        description="重复吐槽",
        source_context="B: 你这也太离谱了吧",
    )

    assert created is not None
    assert duplicate is None


@pytest.mark.asyncio
async def test_render_prompt_suggestions_returns_soft_list(tmp_path) -> None:
    """应渲染成软建议清单。"""

    from plugins.expression_learning.config import ExpressionLearningConfig

    plugin = _Plugin()
    config = ExpressionLearningConfig()
    config.storage.db_path = str(tmp_path / "expression_learning_prompt.db")
    plugin.config = config

    service = get_expression_learning_service(plugin)
    await service.initialize()
    record = await service.create_record(
        scene_types=["附和"],
        regex_patterns=[r"确实", r"有一说一"],
        description="适合轻度附和时使用",
        source_context="A: 这波确实有点东西",
    )

    rendered = service.render_prompt_suggestions([record], "可参考的表达方式")

    assert "## 可参考的表达方式" in rendered
    assert f"[{record.record_id}]" in rendered
    assert "适合轻度附和时使用" in rendered
