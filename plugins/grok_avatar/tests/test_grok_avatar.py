"""grok_avatar 测试。

覆盖：manifest 校验、配置默认值、提示词完整性、内嵌圆环算法、
服务 URL 构造与 b64 提取、命令路由。
"""

from __future__ import annotations

import io
import json
import math
import base64
from pathlib import Path

import httpx
import pytest
from PIL import Image

from ..config import GROK_BOT_ICON_PROMPT, GrokAvatarConfig
from ..google_frame import (
    COLORS,
    SEAMS,
    FrameError,
    apply_google_frame,
    decode_image_b64,
    encode_image_b64,
)
from ..service import GrokAvatarService

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_declares_required_fields() -> None:
    manifest = json.loads((_PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "grok_avatar"
    assert manifest["categories"], "categories 必须非空"
    assert all(
        c in ("tool", "chat", "fun", "information", "moderation")
        for c in manifest["categories"]
    )
    assert manifest.get("tags"), "tags 必须非空"
    assert manifest["dependencies"]["plugins"] == ["onebot_expand"]


def test_manifest_api_version_matches_core() -> None:
    from src.app.plugin_system.api import PLUGIN_API_VERSIONS

    manifest = json.loads((_PLUGIN_ROOT / "manifest.json").read_text(encoding="utf-8"))
    for module, version in manifest["api_version"].items():
        assert PLUGIN_API_VERSIONS.get(module) == version


# ── 配置与提示词 ──────────────────────────────────────────────────────────


def test_prompt_is_verbatim_korean_spec() -> None:
    """提示词必须是用户提供的韩文规格原文（关键锚点逐字保留）。"""
    for anchor in (
        "[목표]",
        "[원본에서 가져올 정보]",
        "[얼굴]",
        "[눈]",
        "[구도]",
        "[머리카락·장식·채색]",
        "[배경과 제외 요소]",
        "[충돌 처리]",
        "[실행과 후속 수정]",
        "검은 캡슐 눈을 가진 미니멀 2D 봇 아이콘",
        "실제로 생성하거나 확인하지 않은 결과를 생성 완료 또는 검증 완료라고 설명하지 않는다.",
    ):
        assert anchor in GROK_BOT_ICON_PROMPT, f"提示词缺少锚点: {anchor}"


def test_config_defaults_match_reference_project() -> None:
    cfg = GrokAvatarConfig()
    assert cfg.frame.border_ratio == 0.04
    assert cfg.frame.gap_ratio == 0.02
    assert cfg.generation.size == 1024
    assert cfg.generation.output_size == 640
    assert cfg.api.model == ""


# ── 内嵌圆环算法 ──────────────────────────────────────────────────────────


def _synthetic_grok_avatar(size: int = 512) -> bytes:
    img = Image.new("RGB", (size, size), (26, 26, 26))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_frame_output_is_square_jpeg() -> None:
    out = apply_google_frame(_synthetic_grok_avatar(), output_size=640)
    img = Image.open(io.BytesIO(out))
    assert img.size == (640, 640)


def test_frame_ring_segment_colors_at_calibrated_angles() -> None:
    """在标定接缝中点采样，四段颜色应与 Google 品牌色一致。"""
    out = apply_google_frame(_synthetic_grok_avatar(), output_size=640)
    px = Image.open(io.BytesIO(out)).convert("RGB")
    center = 320
    radius = 314

    def sample(angle_deg: float) -> tuple[int, int, int]:
        x = center + radius * math.cos(math.radians(angle_deg))
        y = center + radius * math.sin(math.radians(angle_deg))
        return px.getpixel((int(x), int(y)))

    # 接缝中点：green(48~138)→93, yellow(138~206)→172, red(206~314)→260, blue(314~408)→348
    # JPEG 有损压缩允许 ±3 的颜色容差。
    for angle, expected in (
        (93, COLORS["green"]),
        (172, COLORS["yellow"]),
        (260, COLORS["red"]),
        (348, COLORS["blue"]),
    ):
        actual = sample(angle)
        assert all(
            abs(a - b) <= 3 for a, b in zip(actual, expected)
        ), f"{angle}° 期望 {expected}，实际 {actual}"


def test_frame_three_zone_layout() -> None:
    """顶部纵向采样：圆环(红) → 白色间隔 → 头像区。"""
    out = apply_google_frame(_synthetic_grok_avatar(), output_size=640)
    px = Image.open(io.BytesIO(out)).convert("RGB")
    # JPEG 有损压缩允许 ±3 的颜色容差。
    ring = px.getpixel((320, 10))
    assert all(abs(a - b) <= 3 for a, b in zip(ring, COLORS["red"])), ring  # 圆环外圈
    gap = px.getpixel((320, 33))
    assert all(v >= 252 for v in gap), gap  # 白色间隔（JPEG 容差）
    assert px.getpixel((320, 320)) == (26, 26, 26)  # 头像区（源图底色）


def test_frame_rejects_tiny_output() -> None:
    with pytest.raises(FrameError):
        apply_google_frame(_synthetic_grok_avatar(), output_size=16)


def test_frame_b64_roundtrip() -> None:
    out = apply_google_frame(_synthetic_grok_avatar(), output_size=640)
    b64 = encode_image_b64(out)
    assert decode_image_b64(b64) == out
    # data URL 前缀兼容
    assert decode_image_b64(f"data:image/jpeg;base64,{b64}") == out
    # base64| 前缀兼容
    assert decode_image_b64(f"base64|{b64}") == out


def test_seams_match_reference_project() -> None:
    """接缝角度必须与原项目标定值一致。"""
    assert SEAMS == {
        "yellow_red": 206,
        "red_blue": 314,
        "blue_green": 48,
        "green_yellow": 138,
    }


# ── Service ───────────────────────────────────────────────────────────────


def test_build_edits_url_variants() -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)
    build = service._build_edits_url
    assert build("https://api.example.com/v1") == "https://api.example.com/v1/images/edits"
    assert build("https://api.example.com") == "https://api.example.com/v1/images/edits"
    assert build("https://api.example.com/v1/") == "https://api.example.com/v1/images/edits"
    assert (
        build("https://api.example.com/v1/images/edits")
        == "https://api.example.com/v1/images/edits"
    )
    assert (
        build("https://api.example.com/v1/images/generations")
        == "https://api.example.com/v1/images/edits"
    )
    assert build("https://api.example.com/") == "https://api.example.com/v1/images/edits"


def test_detect_image_type() -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)
    assert service._detect_image_type(b"\x89PNG\r\n\x1a\n") == ("avatar.png", "image/png")
    assert service._detect_image_type(b"\xff\xd8\xff\xe0") == ("avatar.jpg", "image/jpeg")
    assert service._detect_image_type(b"GIF89a") == ("avatar.gif", "image/gif")
    assert service._detect_image_type(b"RIFFxxxxWEBP") == ("avatar.webp", "image/webp")


