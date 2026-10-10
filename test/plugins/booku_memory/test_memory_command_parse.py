"""``memory_command`` 参数解析的测试。

覆盖值里带空格、引号以及以 ``-`` 开头的字面值等情况：

- 值可以跨词，标题与正文里的空格要原样保留；
- 成对引号剥离，引号里包着的 ``-xxx`` 是字面值，不能当成下一个选项键；
- 词内撇号（``don't``）按普通字符处理，不能吞掉后面的内容；
- 显式空引号（``""``）必须解析成空字符串，而不是 ``true``。
"""

from __future__ import annotations

import pytest

from plugins.booku_memory.agent.tools import _parse_segment, _tokenize


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 基本：值是一个词
        ("add -content 标题 -tag a,b", {"content": ["标题"], "tag": ["a,b"]}),
        # 值跨词：标题和正文里的空格要保留
        ("add -content 我的 标题", {"content": ["我的 标题"]}),
        (
            "create -content 混合 值 with a - 连字符",
            {"content": ["混合 值 with a - 连字符"]},
        ),
        # 单独的 "-"（长度 1）不是选项键
        ("add -content a - b", {"content": ["a - b"]}),
        # 双引号包裹：成对引号剥离，引号内的空格不切词
        ('add -content "带 空格 的 标题"', {"content": ["带 空格 的 标题"]}),
        # 单引号同理
        (
            "add -content '单引号 内容' -id 12",
            {"content": ["单引号 内容"], "id": ["12"]},
        ),
        # 引号里的 -xxx 是字面值，不能当成下一个选项
        ('add -content "版本 -beta 测试"', {"content": ["版本 -beta 测试"]}),
        # 词内撇号按普通字符处理，不吞后面的内容
        ("add -content don't use 'foo' here", {"content": ["don't use foo here"]}),
        # 混合值：引号部分剥离，未加引号部分原样保留
        ('add -content hello "world"', {"content": ["hello world"]}),
        # 显式空引号是空字符串，不是 true
        ('add -content ""', {"content": [""]}),
        ('add -content "" -tag x', {"content": [""], "tag": ["x"]}),
        ('delete -hard ""', {"hard": [""]}),
        # 完全不给值才是 true
        ("add -content", {"content": ["true"]}),
    ],
)
def test_parse_segment_options(command: str, expected: dict[str, list[str]]) -> None:
    """参数解析结果应与预期一致。"""

    _, options = _parse_segment(command)
    assert options == expected


def test_operation_is_normalized() -> None:
    """操作名应统一成小写。"""

    operation, _ = _parse_segment("ADD -content x")
    assert operation == "add"


def test_repeated_option_collects_all_values() -> None:
    """同一个选项出现多次时应全部保留。"""

    _, options = _parse_segment("search -tag a -tag b")
    assert options["tag"] == ["a", "b"]


def test_empty_segment_raises() -> None:
    """空命令段应报错。"""

    with pytest.raises(ValueError):
        _parse_segment("   ")


def test_tokenize_marks_quoted_tokens() -> None:
    """tokenizer 应保留位置区间，并标记该词是否由引号包裹。"""

    tokens = _tokenize('add -content "a b"')
    assert [text for text, *_ in tokens] == ["add", "-content", "a b"]
    # 引号包裹的 token 带 quoted 标记，且区间覆盖引号本身
    assert tokens[-1][3] is True
    assert 'add -content "a b"'[tokens[-1][1] : tokens[-1][2]] == '"a b"'


def test_tokenize_keeps_empty_quoted_token() -> None:
    """空引号仍然要产生一个 token，否则会被误判成“没有给值”。"""

    tokens = _tokenize('add -content ""')
    assert tokens[-1][0] == ""
    assert tokens[-1][3] is True
