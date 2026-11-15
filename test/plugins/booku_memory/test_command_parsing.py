"""booku_memory memory_command 参数解析回归测试。

覆盖 issue #154 的三类问题：
- 含空格的标题/正文被静默截成第一个词；
- 反斜杠被 shlex 当转义符导致 Windows 路径写坏；
- 不成对引号导致 shlex.split 抛异常、整条命令失败。
"""

from __future__ import annotations

import pytest

from plugins.booku_memory.agent.tools import _parse_segment, _tokenize


@pytest.mark.parametrize(
    ("segment", "key", "expected"),
    [
        # issue #154 表格中的真实截断案例
        (
            (
                "create -title 周边店里出现过印有 MORNYE 的联动立牌 "
                "-content 群里 [1783582416] 落. 提到「官谷」，指官方出品的周边……"
            ),
            "title",
            "周边店里出现过印有 MORNYE 的联动立牌",
        ),
        (
            "create -title 飞行雪绒 -content 飞行雪绒（QQ 981575562）……",
            "content",
            "飞行雪绒（QQ 981575562）……",
        ),
        (
            "create -title 缓存 -content 原图会缓存在 data\\media_cache\\images 目录下",
            "content",
            "原图会缓存在 data\\media_cache\\images 目录下",
        ),
        # 正文含不成对单引号，不应抛 No closing quotation
        (
            "create -title 标题 -content don't stop me now，继续记录",
            "content",
            "don't stop me now，继续记录",
        ),
        # 引号包裹的值仍应剥引号
        (
            'search -triple_tags "朋友,同学|学校,班级|陌生人,路人" -topn 5',
            "triple_tags",
            "朋友,同学|学校,班级|陌生人,路人",
        ),
        (
            'search -query "关键词 多词"',
            "query",
            "关键词 多词",
        ),
        # 值收集应止于下一个 -key，不吞并后续参数
        (
            "search -query 群里 复盘 -topn 50",
            "query",
            "群里 复盘",
        ),
        # 全角空格只影响切词边界，值区间应保留原文
        (
            "create -title 标题　正文 -content 内容",
            "title",
            "标题　正文",
        ),
    ],
)
def test_parse_segment_keeps_full_values(
    segment: str,
    key: str,
    expected: str,
) -> None:
    """含空格/引号/反斜杠的参数值必须完整保留。"""

    operation, options = _parse_segment(segment)

    assert operation == segment.split()[0].lower()
    assert options[key] == [expected]


def test_parse_segment_does_not_swallow_following_options() -> None:
    """值止于下一个 -key，后续参数必须各自独立成键。"""

    operation, options = _parse_segment(
        "create -title 多词 标题 -content 多词 正文 "
        "-core_tags a -diffusion_tags b -opposing_tags c"
    )

    assert operation == "create"
    assert options["title"] == ["多词 标题"]
    assert options["content"] == ["多词 正文"]
    assert options["core_tags"] == ["a"]
    assert options["diffusion_tags"] == ["b"]
    assert options["opposing_tags"] == ["c"]


def test_parse_segment_repeated_keys_keep_separate_values() -> None:
    """同 key 多次出现仍各自成值。"""

    _, options = _parse_segment("read -id a -id b")

    assert options["id"] == ["a", "b"]


def test_parse_segment_flag_option_defaults_to_true() -> None:
    """无值开关仍解析为 true。"""

    _, options = _parse_segment("delete -id m2 -hard")

    assert options["hard"] == ["true"]


def test_parse_segment_flags_between_values_are_not_merged() -> None:
    """紧随 -key 的下一个 -key 不会被吃进值里。"""

    _, options = _parse_segment("delete -id m2 -hard -id m3")

    assert options["id"] == ["m2", "m3"]
    assert options["hard"] == ["true"]


def test_parse_segment_operation_is_lowercased() -> None:
    """操作名应统一转小写。"""

    operation, _ = _parse_segment("HELP")

    assert operation == "help"


def test_parse_segment_empty_segment_raises() -> None:
    """空白命令段应抛 ValueError。"""

    with pytest.raises(ValueError, match="空命令段"):
        _parse_segment("   ")


def test_tokenize_strips_paired_quotes_and_keeps_spans() -> None:
    """成对引号应剥内容且区间覆盖引号本身，便于还原原始值。"""

    tokens = _tokenize('-title "a b"')

    assert tokens == [("-title", 0, 6), ("a b", 7, 12)]


def test_tokenize_treats_unpaired_quote_as_plain_char() -> None:
    """不成对引号按普通字符处理，不抛异常。"""

    tokens = _tokenize("don't")

    assert tokens == [("don't", 0, 5)]


def test_tokenize_keeps_backslash_verbatim() -> None:
    """反斜杠不做转义，Windows 路径原样保留。"""

    tokens = _tokenize("data\\media_cache\\images")

    assert tokens == [("data\\media_cache\\images", 0, 23)]
