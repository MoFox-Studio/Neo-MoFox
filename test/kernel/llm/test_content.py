"""Tests for payload/content.py."""

from __future__ import annotations

import base64
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from src.kernel.llm.payload.content import Audio, Content, File, Image, Text


class TestContent:
    """Test cases for Content base class."""

    def test_content_is_frozen(self) -> None:
        """Test that Content is frozen."""
        content = Content()
        with pytest.raises(Exception):  # FrozenInstanceError from dataclasses
            content.some_attr = "value"

    def test_content_has_slots(self) -> None:
        """Test that Content uses slots."""
        content = Content()
        with pytest.raises(AttributeError):
            content.__dict__


class TestText:
    """Test cases for Text content."""

    def test_text_creation(self) -> None:
        """Test creating Text content."""
        text = Text(text="Hello, world!")
        assert text.text == "Hello, world!"

    def test_text_is_content_subclass(self) -> None:
        """Test that Text is a Content subclass."""
        assert isinstance(Text("test"), Content)

    def test_text_is_frozen(self) -> None:
        """Test that Text is frozen."""
        text = Text(text="test")
        with pytest.raises(Exception):
            text.text = "modified"

    def test_text_has_slots(self) -> None:
        """Test that Text uses slots."""
        text = Text(text="test")
        with pytest.raises(AttributeError):
            text.__dict__

    def test_text_equality(self) -> None:
        """Test Text equality."""
        text1 = Text(text="hello")
        text2 = Text(text="hello")
        text3 = Text(text="world")
        assert text1 == text2
        assert text1 != text3

    def test_text_empty_string(self) -> None:
        """Test Text with empty string."""
        text = Text(text="")
        assert text.text == ""

    def test_text_unicode(self) -> None:
        """Test Text with unicode content."""
        text = Text(text="Hello 世界! 🌍")
        assert text.text == "Hello 世界! 🌍"


class TestImage:
    """Test cases for Image content."""

    def test_image_creation_with_file_path(self, tmp_path: Path) -> None:
        """Test creating Image with an actual file path; value is normalized to pure base64."""
        img_file = tmp_path / "pic.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n")
        image = Image(str(img_file))
        assert image.value == base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("utf-8")

    def test_image_creation_with_data_url(self) -> None:
        """Test creating Image with data URL; value is stripped to pure base64."""
        b64_payload = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        data_url = f"data:image/png;base64,{b64_payload}"
        image = Image(data_url)
        assert image.value == b64_payload

    def test_image_creation_with_base64_prefix(self) -> None:
        """Test creating Image with base64| prefix; value is stripped to pure base64."""
        b64_payload = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        image = Image(f"base64|{b64_payload}")
        assert image.value == b64_payload

    def test_image_creation_with_pure_base64(self) -> None:
        """Test creating Image with a pure base64 string."""
        b64 = base64.b64encode(b"fake_image_bytes").decode("utf-8")
        image = Image(b64)
        assert image.value == b64

    def test_image_creation_from_bytesio(self) -> None:
        """Test creating Image from a BytesIO object."""
        data = b"fake_image_bytes"
        image = Image(BytesIO(data))
        assert image.value == base64.b64encode(data).decode("utf-8")

    def test_image_is_content_subclass(self) -> None:
        """Test that Image is a Content subclass."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        assert isinstance(Image(b64), Content)

    def test_image_is_file_subclass(self) -> None:
        """Test that Image is also a File subclass."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        assert isinstance(Image(b64), File)

    def test_image_is_frozen(self) -> None:
        """Test that Image is frozen (immutable)."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        image = Image(b64)
        with pytest.raises(AttributeError):
            image.value = "modified"  # type: ignore[misc]

    def test_image_has_slots(self) -> None:
        """Test that Image uses __slots__ (no __dict__)."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        image = Image(b64)
        with pytest.raises(AttributeError):
            image.__dict__

    def test_image_equality(self) -> None:
        """Test Image equality based on value."""
        b64_a = base64.b64encode(b"aaa").decode("utf-8")
        b64_b = base64.b64encode(b"bbb").decode("utf-8")
        assert Image(b64_a) == Image(b64_a)
        assert Image(b64_a) != Image(b64_b)

    def test_image_data_url_strips_to_base64(self) -> None:
        """Value from a data URL should be pure base64, not starting with 'data:'."""
        b64_payload = "ABC123DEF456GHI789JKL"
        # 补 padding 使其成为合法 base64
        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        image = Image(f"data:image/png;base64,{padded}")
        assert not image.value.startswith("data:")
        assert image.value == padded


