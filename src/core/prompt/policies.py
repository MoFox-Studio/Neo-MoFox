"""
Prompt 渲染策略模块

提供各种渲染策略工厂函数，用于在提示词模板中控制占位符的渲染行为。

用法示例:
    from src.core.prompt.policies import optional, trim, header, wrap, join_blocks, min_len

    # 基础策略
    trim_policy = trim()
    print(trim_policy("  hello  "))  # "hello"

    # 策略链式调用
    policy = trim().then(min_len(5)).then(header("# 标题"))
    print(policy("hello"))  # "" (长度小于5)
    print(policy("hello world"))  # "# 标题\\nhello world"

    # 空值处理
    opt = optional("默认值")
    print(opt(""))  # "默认值"
    print(opt("有效值"))  # "有效值"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


def _is_effectively_empty(v: Any) -> bool:
    """检查值是否为空

    Args:
        v: 要检查的值

    Returns:
        bool: 如果值为 None、空字符串、空列表等，返回 True
    """
    if v is None:
        return True
    if isinstance(v, str):
        return len(v.strip()) == 0
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) == 0
    return False


@dataclass(frozen=True)
class RenderPolicy:
    """渲染策略类

    用于控制提示词模板中占位符的渲染行为。
    支持策略链式调用，可以通过 then 方法串联多个策略。

    Attributes:
        fn: 渲染函数，接收任意值并返回渲染后的字符串
    """

    fn: Callable[[Any], str]

    def __call__(self, value: Any) -> str:
        """执行渲染策略

        Args:
            value: 要渲染的值

        Returns:
            str: 渲染后的字符串
        """
        return self.fn(value)

    def then(self, other: RenderPolicy) -> RenderPolicy:
        """串联两个渲染策略

        先执行当前策略，再将结果传递给下一个策略。

        Args:
            other: 下一个要执行的渲染策略

        Returns:
            RenderPolicy: 组合后的新策略
        """
        return RenderPolicy(lambda v: other(self(v)))


def optional(empty: str = "") -> RenderPolicy:
    """可选值策略：空值时返回默认值

    Args:
        empty: 空值时返回的默认字符串，默认为空字符串

    Returns:
        RenderPolicy: 可选值渲染策略

    Examples:
        >>> policy = optional("未提供")
        >>> policy("")  # "未提供"
        >>> policy(None)  # "未提供"
        >>> policy("有效值")  # "有效值"
    """
    return RenderPolicy(lambda v: empty if _is_effectively_empty(v) else str(v))


def trim() -> RenderPolicy:
    """去除首尾空格策略

    Returns:
        RenderPolicy: 去除首尾空格的渲染策略

    Examples:
        >>> policy = trim()
        >>> policy("  hello  ")  # "hello"
        >>> policy(None)  # ""
    """
    return RenderPolicy(lambda v: "" if v is None else str(v).strip())


def header(title: str, sep: str = "\n") -> RenderPolicy:
    """添加标题策略

    如果值为空，返回空字符串；否则添加标题前缀。

    Args:
        title: 标题文本
        sep: 标题与内容之间的分隔符，默认为换行符

    Returns:
        RenderPolicy: 添加标题的渲染策略

    Examples:
        >>> policy = header("# 知识库内容")
        >>> policy("")  # ""
        >>> policy("这是内容")  # "# 知识库内容\\n这是内容"
    """
    def _fn(v: Any) -> str:
        if _is_effectively_empty(v):
            return ""
        s = str(v)
        if _is_effectively_empty(s):
            return ""
        return f"{title}{sep}{s}"

    return RenderPolicy(_fn)


def wrap(prefix: str = "", suffix: str = "") -> RenderPolicy:
    """包裹策略

    如果值为空，返回空字符串；否则在前后添加指定文本。

    Args:
        prefix: 前缀文本
        suffix: 后缀文本

    Returns:
        RenderPolicy: 包裹的渲染策略

    Examples:
        >>> policy = wrap("```json\\n", "\\n```")
        >>> policy("")  # ""
        >>> policy('{"key": "value"}')  # "```json\\n{"key": "value"}\\n```"
    """
    def _fn(v: Any) -> str:
        s = str(v)
        if _is_effectively_empty(s):
            return ""
        return f"{prefix}{s}{suffix}"

    return RenderPolicy(_fn)


def join_blocks(block_sep: str = "\n\n") -> RenderPolicy:
    """连接块策略

    将列表/元组中的非空元素用指定分隔符连接。
    如果值不是列表类型或为空，则转换为字符串。

    Args:
        block_sep: 块之间的分隔符，默认为两个换行符

    Returns:
        RenderPolicy: 连接块的渲染策略

    Examples:
        >>> policy = join_blocks("\\n")
        >>> policy(["a", "", "b", "c"])  # "a\\nb\\nc"
        >>> policy([])  # ""
    """
    def _fn(v: Any) -> str:
        if _is_effectively_empty(v):
            return ""
        if isinstance(v, (list, tuple)):
            parts = [str(x).strip() for x in v if not _is_effectively_empty(x)]
            return block_sep.join(parts)
        return str(v)

    return RenderPolicy(_fn)


def min_len(n: int) -> RenderPolicy:
    """最小长度策略

    如果字符串长度（去除首尾空格后）小于指定值，返回空字符串。

    Args:
        n: 最小长度阈值

    Returns:
        RenderPolicy: 最小长度过滤的渲染策略

    Examples:
        >>> policy = min_len(5)
        >>> policy("hi")  # ""
        >>> policy("hello")  # "hello"
        >>> policy(None)  # ""
    """
    def _fn(v: Any) -> str:
        if v is None:
            return ""
        s = str(v)
        return "" if len(s.strip()) < n else s

    return RenderPolicy(_fn)
