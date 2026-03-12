"""Booku Memory 插件组件暴露模式测试。"""

from __future__ import annotations

from plugins.booku_memory.agent import (
    BookuMemoryCreateTool,
    BookuMemoryEditInherentTool,
    BookuMemoryReadAgent,
    BookuMemoryRetrieveTool,
    BookuMemoryWriteAgent,
)
from plugins.booku_memory.config import BookuMemoryConfig
from plugins.booku_memory.event_handler import (
    BookuMemoryStartupIngestHandler,
    MemoryFlashbackInjector,
)
from plugins.booku_memory.plugin import BookuMemoryAgentPlugin
from plugins.booku_memory.service import BookuKnowledgeService, BookuMemoryService


def test_get_components_returns_agent_mode_by_default() -> None:
    """缺少配置对象时应回退为 agent 代理模式。"""

    plugin = BookuMemoryAgentPlugin(config=None)

    assert plugin.get_components() == [
        BookuMemoryWriteAgent,
        BookuMemoryReadAgent,
        BookuMemoryService,
        BookuKnowledgeService,
        BookuMemoryStartupIngestHandler,
        MemoryFlashbackInjector,
    ]


def test_get_components_returns_agent_mode_when_enabled() -> None:
    """开启代理模式时应暴露读写 Agent。"""

    cfg = BookuMemoryConfig()
    cfg.plugin.enable_agent_proxy_mode = True
    plugin = BookuMemoryAgentPlugin(config=cfg)

    assert plugin.get_components() == [
        BookuMemoryWriteAgent,
        BookuMemoryReadAgent,
        BookuMemoryService,
        BookuKnowledgeService,
        BookuMemoryStartupIngestHandler,
        MemoryFlashbackInjector,
    ]


def test_get_components_returns_tool_mode_when_proxy_disabled() -> None:
    """关闭代理模式时应改为直接暴露工具。"""

    cfg = BookuMemoryConfig()
    cfg.plugin.enable_agent_proxy_mode = False
    plugin = BookuMemoryAgentPlugin(config=cfg)

    assert plugin.get_components() == [
        BookuMemoryRetrieveTool,
        BookuMemoryCreateTool,
        BookuMemoryEditInherentTool,
        BookuMemoryService,
        BookuKnowledgeService,
        MemoryFlashbackInjector,
        BookuMemoryStartupIngestHandler,
    ]


def test_get_components_returns_empty_when_plugin_disabled() -> None:
    """插件被禁用时不应暴露任何组件。"""

    cfg = BookuMemoryConfig()
    cfg.plugin.enabled = False
    plugin = BookuMemoryAgentPlugin(config=cfg)

    assert plugin.get_components() == []