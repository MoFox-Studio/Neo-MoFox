"""Tests for payload/content.py."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from src.kernel.llm.payload.content import Action, Audio, Content, Image, Text


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

    def test_image_creation_with_file_path(self) -> None:
        """Test creating Image with file path."""
        image = Image(value="pic.jpg")
        assert image.value == "pic.jpg"

    def test_image_creation_with_data_url(self) -> None:
        """Test creating Image with data URL."""
        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        image = Image(value=data_url)
        assert image.value == data_url

    def test_image_creation_with_base64_prefix(self) -> None:
        """Test creating Image with base64| prefix format."""
        image = Image(value="base64|iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
        assert image.value == "base64|iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    def test_image_is_content_subclass(self) -> None:
        """Test that Image is a Content subclass."""
        assert isinstance(Image(value="pic.jpg"), Content)

    def test_image_is_frozen(self) -> None:
        """Test that Image is frozen."""
        image = Image(value="pic.jpg")
        with pytest.raises(Exception):
            image.value = "modified"

    def test_image_has_slots(self) -> None:
        """Test that Image uses slots."""
        image = Image(value="pic.jpg")
        with pytest.raises(AttributeError):
            image.__dict__

    def test_image_equality(self) -> None:
        """Test Image equality."""
        image1 = Image(value="pic.jpg")
        image2 = Image(value="pic.jpg")
        image3 = Image(value="other.png")
        assert image1 == image2
        assert image1 != image3

    def test_image_data_url_detection(self) -> None:
        """Test data URL format detection."""
        # These tests are for the _is_data_url function in openai_client.py
        # Here we just verify the value is stored correctly
        data_url = "data:image/png;base64,ABC123"
        image = Image(value=data_url)
        assert image.value == data_url

    def test_image_non_data_url(self) -> None:
        """Test non-data URL values."""
        image = Image(value="path/to/image.jpg")
        assert not image.value.startswith("data:")


class TestAudio:
    """Test cases for Audio content."""

    def test_audio_creation(self) -> None:
        """Test creating Audio content."""
        audio = Audio(value="audio.mp3")
        assert audio.value == "audio.mp3"

    def test_audio_creation_with_data_url(self) -> None:
        """Test creating Audio with data URL."""
        data_url = "data:audio/mp3;base64,//uQRAAAAWMSLwUIYAAsYkXgoQwAEaYLWfkWgAI0wWs/ItAAAG84AA0WAgAAAAAAabwA"
        audio = Audio(value=data_url)
        assert audio.value == data_url

    def test_audio_is_content_subclass(self) -> None:
        """Test that Audio is a Content subclass."""
        assert isinstance(Audio(value="test.mp3"), Content)

    def test_audio_is_frozen(self) -> None:
        """Test that Audio is frozen."""
        audio = Audio(value="test.mp3")
        with pytest.raises(Exception):
            audio.value = "modified"

    def test_audio_has_slots(self) -> None:
        """Test that Audio uses slots."""
        audio = Audio(value="test.mp3")
        with pytest.raises(AttributeError):
            audio.__dict__

    def test_audio_equality(self) -> None:
        """Test Audio equality."""
        audio1 = Audio(value="audio.mp3")
        audio2 = Audio(value="audio.mp3")
        audio3 = Audio(value="other.wav")
        assert audio1 == audio2
        assert audio1 != audio3


class TestAction:
    """Test cases for Action content."""

    def test_action_creation(self) -> None:
        """Test creating Action content."""
        action = Action(action=Mock)
        assert action.action == Mock

    def test_action_is_content_subclass(self) -> None:
        """Test that Action is a Content subclass."""
        assert isinstance(Action(action=Mock), Content)

    def test_action_is_frozen(self) -> None:
        """Test that Action is frozen."""
        action = Action(action=Mock)
        with pytest.raises(Exception):
            action.action = MagicMock

    def test_action_has_slots(self) -> None:
        """Test that Action uses slots."""
        action = Action(action=Mock)
        with pytest.raises(AttributeError):
            action.__dict__

    def test_action_equality(self) -> None:
        """Test Action equality."""
        action1 = Action(action=Mock)
        action2 = Action(action=Mock)
        action3 = Action(action=MagicMock)
        assert action1 == action2
        assert action1 != action3

    def test_action_with_class(self) -> None:
        """Test Action with actual class."""

        class MyAction:
            pass

        action = Action(action=MyAction)
        assert action.action == MyAction


class TestMixedContent:
    """Test cases for using different content types together."""

    def test_content_type_discrimination(self) -> None:
        """Test discriminating between content types."""
        contents = [
            Text(text="Hello"),
            Image(value="pic.jpg"),
            Audio(value="audio.mp3"),
            Action(action=Mock),
        ]

        assert isinstance(contents[0], Text)
        assert isinstance(contents[1], Image)
        assert isinstance(contents[2], Audio)
        assert isinstance(contents[3], Action)

        assert all(isinstance(c, Content) for c in contents)

    def test_content_list(self) -> None:
        """Test storing different content types in a list."""
        content_list = [
            Text(text="Text content"),
            Image(value="image.jpg"),
        ]
        assert len(content_list) == 2
        assert any(isinstance(c, Text) for c in content_list)
        assert any(isinstance(c, Image) for c in content_list)
