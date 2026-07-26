"""token_counter 模块的单元测试。

重点验证图片等媒体内容的 token 估算不会被当作 base64 文本直接计算，
而是基于原始二进制字节数进行合理估算。
"""

from __future__ import annotations

import base64 as _b64

import pytest

from src.kernel.llm.payload import (
    Audio,
    File,
    Image,
    LLMPayload,
    Text,
    ToolCall,
    ToolResult,
    Video,
)
from src.kernel.llm.roles import ROLE
from src.kernel.llm.token_counter import (
    _estimate_media_tokens,
    _extract_media_parts,
    _serialize_payload,
    count_payload_tokens,
    count_text_tokens,
)

MODEL_ID = "gpt-4"


# ----------------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------------


def _make_payload(*parts, role: ROLE = ROLE.USER) -> LLMPayload:
    """构造一个包含给定 content 部分的 LLMPayload。"""
    return LLMPayload(role, list(parts))


def _b64_of(data: bytes) -> str:
    """返回 bytes 的纯 base64 字符串。"""
    return _b64.b64encode(data).decode("ascii")


# ----------------------------------------------------------------------------
# _estimate_media_tokens
# ----------------------------------------------------------------------------


class TestEstimateMediaTokens:
    """测试媒体 token 估算逻辑。"""

    def test_image_uses_raw_byte_count(self) -> None:
        """图片应基于原始字节数估算，而非 base64 文本长度。"""
        raw = b"x" * 10000  # 10KB 原始数据
        img = Image(_b64_of(raw))

        tokens = _estimate_media_tokens(img)

        # Image 比例 = 1/1000，所以 10000 字节 -> 约 10 token
        assert tokens == max(1, int(10000 * (1 / 1000)))
        assert tokens == 10

    def test_audio_uses_raw_byte_count(self) -> None:
        """音频应基于原始字节数估算。"""
        raw = b"y" * 5000  # 5KB
        audio = Audio(_b64_of(raw))

        tokens = _estimate_media_tokens(audio)

        # Audio 比例 = 1/100
        assert tokens == max(1, int(5000 * (1 / 100)))
        assert tokens == 50

    def test_video_uses_raw_byte_count(self) -> None:
        """视频应基于原始字节数估算。"""
        raw = b"z" * 20000  # 20KB
        video = Video(_b64_of(raw))

        tokens = _estimate_media_tokens(video)

        # Video 比例 = 1/200
        assert tokens == max(1, int(20000 * (1 / 200)))
        assert tokens == 100

    def test_file_uses_generic_ratio(self) -> None:
        """通用 File 应使用通用比例估算。"""
        raw = b"f" * 5000
        f = File(_b64_of(raw))

        tokens = _estimate_media_tokens(f)

        # File 比例 = 1/500
        assert tokens == max(1, int(5000 * (1 / 500)))
        assert tokens == 10

    def test_empty_media_returns_at_least_one(self) -> None:
        """空媒体数据至少返回 1 个 token。"""
        # 1 字节 -> 1*1/1000 = 0.001 -> max(1, 0) = 1
        img = Image(_b64_of(b"\x00"))
        tokens = _estimate_media_tokens(img)
        assert tokens >= 1

    def test_invalid_base64_falls_back_to_string_length(self) -> None:
        """base64 解码失败时回退到字符串长度估算。

        Image 构造函数会做严格的 base64 校验，合法 base64 字符串经构造后
        其 value 也能被标准库解码。因此这里用 Mock 对象模拟一个解码失败的场景，
        直接测试 _estimate_media_tokens 的 fallback 分支。
        """
        from unittest.mock import Mock

        # 构造一个 File 子类的 Mock，其 value 无法被 b64decode 解析
        fake_part = Mock(spec=File)
        invalid_value = "!!!!not-base64@@@@"
        fake_part.value = invalid_value

        tokens = _estimate_media_tokens(fake_part)

        # Mock 的 type 不是 Image/Audio/Video，回退到 File 的通用比例 1/500
        expected_raw = len(invalid_value) * 3 // 4
        assert tokens == max(1, int(expected_raw * (1 / 500)))

    def test_not_significantly_inflated_by_base64_text(self) -> None:
        """关键回归测试：媒体 token 不应被 base64 文本长度放大。

        base64 编码会使数据膨胀约 33%，且 tiktoken 对 base64 字符的编码
        通常每 1-2 个字符就消耗 1 token。旧实现会把一张 10KB 图片的
        base64 字符串（约 13KB 文本）计算成数千 token，这是错误的。
        """
        raw = b"x" * 10240  # 10KB 图片
        img = Image(_b64_of(raw))

        tokens = _estimate_media_tokens(img)

        # 新实现：10KB -> 约 10 token
        # 旧实现（base64 文本编码）：约 5000+ token
        # 回归断言：应远小于 base64 文本长度
        assert tokens < len(img.value) / 100
        assert tokens == 10


