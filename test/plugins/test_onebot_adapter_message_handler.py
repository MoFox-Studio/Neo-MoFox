"""测试 OneBot 适配器消息转换。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.onebot_adapter.src.handlers.to_core import message_handler


@pytest.mark.asyncio
async def test_handle_raw_message_preserves_flash_transfer_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QQ 闪传消息段应原样透传给核心层。"""
    monkeypatch.setattr(
        message_handler,
        "get_group_info",
        AsyncMock(return_value={"group_name": "测试群"}),
    )
    handler = message_handler.MessageHandler(SimpleNamespace(plugin=None))
    flash_transfer_segment = {
        "type": "flashtransfer",
        "data": {"fileSetId": "flash-transfer-file-set-id"},
    }
    raw_message = {
        "message_type": "group",
        "message_id": 63001,
        "group_id": 63002,
        "sender": {
            "user_id": 63003,
            "nickname": "测试用户",
            "card": "群名片",
            "role": "member",
        },
        "message": [flash_transfer_segment],
    }

    envelope = await handler.handle_raw_message(raw_message)

    assert envelope["message_segment"] == flash_transfer_segment
    assert "flashtransfer" in envelope["message_info"]["format_info"]["content_format"]
