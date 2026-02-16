"""测试 napcat_adapter 启动时的身份配置校验。"""

from __future__ import annotations

import pytest

from plugins.napcat_adapter.config import NapcatAdapterConfig
from plugins.napcat_adapter.plugin import _validate_bot_identity


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
                    "ban_qq_bot": False,
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
                    "ban_qq_bot": False,
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
                    "ban_qq_bot": False,
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
                    "ban_qq_bot": False,
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
