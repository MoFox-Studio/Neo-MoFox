"""send_api 媒体 content 结构化组装的单元测试。

覆盖发送端 ``send_image``/``send_emoji``/``send_voice``/``send_video``
组装 ``{"text", "media"}`` 结构化 content（与接收端 ``_build_content``
产出同构），确保裸 base64 不再作为 ``Message.content`` 出现。
"""

from __future__ import annotations

from unittest.mock import patch

from src.app.plugin_system.api.send_api import _build_media_content


def test_build_media_content_image_normalizes_and_hashes() -> None:
    """图片媒体应规范化 base64 并计算 image_id（与接收端哈希算法一致）。"""
    with patch(
        "src.core.managers.media_manager.MediaManager.compute_media_hash",
        return_value="hash123",
    ) as mock_hash:
        content = _build_media_content(
            "image", "iVBORw0KGgo=", "[图片]", "image_id"
        )

    assert content["text"] == "[图片]"
    assert len(content["media"]) == 1
    item = content["media"][0]
    assert item["type"] == "image"
    assert item["data"] == "base64|iVBORw0KGgo="
    assert item["image_id"] == "hash123"
    mock_hash.assert_called_once_with("base64|iVBORw0KGgo=")


def test_build_media_content_voice_uses_voice_id() -> None:
    """语音媒体应注入 voice_id 字段名。"""
    with patch(
        "src.core.managers.media_manager.MediaManager.compute_media_hash",
        return_value="voice_hash",
    ):
        content = _build_media_content(
            "voice", "UklGRg==", "[语音]", "voice_id"
        )

    assert content["media"][0]["type"] == "voice"
    assert content["media"][0]["voice_id"] == "voice_hash"
    assert "image_id" not in content["media"][0]


def test_build_media_content_keeps_existing_prefix() -> None:
    """已带 base64| 前缀的数据不应重复添加前缀。"""
    with patch(
        "src.core.managers.media_manager.MediaManager.compute_media_hash",
        return_value="hash",
    ):
        content = _build_media_content(
            "image", "base64|iVBORw0KGgo=", "[图片]", "image_id"
        )

    assert content["media"][0]["data"] == "base64|iVBORw0KGgo="
