"""测试 napcat_adapter 启动时的身份配置校验。"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
import src.kernel.storage as kernel_storage

from plugins.napcat_adapter.config import NapcatAdapterConfig
from plugins.napcat_adapter.plugin import NapcatAdapter, NapcatAdapterPlugin, _validate_bot_identity
from plugins.napcat_adapter.src.handlers import utils as napcat_utils


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


class TestNapcatAdapterStartupValidation:
    """测试 Napcat 适配器启动校验。"""

    def test_validate_bot_identity_accepts_valid_values(self) -> None:
        """有效配置应通过校验。"""
        config = NapcatAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
                "napcat_server": {
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
        config = NapcatAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "", "qq_nickname": "MoFoxBot"},
                "napcat_server": {
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
        config = NapcatAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "abc123", "qq_nickname": "MoFoxBot"},
                "napcat_server": {
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
        config = NapcatAdapterConfig.from_dict(
            {
                "plugin": {"enabled": True, "config_version": "2.0.0"},
                "bot": {"qq_id": "123456789", "qq_nickname": "   "},
                "napcat_server": {
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
    """NapcatAdapter 应按统一契约返回 bot_name。"""
    config = NapcatAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "napcat_server": {
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
    plugin = NapcatAdapterPlugin(config=config)
    adapter = NapcatAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)

    bot_info = await adapter.get_bot_info()

    assert bot_info == {
        "bot_id": "123456789",
        "bot_name": "MoFoxBot",
        "platform": "qq",
    }


@pytest.mark.asyncio
async def test_send_napcat_api_times_out_when_websocket_send_blocks() -> None:
    """send_napcat_api 应对 WebSocket 发送阻塞施加总超时。"""

    config = NapcatAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "napcat_server": {
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
    plugin = NapcatAdapterPlugin(config=config)
    adapter = NapcatAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    adapter._ws = _HangingWebSocket()

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_napcat_api("send_group_msg", {"group_id": 1}, timeout=0.01)

    assert adapter._response_pool == {}


@pytest.mark.asyncio
async def test_handle_video_message_times_out_on_blocked_local_file_read(monkeypatch, tmp_path) -> None:
    """本地视频读取阻塞时应返回超时占位文本。"""

    config = NapcatAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "napcat_server": {
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
    plugin = NapcatAdapterPlugin(config=config)
    adapter = NapcatAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"test")

    monkeypatch.setattr(adapter.message_handler, "_get_video_io_timeout", lambda: 0.01)

    async def _blocked_to_thread(_func, *_args, **_kwargs):
        await asyncio.sleep(0.05)
        return b"late"

    monkeypatch.setattr(
        "plugins.napcat_adapter.src.handlers.to_core.message_handler.asyncio.to_thread",
        _blocked_to_thread,
    )

    result = await adapter.message_handler._handle_video_message(
        {"data": {"filePath": str(video_file)}}
    )

    assert result == {"type": "text", "data": "[视频处理超时]"}


@pytest.mark.asyncio
async def test_napcat_cache_load_timeout_does_not_block(monkeypatch) -> None:
    """慢缓存读取应在超时后快速降级。"""

    original_cache = {
        section: values.copy() for section, values in napcat_utils._CACHE.items()
    }
    original_loaded = napcat_utils._CACHE_LOADED

    async def _slow_load(_name: str) -> dict[str, Any] | None:
        await asyncio.sleep(0.05)
        return {"group_info": {"1": {"data": {"group_id": 1}, "ts": 1.0}}}

    monkeypatch.setattr(napcat_utils, "CACHE_IO_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(kernel_storage.json_store, "load", _slow_load)
    napcat_utils._CACHE_LOADED = False
    for section in napcat_utils._CACHE.values():
        section.clear()

    try:
        await napcat_utils._ensure_cache_loaded()
        assert napcat_utils._CACHE_LOADED is True
        assert napcat_utils._CACHE["group_info"] == {}
    finally:
        napcat_utils._CACHE_LOADED = original_loaded
        for section_name, values in original_cache.items():
            napcat_utils._CACHE[section_name].clear()
            napcat_utils._CACHE[section_name].update(values)
