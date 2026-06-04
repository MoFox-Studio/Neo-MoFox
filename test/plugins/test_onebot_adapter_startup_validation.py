"""测试 onebot_adapter 启动时的身份配置校验。"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import src.kernel.storage as kernel_storage
from src.kernel.concurrency import get_task_manager

from plugins.onebot_adapter.config import OneBotAdapterConfig
from plugins.onebot_adapter.plugin import OneBotAdapter, OneBotAdapterPlugin, _validate_bot_identity
from plugins.onebot_adapter.src.handlers import utils as onebot_utils


class _FakeCoreSink:
    """满足 BaseAdapter 初始化所需的最小 CoreSink 替身。"""

    def set_outgoing_handler(self, _handler) -> None:
        """设置发送处理器。"""

    def remove_outgoing_handler(self, _handler) -> None:
        """移除发送处理器。"""

    async def push_outgoing(self, _message) -> None:
        """推送单条外发消息。"""

    async def close(self) -> None:
        """关闭 sink。"""

    async def send(self, _message) -> None:
        """发送单条消息。"""

    async def send_many(self, _messages) -> None:
        """发送多条消息。"""


class _HangingWebSocket:
    """用于测试发送阶段超时的 WebSocket 替身。"""

    async def send(self, _request: str) -> None:
        """模拟永远无法及时完成的发送。"""

        await asyncio.sleep(1)


class TestOneBotAdapterStartupValidation:
    """测试 Napcat 适配器启动校验。"""

    def test_validate_bot_identity_accepts_valid_values(self) -> None:
        """有效配置应通过校验。"""
        config = OneBotAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
                "onebot_server": {
                    "mode": "reverse",
                    "host": "localhost",
                    "port": 8095,
                    "access_token": "",
                },
                "features": {
                    "group_list_type": "blacklist",
                    "group_list": [],
                    "private_list_type": "blacklist",
                    "private_list": [],
                    "ban_user_id": [],
                    "enable_poke": True,
                    "ignore_non_self_poke": False,
                    "poke_debounce_seconds": 2.0,
                    "enable_emoji_like": True,
                    "enable_reply_at": True,
                    "reply_at_rate": 0.5,
                    "enable_video_processing": True,
                    "video_max_size_mb": 100,
                    "video_download_timeout": 60,
                },
            }
        )

        _validate_bot_identity(config)

    def test_validate_bot_identity_rejects_empty_qq_id(self) -> None:
        """空 qq_id 应被拒绝。"""
        config = OneBotAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "", "qq_nickname": "MoFoxBot"},
                "onebot_server": {
                    "mode": "reverse",
                    "host": "localhost",
                    "port": 8095,
                    "access_token": "",
                },
                "features": {
                    "group_list_type": "blacklist",
                    "group_list": [],
                    "private_list_type": "blacklist",
                    "private_list": [],
                    "ban_user_id": [],
                    "enable_poke": True,
                    "ignore_non_self_poke": False,
                    "poke_debounce_seconds": 2.0,
                    "enable_emoji_like": True,
                    "enable_reply_at": True,
                    "reply_at_rate": 0.5,
                    "enable_video_processing": True,
                    "video_max_size_mb": 100,
                    "video_download_timeout": 60,
                },
            }
        )

        with pytest.raises(ValueError, match="bot.qq_id"):
            _validate_bot_identity(config)

    def test_validate_bot_identity_rejects_non_digit_qq_id(self) -> None:
        """非数字 qq_id 应被拒绝。"""
        config = OneBotAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "abc123", "qq_nickname": "MoFoxBot"},
                "onebot_server": {
                    "mode": "reverse",
                    "host": "localhost",
                    "port": 8095,
                    "access_token": "",
                },
                "features": {
                    "group_list_type": "blacklist",
                    "group_list": [],
                    "private_list_type": "blacklist",
                    "private_list": [],
                    "ban_user_id": [],
                    "enable_poke": True,
                    "ignore_non_self_poke": False,
                    "poke_debounce_seconds": 2.0,
                    "enable_emoji_like": True,
                    "enable_reply_at": True,
                    "reply_at_rate": 0.5,
                    "enable_video_processing": True,
                    "video_max_size_mb": 100,
                    "video_download_timeout": 60,
                },
            }
        )

        with pytest.raises(ValueError, match="bot.qq_id"):
            _validate_bot_identity(config)

    def test_validate_bot_identity_rejects_empty_nickname(self) -> None:
        """空 qq_nickname 应被拒绝。"""
        config = OneBotAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "123456789", "qq_nickname": "   "},
                "onebot_server": {
                    "mode": "reverse",
                    "host": "localhost",
                    "port": 8095,
                    "access_token": "",
                },
                "features": {
                    "group_list_type": "blacklist",
                    "group_list": [],
                    "private_list_type": "blacklist",
                    "private_list": [],
                    "ban_user_id": [],
                    "enable_poke": True,
                    "ignore_non_self_poke": False,
                    "poke_debounce_seconds": 2.0,
                    "enable_emoji_like": True,
                    "enable_reply_at": True,
                    "reply_at_rate": 0.5,
                    "enable_video_processing": True,
                    "video_max_size_mb": 100,
                    "video_download_timeout": 60,
                },
            }
        )

        with pytest.raises(ValueError, match="bot.qq_nickname"):
            _validate_bot_identity(config)


@pytest.mark.asyncio
async def test_get_bot_info_returns_standard_bot_name_field() -> None:
    """OneBotAdapter 应按统一契约返回 bot_name。"""
    config = OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 60,
            },
        }
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)

    bot_info = await adapter.get_bot_info()

    assert bot_info == {
        "bot_id": "123456789",
        "bot_name": "MoFoxBot",
        "platform": "qq",
    }


@pytest.mark.asyncio
async def test_send_onebot_api_times_out_when_websocket_send_blocks() -> None:
    """send_onebot_api 应对 WebSocket 发送阻塞施加总超时。"""

    config = OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 60,
            },
        }
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    adapter._ws = cast(Any, _HangingWebSocket())

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_onebot_api("send_group_msg", {"group_id": 1}, timeout=0.01)

    assert adapter._response_pool == {}


@pytest.mark.asyncio
async def test_handle_video_message_times_out_on_blocked_local_file_read(monkeypatch, tmp_path) -> None:
    """本地视频读取阻塞时应返回超时占位文本。"""

    config = OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 10,
            },
        }
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"test")

    monkeypatch.setattr(adapter.message_handler, "_get_video_io_timeout", lambda: 0.01)

    async def _blocked_to_thread(_func, *_args, **_kwargs):
        await asyncio.sleep(0.05)
        return b"late"

    monkeypatch.setattr(
        "plugins.onebot_adapter.src.handlers.to_core.message_handler.asyncio.to_thread",
        _blocked_to_thread,
    )

    result = await adapter.message_handler._handle_video_message(
        {"data": {"filePath": str(video_file)}}
    )

    assert result == {"type": "text", "data": "[视频处理超时]"}


@pytest.mark.asyncio
async def test_onebot_cache_load_timeout_does_not_block(monkeypatch) -> None:
    """慢缓存读取应在超时后快速降级。"""

    original_cache = {
        section: values.copy() for section, values in onebot_utils._CACHE.items()
    }
    original_loaded = onebot_utils._CACHE_LOADED

    async def _slow_load(_name: str) -> dict[str, Any] | None:
        await asyncio.sleep(0.05)
        return {"group_info": {"1": {"data": {"group_id": 1}, "ts": 1.0}}}

    monkeypatch.setattr(onebot_utils, "CACHE_IO_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(kernel_storage.json_store, "load", _slow_load)
    onebot_utils._CACHE_LOADED = False
    for section in onebot_utils._CACHE.values():
        section.clear()

    try:
        await onebot_utils._ensure_cache_loaded()
        assert onebot_utils._CACHE_LOADED is True
        assert onebot_utils._CACHE["group_info"] == {}
    finally:
        onebot_utils._CACHE_LOADED = original_loaded
        for section_name, values in original_cache.items():
            onebot_utils._CACHE[section_name].clear()
            onebot_utils._CACHE[section_name].update(values)


@pytest.mark.asyncio
async def test_meta_event_handler_reconnects_when_heartbeat_times_out(monkeypatch) -> None:
    """心跳超时时应触发适配器自动重连。"""

    config = OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 60,
            },
        }
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    adapter.reconnect = AsyncMock()

    handler = adapter.meta_event_handler
    handler.last_heart_beat = 0.0
    handler.interval = 0.01

    monkeypatch.setattr(
        "plugins.onebot_adapter.src.handlers.to_core.meta_event_handler.time.time",
        lambda: 0.03,
    )

    await handler.check_heartbeat(123456789)

    adapter.reconnect.assert_awaited_once()
    assert handler._interval_checking is False


@pytest.mark.asyncio
async def test_on_adapter_unloaded_stops_stale_heartbeat_monitor() -> None:
    """适配器卸载时应停止残留的心跳监控任务。"""

    config = OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 60,
            },
        }
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)

    handler = adapter.meta_event_handler
    handler.last_heart_beat = 1.0
    handler.interval = 60.0

    heartbeat_task = get_task_manager().create_task(
        asyncio.sleep(60),
        name="test_onebot_adapter_heartbeat_check",
        daemon=True,
    )
    handler._heartbeat_task = heartbeat_task
    handler._interval_checking = True

    await adapter.on_adapter_unloaded()

    task = heartbeat_task.task
    assert task is not None

    with pytest.raises(asyncio.CancelledError):
        await task

    assert handler._heartbeat_task is None
    assert handler._interval_checking is False
    assert handler.last_heart_beat == 0.0