def test_extract_b64_variants() -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)
    assert service._extract_b64({"data": [{"b64_json": "abc"}]}) == "abc"
    assert service._extract_b64({"data": [{"image": "abc"}]}) == "abc"
    assert service._extract_b64({"data": []}) is None
    assert service._extract_b64({}) is None
    assert service._extract_b64("not a dict") is None


def test_extract_avatar_url_accepts_standard_and_nested_fields() -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)
    assert service._extract_avatar_url({"data": {"avatar": "https://a/avatar.jpg"}}) == (
        "https://a/avatar.jpg"
    )
    assert service._extract_avatar_url({"avatar_url": "https://b/avatar.jpg"}) == (
        "https://b/avatar.jpg"
    )
    assert service._extract_avatar_url({"data": {"user_avatar": "https://c/avatar.jpg"}}) == (
        "https://c/avatar.jpg"
    )
    assert service._extract_avatar_url({"data": {"avatar": ""}}) is None


def test_extract_media_candidates_supports_runtime_and_database_content() -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)

    class _Message:
        message_id = "m-1"
        content = "{'media': [{'type': 'image', 'image_id': 'hash-1'}]}"
        extra = {}
        raw_data = None

    candidates = service._message_image_candidates(_Message())
    assert candidates == [{"type": "image", "image_id": "hash-1", "message_id": "m-1"}]


