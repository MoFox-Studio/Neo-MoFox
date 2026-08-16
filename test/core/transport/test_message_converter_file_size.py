"""测试 MessageConverter 对文件消息段（file）的大小渲染与解析。"""

from __future__ import annotations

from src.core.transport.message_receive.converter import (
    MessageConverter,
    _format_file_size,
)


def _parse_file(data: object) -> tuple[list[str], list[dict]]:
    """构造 _ParseResult 并调用 _handle_file，返回文本与媒体列表。"""
    from src.core.transport.message_receive.converter import _ParseResult

    parse_result = _ParseResult()
    MessageConverter._handle_file(data, parse_result)
    return parse_result.text_parts, parse_result.media


def test_format_file_size_bytes() -> None:
    """字节单位直接展示，无小数。"""
    assert _format_file_size(0) == "0B"
    assert _format_file_size(512) == "512B"


def test_format_file_size_units() -> None:
    """大于等于 1024 时按 1024 进制换算并保留一位小数。"""
    assert _format_file_size(1024) == "1.0KB"
    assert _format_file_size(5119) == "5.0KB"
    assert _format_file_size(83385540) == "79.5MB"
    assert _format_file_size(1073741824) == "1.0GB"


def test_format_file_size_numeric_string() -> None:
    """数字字符串应被解析为字节数。"""
    assert _format_file_size("3020") == "2.9KB"


def test_format_file_size_preset_text() -> None:
    """已是可读格式（如 OneBot 回声中 "1.7MB"）原样返回。"""
    assert _format_file_size("1.7MB") == "1.7MB"


def test_format_file_size_invalid() -> None:
    """None / 空串 / 非法值返回空串，不污染 LLM 文本。"""
    assert _format_file_size(None) == ""
    assert _format_file_size("") == ""
    assert _format_file_size("  ") == ""
    assert _format_file_size("abc") == ""
    assert _format_file_size(object()) == ""


def test_format_file_size_negative() -> None:
    """负数无法表示真实大小，返回空串。"""
    assert _format_file_size(-1) == ""
    assert _format_file_size("-1024") == ""


def test_format_file_size_thousands_separator() -> None:
    """千分位逗号字符串应被解析为字节数。"""
    assert _format_file_size("1,024") == "1.0KB"
    assert _format_file_size("83,385,540") == "79.5MB"


def test_format_file_size_preset_text_case_insensitive() -> None:
    """已是可读格式的字符串（含小写/二进制单位）原样返回。"""
    assert _format_file_size("1.7MB") == "1.7MB"
    assert _format_file_size("512kb") == "512kb"
    assert _format_file_size("1.0GiB") == "1.0GiB"


def test_handle_file_with_size() -> None:
    """带 size 的文件段应把可读大小渲染进文本占位符。"""
    text_parts, media = _parse_file(
        {"name": "今日日记.txt", "size": 3020, "id": "f1"}
    )
    assert text_parts == ["[文件:今日日记.txt(2.9KB)]"]
    assert media == [
        {"type": "file", "data": {"name": "今日日记.txt", "size": 3020, "id": "f1"}}
    ]


def test_handle_file_without_size() -> None:
    """无 size 时不追加大小括号，保持原占位符格式。"""
    text_parts, _media = _parse_file({"name": "a.txt"})
    assert text_parts == ["[文件:a.txt]"]


def test_handle_file_legacy_file_field() -> None:
    """兼容旧字段名 file（OneBot 用 file 字段承载文件名）。"""
    text_parts, media = _parse_file({"file": "a.mp4", "file_size": 36492772})
    assert text_parts == ["[文件:a.mp4(34.8MB)]"]
    assert media[0]["data"]["name"] == "a.mp4"
    assert media[0]["data"]["size"] == 36492772


def test_handle_file_size_preset_text() -> None:
    """file_size 已是可读文本（如 "1.7MB"）时直接展示。"""
    text_parts, _media = _parse_file({"file": "x.zip", "file_size": "1.7MB"})
    assert text_parts == ["[文件:x.zip(1.7MB)]"]


def test_handle_file_non_dict() -> None:
    """无法解析为 dict 时回退到占位符 [文件]，media 保留原始值。"""
    text_parts, media = _parse_file("not-a-dict")
    assert text_parts == ["[文件]"]
    assert media == [{"type": "file", "data": "not-a-dict"}]