class TestAudio:
    """Test cases for Audio content."""

    def test_audio_creation_with_file_path(self, tmp_path: Path) -> None:
        """Test creating Audio with an actual file path; value is normalized to pure base64."""
        audio_file = tmp_path / "speech.mp3"
        audio_file.write_bytes(b"ID3\x03\x00")
        audio = Audio(str(audio_file))
        assert audio.value == base64.b64encode(b"ID3\x03\x00").decode("utf-8")

    def test_audio_creation_with_data_url(self) -> None:
        """Test creating Audio with data URL; value is stripped to pure base64."""
        b64_payload = "//uQRAAAAWMSLwUIYAAsYkXgoQwAEaYLWfkWgAI0wWs/ItAAAG84AA0WAgAAAAAAabwA"
        data_url = f"data:audio/mp3;base64,{b64_payload}"
        audio = Audio(data_url)
        assert audio.value == b64_payload

    def test_audio_creation_with_pure_base64(self) -> None:
        """Test creating Audio with a pure base64 string."""
        b64 = base64.b64encode(b"fake_audio_bytes").decode("utf-8")
        audio = Audio(b64)
        assert audio.value == b64

    def test_audio_creation_from_bytesio(self) -> None:
        """Test creating Audio from a BytesIO object."""
        data = b"fake_audio_bytes"
        audio = Audio(BytesIO(data))
        assert audio.value == base64.b64encode(data).decode("utf-8")

    def test_audio_is_content_subclass(self) -> None:
        """Test that Audio is a Content subclass."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        assert isinstance(Audio(b64), Content)

    def test_audio_is_file_subclass(self) -> None:
        """Test that Audio is also a File subclass."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        assert isinstance(Audio(b64), File)

    def test_audio_is_frozen(self) -> None:
        """Test that Audio is frozen (immutable)."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        audio = Audio(b64)
        with pytest.raises(AttributeError):
            audio.value = "modified"  # type: ignore[misc]

    def test_audio_has_slots(self) -> None:
        """Test that Audio uses __slots__ (no __dict__)."""
        b64 = base64.b64encode(b"x").decode("utf-8")
        audio = Audio(b64)
        with pytest.raises(AttributeError):
            audio.__dict__

    def test_audio_equality(self) -> None:
        """Test Audio equality based on value."""
        b64_a = base64.b64encode(b"aaa").decode("utf-8")
        b64_b = base64.b64encode(b"bbb").decode("utf-8")
        assert Audio(b64_a) == Audio(b64_a)
        assert Audio(b64_a) != Audio(b64_b)


# ---------------------------------------------------------------------------
# TestFile
# ---------------------------------------------------------------------------

_SAMPLE_BYTES = b"hello, file content"
_SAMPLE_B64 = base64.b64encode(_SAMPLE_BYTES).decode("utf-8")


class TestFile:
    """Test cases for File content."""

    # --- 文件对象输入 ---

    def test_file_from_bytesio(self) -> None:
        """通过 BytesIO 构造 File，value 应为纯 base64 字符串。"""
        f = File(BytesIO(_SAMPLE_BYTES))
        assert f.value == _SAMPLE_B64

    def test_file_from_bytesio_empty(self) -> None:
        """空 BytesIO 应生成空字符串的 base64（""）。"""
        f = File(BytesIO(b""))
        assert f.value == base64.b64encode(b"").decode("utf-8")

    # --- 文件路径输入 ---

    def test_file_from_path_str(self, tmp_path: Path) -> None:
        """通过文件路径字符串构造 File。"""
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(_SAMPLE_BYTES)
        f = File(str(file_path))
        assert f.value == _SAMPLE_B64

    def test_file_from_path_object(self, tmp_path: Path) -> None:
        """通过 Path 对象构造 File。"""
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(_SAMPLE_BYTES)
        f = File(file_path)
        assert f.value == _SAMPLE_B64

    def test_file_from_nonexistent_path_raises(self) -> None:
        """传入不存在路径且不是 base64 字符串时应抛出 ValueError。"""
        with pytest.raises(ValueError):
            File("/nonexistent/path/to/file.bin")

    # --- base64 字符串输入 ---

    def test_file_from_pure_base64_string(self) -> None:
        """传入纯 base64 字符串，value 应原样保留（去除空白）。"""
        f = File(_SAMPLE_B64)
        assert f.value == _SAMPLE_B64

    def test_file_from_data_url(self) -> None:
        """传入 data URL，应剥离前缀后保留 base64 部分。"""
        data_url = f"data:application/octet-stream;base64,{_SAMPLE_B64}"
        f = File(data_url)
        assert f.value == _SAMPLE_B64

    def test_file_from_base64_pipe_prefix(self) -> None:
        """传入 base64| 前缀的字符串，应剥离前缀后保留 base64 部分。"""
        f = File(f"base64|{_SAMPLE_B64}")
        assert f.value == _SAMPLE_B64

    def test_file_from_base64_with_whitespace(self) -> None:
        """base64 字符串中的空白字符应被去除。"""
        padded = f"  {_SAMPLE_B64}  "
        # 作为 data URL 前缀输入以触发 base64 路径
        data_url = f"data:text/plain;base64,{padded}"
        f = File(data_url)
        assert f.value == _SAMPLE_B64.strip()

    # --- 不可变性 ---

    def test_file_is_frozen(self) -> None:
        """File 应为 frozen，赋值应抛出异常。"""
        f = File(_SAMPLE_B64)
        with pytest.raises(Exception):
            f.value = "new_value"  # type: ignore[misc]

    def test_file_is_content_subclass(self) -> None:
        """File 应是 Content 的子类。"""
        f = File(_SAMPLE_B64)
        assert isinstance(f, Content)

    def test_file_equality(self) -> None:
        """相同 base64 内容的 File 应相等。"""
        f1 = File(_SAMPLE_B64)
        f2 = File(_SAMPLE_B64)
        assert f1 == f2

    def test_file_inequality(self) -> None:
        """不同内容的 File 应不相等。"""
        f1 = File(BytesIO(b"aaa"))
        f2 = File(BytesIO(b"bbb"))
        assert f1 != f2

    def test_file_from_path_equals_from_bytesio(self, tmp_path: Path) -> None:
        """从相同二进制数据的路径与 BytesIO 构造的 File 应相等。"""
        file_path = tmp_path / "test.bin"
        file_path.write_bytes(_SAMPLE_BYTES)
        f_path = File(str(file_path))
        f_io = File(BytesIO(_SAMPLE_BYTES))
        assert f_path == f_io

    # --- 类型错误 ---

    def test_file_unsupported_type_raises(self) -> None:
        """传入不支持的类型应抛出 TypeError。"""
        with pytest.raises(TypeError):
            File(12345)  # type: ignore[arg-type]


class TestMixedContent:
    """Test cases for using different content types together."""

    def test_content_type_discrimination(self) -> None:
        """Test discriminating between content types."""
        b64_img = base64.b64encode(b"img").decode("utf-8")
        b64_aud = base64.b64encode(b"aud").decode("utf-8")
        contents = [
            Text(text="Hello"),
            Image(b64_img),
            Audio(b64_aud),
        ]

        assert isinstance(contents[0], Text)
        assert isinstance(contents[1], Image)
        assert isinstance(contents[2], Audio)

        assert all(isinstance(c, Content) for c in contents)

    def test_image_and_audio_are_file_subclasses(self) -> None:
        """Test that Image and Audio are also File subclasses."""
        b64 = base64.b64encode(b"data").decode("utf-8")
        assert isinstance(Image(b64), File)
        assert isinstance(Audio(b64), File)

    def test_content_list(self) -> None:
        """Test storing different content types in a list."""
        b64 = base64.b64encode(b"img").decode("utf-8")
        content_list = [
            Text(text="Text content"),
            Image(b64),
        ]
        assert len(content_list) == 2
        assert any(isinstance(c, Text) for c in content_list)
        assert any(isinstance(c, Image) for c in content_list)