# ----------------------------------------------------------------------------
# _extract_media_parts
# ----------------------------------------------------------------------------


class TestExtractMediaParts:
    """测试从 payload 中提取媒体部分。"""

    def test_extracts_image_and_audio(self) -> None:
        """应提取 Image 和 Audio 部分。"""
        payload = _make_payload(
            Text("hello"),
            Image(_b64_of(b"img")),
            Audio(_b64_of(b"aud")),
            Text("world"),
        )

        media = _extract_media_parts(payload)

        assert len(media) == 2
        assert isinstance(media[0], Image)
        assert isinstance(media[1], Audio)

    def test_no_media_returns_empty(self) -> None:
        """纯文本 payload 应返回空列表。"""
        payload = _make_payload(Text("hello"), Text("world"))
        assert _extract_media_parts(payload) == []

    def test_extracts_video_and_file(self) -> None:
        """应提取 Video 和 File 部分。"""
        payload = _make_payload(
            Video(_b64_of(b"vid")),
            File(_b64_of(b"dat")),
        )

        media = _extract_media_parts(payload)

        assert len(media) == 2
        assert isinstance(media[0], Video)
        assert isinstance(media[1], File)


# ----------------------------------------------------------------------------
# _serialize_payload
# ----------------------------------------------------------------------------


class TestSerializePayload:
    """测试 payload 序列化不包含 base64 数据。"""

    def test_image_not_in_serialized_output(self) -> None:
        """序列化结果不应包含图片的 base64 数据。"""
        raw = b"x" * 5000
        img = Image(_b64_of(raw))
        payload = _make_payload(Text("caption"), img)

        serialized = _serialize_payload(payload)

        # base64 数据不应出现在序列化文本中
        assert img.value not in serialized
        assert "caption" in serialized

    def test_audio_not_in_serialized_output(self) -> None:
        """序列化结果不应包含音频的 base64 数据。"""
        raw = b"y" * 5000
        audio = Audio(_b64_of(raw))
        payload = _make_payload(Text("desc"), audio)

        serialized = _serialize_payload(payload)

        assert audio.value not in serialized
        assert "desc" in serialized

    def test_video_not_in_serialized_output(self) -> None:
        """序列化结果不应包含视频的 base64 数据。"""
        raw = b"z" * 5000
        video = Video(_b64_of(raw))
        payload = _make_payload(Text("desc"), video)

        serialized = _serialize_payload(payload)

        assert video.value not in serialized

    def test_text_payload_unchanged(self) -> None:
        """纯文本 payload 的序列化应保持不变。"""
        payload = _make_payload(Text("hello world"), role=ROLE.USER)

        serialized = _serialize_payload(payload)

        assert "role:user" in serialized
        assert "hello world" in serialized

    def test_tool_result_serialized(self) -> None:
        """ToolResult 应正常序列化。"""
        result = ToolResult(value={"temp": 25}, call_id="c1", name="get_weather")
        payload = _make_payload(result, role=ROLE.TOOL)

        serialized = _serialize_payload(payload)

        # to_text() 对 dict value 会做 JSON 序列化，包含 temp 字段
        assert "temp" in serialized
        assert "25" in serialized

    def test_tool_call_serialized(self) -> None:
        """ToolCall 应正常序列化。"""
        call = ToolCall(id="c1", name="get_weather", args={"location": "Tokyo"})
        payload = _make_payload(call, role=ROLE.ASSISTANT)

        serialized = _serialize_payload(payload)

        assert "get_weather" in serialized
        assert "Tokyo" in serialized


# ----------------------------------------------------------------------------
# count_payload_tokens
# ----------------------------------------------------------------------------


