from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


# 确保测试中可直接 `import src...`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """创建事件循环的 fixture。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """创建临时目录的 fixture。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_plugin():
    """创建模拟插件的 fixture。"""
    plugin = MagicMock()
    plugin.plugin_name = "test_plugin"
    plugin.plugin_description = "Test plugin"
    plugin.plugin_version = "1.0.0"
    plugin.get_components = Mock(return_value=[])
    plugin.on_plugin_loaded = AsyncMock()
    plugin.on_plugin_unloaded = AsyncMock()
    return plugin


@pytest.fixture
def mock_chat_stream():
    """创建模拟聊天流的 fixture。"""
    stream = MagicMock()
    stream.stream_id = "test_stream_123"
    stream.chat_type = "group"
    stream.platform = "test_platform"

    # 模拟 context
    context = MagicMock()
    context.history_messages = []

    # 模拟消息
    mock_message = MagicMock()
    mock_message.processed_plain_text = "Hello world"
    mock_message.content = "Hello world"
    mock_message.sender_name = "TestUser"
    context.history_messages.append(mock_message)

    stream.context = context
    return stream
