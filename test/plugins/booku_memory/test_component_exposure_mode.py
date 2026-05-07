"""Booku Memory 插件组件暴露模式测试。"""

from __future__ import annotations

from plugins.booku_memory.agent import BookuMemoryCommandTool
from plugins.booku_memory.config import BookuMemoryConfig
from plugins.booku_memory.event_handler import (
    BookuMemoryStartupIngestHandler,
    MemoryFlashbackInjector,
)
from plugins.booku_memory.plugin import BookuMemoryAgentPlugin
from plugins.booku_memory.service import BookuKnowledgeService, BookuMemoryService


def _expected_components() -> list[type]:
    """返回命令模式下期望组件列表。"""

    return [
        BookuMemoryCommandTool,
        BookuMemoryService,
        BookuKnowledgeService,
        MemoryFlashbackInjector,
        BookuMemoryStartupIngestHandler,
    ]


def test_get_components_returns_command_mode_by_default() -> None:
    """缺少配置对象时应回退为命令模式。"""

    plugin = BookuMemoryAgentPlugin(config=None)
    assert plugin.get_components() == _expected_components()


def test_get_components_returns_command_mode_when_enabled() -> None:
    """插件启用时应始终暴露命令模式组件。"""

    cfg = BookuMemoryConfig()
    cfg.plugin.enabled = True
    plugin = BookuMemoryAgentPlugin(config=cfg)

    assert plugin.get_components() == _expected_components()


def test_get_components_returns_empty_when_plugin_disabled() -> None:
    """插件禁用时不应暴露任何组件。"""

    cfg = BookuMemoryConfig()
    cfg.plugin.enabled = False
    plugin = BookuMemoryAgentPlugin(config=cfg)

    assert plugin.get_components() == []
