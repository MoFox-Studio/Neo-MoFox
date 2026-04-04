"""expression_learning 插件装配测试。"""

from __future__ import annotations

from plugins.expression_learning.config import ExpressionLearningConfig
from plugins.expression_learning.event_handler import (
    ExpressionMatchListener,
    ExpressionPromptSuggestionListener,
)
from plugins.expression_learning.plugin import ExpressionLearningPlugin
from plugins.expression_learning.service import ExpressionLearningService
from plugins.expression_learning.tools import (
    ExpressionStyleCreateTool,
    ExpressionStyleFeedbackTool,
    ExpressionStyleGetTool,
)


def test_plugin_components_in_normal_mode() -> None:
    """普通模式应暴露创建、查询、反馈和两个事件处理器。"""

    plugin = ExpressionLearningPlugin()
    plugin.config = ExpressionLearningConfig()
    components = plugin.get_components()

    assert components == [
        ExpressionLearningService,
        ExpressionStyleCreateTool,
        ExpressionStyleGetTool,
        ExpressionStyleFeedbackTool,
        ExpressionMatchListener,
        ExpressionPromptSuggestionListener,
    ]


def test_plugin_components_in_collaborator_mode() -> None:
    """协作者模式应隐藏创建和查询工具。"""

    plugin = ExpressionLearningPlugin()
    config = ExpressionLearningConfig()
    config.collaborator.enabled = True
    plugin.config = config

    components = plugin.get_components()

    assert components == [
        ExpressionLearningService,
        ExpressionStyleFeedbackTool,
        ExpressionMatchListener,
        ExpressionPromptSuggestionListener,
    ]