class TestCountPayloadTokens:
    """测试完整的 token 计数逻辑。"""

    def test_text_only_uses_tiktoken(self) -> None:
        """纯文本应使用 tiktoken 精确计数。"""
        payload = _make_payload(Text("hello world"), Text("second message"))

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # 应与直接编码文本的结果接近
        expected_text = "role:user\nhello world\nsecond message"
        from tiktoken import get_encoding

        enc = get_encoding("cl100k_base")
        assert tokens == len(enc.encode(expected_text))

    def test_image_token_is_reasonable(self) -> None:
        """包含图片的 payload token 应基于字节数估算，而非 base64 文本。"""
        raw = b"x" * 10240  # 10KB 图片
        img = Image(_b64_of(raw))
        payload = _make_payload(Text("look"), img)

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # 文本部分约几个 token + 图片估算 10 token
        # 关键是：不应是几千 token
        assert tokens < 100

    def test_image_not_inflated_by_base64(self) -> None:
        """回归测试：图片 token 不被 base64 文本长度放大。

        旧实现会将 10KB 图片（base64 约 13KB 文本）计算成 5000+ token，
        导致上下文预算严重误判。新实现应将其限制在个位数到两位数。
        """
        raw = b"x" * 10240
        img = Image(_b64_of(raw))
        payload = _make_payload(img)

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # 旧实现约 5000+ token，新实现约 10 token
        assert tokens < 50

    def test_multiple_media_summed(self) -> None:
        """多个媒体部分的 token 应累加。"""
        raw_img = b"x" * 10000
        raw_aud = b"y" * 5000
        payload = _make_payload(
            Image(_b64_of(raw_img)),
            Audio(_b64_of(raw_aud)),
        )

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # Image: 10, Audio: 50, 加上 role 前缀的文本 token（约 2-3）
        assert 50 < tokens < 70

    def test_mixed_text_and_media(self) -> None:
        """文本和媒体混合的 payload 应分别计数后累加。"""
        raw = b"x" * 5000  # 5KB 图片 -> 5 token
        payload = _make_payload(
            Text("This is a long enough text message for testing."),
            Image(_b64_of(raw)),
        )

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # 文本约 10 token + 图片 5 token + role 前缀约 2 token
        assert 10 < tokens < 30

    def test_empty_payload(self) -> None:
        """空 content 的 payload 只计算 role 前缀。"""
        payload = LLMPayload(ROLE.USER, [])

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        assert tokens > 0  # role 前缀本身有 token

    def test_video_with_mime_type(self) -> None:
        """Video 应正确估算 token，不受 mime_type 影响。"""
        raw = b"v" * 10000
        video = Video(_b64_of(raw), mime_type="video/webm")
        payload = _make_payload(video)

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # Video: 10000 * 1/200 = 50 token + role 前缀
        assert 40 < tokens < 60


# ----------------------------------------------------------------------------
# count_text_tokens
# ----------------------------------------------------------------------------


class TestCountTextTokens:
    """测试纯文本 token 计数（未受本次修改影响，做回归保护）。"""

    def test_basic_text(self) -> None:
        """简单文本应返回正数 token。"""
        tokens = count_text_tokens("hello world", model_identifier=MODEL_ID)
        assert tokens > 0

    def test_empty_text(self) -> None:
        """空文本应返回 0。"""
        assert count_text_tokens("", model_identifier=MODEL_ID) == 0

    def test_long_text_more_tokens(self) -> None:
        """长文本应有更多 token。"""
        short = count_text_tokens("hi", model_identifier=MODEL_ID)
        long = count_text_tokens("hello " * 100, model_identifier=MODEL_ID)
        assert long > short


# ----------------------------------------------------------------------------
# 综合回归测试
# ----------------------------------------------------------------------------


class TestRegressionMediaNotBase64Counted:
    """确保媒体内容不再被当作 base64 文本计算 token。"""

    def test_large_image_does_not_blow_up_budget(self) -> None:
        """大图片不应导致 token 计数爆炸。

        模拟一张 500KB 的图片，旧实现会将其 base64 文本（约 666KB）
        编码成约 30 万 token，完全淹没上下文预算。
        """
        raw = b"\x00" * (500 * 1024)  # 500KB
        img = Image(_b64_of(raw))
        payload = _make_payload(Text("check this"), img)

        tokens = count_payload_tokens([payload], model_identifier=MODEL_ID)

        # 新实现：500KB -> 500 token（1/1000 比例）+ 少量文本 token
        # 关键断言：不应超过 1000（远小于旧实现的 30 万）
        assert tokens < 1000
        # 更精确：约 500 + 文本
        assert 450 < tokens < 600

    def test_text_token_unchanged_with_nearby_media(self) -> None:
        """媒体存在不应影响同 payload 中文本的 token 计数。"""
        text = "The quick brown fox jumps over the lazy dog."
        payload_text_only = _make_payload(Text(text))
        payload_with_media = _make_payload(Text(text), Image(_b64_of(b"x" * 1000)))

        tokens_text_only = count_payload_tokens(
            [payload_text_only], model_identifier=MODEL_ID
        )
        tokens_with_media = count_payload_tokens(
            [payload_with_media], model_identifier=MODEL_ID
        )

        # 文本部分 token 应相同，差异仅为图片估算 + role 前缀
        media_tokens = _estimate_media_tokens(Image(_b64_of(b"x" * 1000)))
        assert tokens_with_media >= tokens_text_only + media_tokens