def test_decode_input_b64_accepts_base64_prefixes() -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)
    payload = b"image-bytes"
    encoded = base64.b64encode(payload).decode()
    assert service._decode_input_b64(encoded) == payload
    assert service._decode_input_b64(f"base64|{encoded}") == payload


@pytest.mark.asyncio
async def test_resolve_chat_image_uses_onebot_get_msg_then_get_image(monkeypatch) -> None:
    service = GrokAvatarService.__new__(GrokAvatarService)
    calls: list[tuple[str, object]] = []

    class _Message:
        message_id = "m-2"
        content = {"media": [{"type": "image", "image_id": "hash-2"}]}
        extra = {}
        raw_data = None

    async def get_stream_messages(stream_id, limit, offset):
        assert (stream_id, limit, offset) == ("stream-1", 100, 0)
        return [_Message()]

    class _MessageService:
        async def get_msg(self, message_id):
            calls.append(("get_msg", message_id))
            return {"data": {"message": [{"type": "image", "data": {"file": "file-2"}}]}}

    class _FileService:
        async def get_image(self, file):
            calls.append(("get_image", file))
            return {"data": {"base64": base64.b64encode(b"image-bytes").decode()}}

    from src.app.plugin_system.api import service_api, stream_api

    monkeypatch.setattr(stream_api, "get_stream_messages", get_stream_messages)
    monkeypatch.setattr(
        service_api,
        "get_service",
        lambda signature: {
            "onebot_expand:service:message_service": _MessageService(),
            "onebot_expand:service:file_service": _FileService(),
        }.get(signature),
    )

    assert await service.resolve_chat_image("stream-1") == b"image-bytes"
    assert calls == [("get_msg", "m-2"), ("get_image", "file-2")]


@pytest.mark.asyncio
async def test_generate_uses_fresh_image_edit_request_with_original_avatar(monkeypatch) -> None:
    """每次调用必须把原始头像作为唯一图片重新上传，不能走历史对话续写。"""
    service = GrokAvatarService.__new__(GrokAvatarService)
    cfg = GrokAvatarConfig(
        api={
            "base_url": "https://image.example.com/v1",
            "api_key": "test-key",
            "model": "image-model",
        }
    )
    original_avatar = b"\x89PNG\r\n\x1a\noriginal-avatar"
    generated = _synthetic_grok_avatar()
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": [{"b64_json": encode_image_b64(generated)}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, data, files, headers):
            captured.update({"url": url, "data": data, "files": files, "headers": headers})
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    result = await service.generate_grok_avatar(original_avatar, cfg)

    assert result == generated
    assert captured["url"] == "https://image.example.com/v1/images/edits"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert isinstance(captured["data"], dict)
    assert captured["data"]["prompt"] == cfg.generation.prompt
    assert "messages" not in captured["data"]
    assert "previous_response_id" not in captured["data"]
    image = captured["files"]["image"]
    assert image[1] == original_avatar
    assert image[2] == "image/png"


# ── Tool ─────────────────────────────────────────────────────────────────


def test_tool_schema_exposes_mode_and_qq() -> None:
    """Tool schema 应暴露 mode 与 qq_number 两个参数。"""
    from ..tools import GrokAvatarTool

    schema = GrokAvatarTool.to_schema()
    # schema 形如 {"type":"function","function":{"name":...,"parameters":{...}}}
    function = schema.get("function", schema) if isinstance(schema, dict) else {}
    params = function.get("parameters", function)
    props = params.get("properties", params) if isinstance(params, dict) else {}
    assert "mode" in props
    assert "qq_number" in props
    assert "source" in props
    assert "image" in props
    assert "history_index" in props


def test_tool_resolve_qq_prefers_explicit_then_sender() -> None:
    from ..tools import GrokAvatarTool

    tool = GrokAvatarTool.__new__(GrokAvatarTool)

    class _Msg:
        sender_id = "10086"

    tool.trigger_message = _Msg()
    assert tool._resolve_qq(None) == "10086"
    assert tool._resolve_qq(" 12345 ") == "12345"

    tool.trigger_message = None
    assert tool._resolve_qq(None) == ""
