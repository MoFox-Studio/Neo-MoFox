from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from plugins.tts_http_server.action import GenerateVoiceAction
from plugins.tts_http_server.config import TTSHttpServerConfig
from plugins.tts_http_server.plugin import TTSHttpServerPlugin
from plugins.tts_http_server.router import TTSHttpServerRouter
from plugins.tts_http_server.service import TTSProviderRegistryService


def test_tts_http_server_always_registers_generate_voice_component() -> None:
    config = TTSHttpServerConfig()

    plugin = TTSHttpServerPlugin(config=config)

    assert plugin.get_components() == [
        GenerateVoiceAction,
        TTSHttpServerRouter,
        TTSProviderRegistryService,
    ]


@pytest.mark.asyncio
async def test_generate_voice_action_is_disabled_when_config_is_off() -> None:
    config = TTSHttpServerConfig()
    action = GenerateVoiceAction(
        chat_stream=cast(Any, SimpleNamespace(stream_id="stream-1")),
        plugin=cast(Any, SimpleNamespace(config=config)),
    )

    with patch("plugins.tts_http_server.action.get_chatter_manager") as mock_get_chatter_manager:
        mock_get_chatter_manager.return_value = MagicMock()

        assert await action.go_activate() is False


@pytest.mark.asyncio
async def test_generate_voice_action_is_disabled_for_voice_chatter() -> None:
    config = TTSHttpServerConfig()
    config.action.expose_generate_voice_action = True
    action = GenerateVoiceAction(
        chat_stream=cast(Any, SimpleNamespace(stream_id="stream-1")),
        plugin=cast(Any, SimpleNamespace(config=config)),
    )

    active_chatter = MagicMock()
    active_chatter.chatter_name = "voice_chatter"
    active_chatter.get_signature.return_value = "voice_chatter:chatter:voice_chatter"

    with patch("plugins.tts_http_server.action.get_chatter_manager") as mock_get_chatter_manager:
        mock_get_chatter_manager.return_value.get_chatter_by_stream.return_value = active_chatter

        assert await action.go_activate() is False


@pytest.mark.asyncio
async def test_generate_voice_action_is_enabled_for_normal_chatter_when_config_is_on() -> None:
    config = TTSHttpServerConfig()
    config.action.expose_generate_voice_action = True
    action = GenerateVoiceAction(
        chat_stream=cast(Any, SimpleNamespace(stream_id="stream-1")),
        plugin=cast(Any, SimpleNamespace(config=config)),
    )

    active_chatter = MagicMock()
    active_chatter.chatter_name = "default_chatter"
    active_chatter.get_signature.return_value = "default_chatter:chatter:default_chatter"

    with patch("plugins.tts_http_server.action.get_chatter_manager") as mock_get_chatter_manager:
        mock_get_chatter_manager.return_value.get_chatter_by_stream.return_value = active_chatter

        assert await action.go_activate() is True