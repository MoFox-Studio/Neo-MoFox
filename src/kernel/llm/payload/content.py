"""LLM payload 内容类型定义。

定义了用于构建 LLM 消息的各类内容类型：Content（基类）、File、Text、Image、Audio。
File 支持文件路径、文件对象、base64 字符串三种输入，并在构造时统一规范化为纯 base64 编码字符串。
Image 和 Audio 均继承自 File，共享相同的规范化逻辑，并在语义上区分媒体类型。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import RawIOBase, BufferedIOBase
from os import PathLike
from pathlib import Path
from typing import BinaryIO, Union


@dataclass(frozen=True, slots=True)
class Content:
    """Payload content 基类。"""


def _normalize_file_to_base64(
    source: Union[str, "PathLike[str]", BinaryIO],
) -> str:
    """将文件路径、文件对象或 base64 字符串统一规范化为纯 base64 编码字符串。

    Args:
        source: 支持以下三种形式：
            - 文件路径（``str`` 或 ``Path``）：读取文件内容并编码为 base64。
            - 文件对象（``BinaryIO``，如 ``BytesIO``、``open(..., "rb")``）：读取内容并编码。
            - base64 字符串：自动去除 ``data:...;base64,`` 或 ``base64|`` 前缀后原样返回。

    Returns:
        纯净的 base64 编码字符串（不含任何前缀）。

    Raises:
        ValueError: 当传入的字符串既不是有效的文件路径也无法识别为 base64 格式时。
        TypeError: 当 ``source`` 类型不受支持时。
    """
    # 文件对象
    if isinstance(source, (RawIOBase, BufferedIOBase)) or hasattr(source, "read"):
        raw: bytes = source.read()  # type: ignore[union-attr]
        return base64.b64encode(raw).decode("utf-8")

    # 字符串或路径
    if isinstance(source, (str, PathLike)):
        s = str(source)

        # data URL 前缀：data:...;base64,<payload>
        if s.startswith("data:") and "base64," in s:
            return s.split("base64,", 1)[1].strip()

        # base64| 前缀
        if s.startswith("base64|"):
            return s[len("base64|"):].strip()

        # 尝试作为文件路径处理
        path = Path(s)
        if path.exists() and path.is_file():
            return base64.b64encode(path.read_bytes()).decode("utf-8")

        # 最后尝试验证是否为纯 base64 字符串
        try:
            cleaned = s.replace("\n", "").replace("\r", "").replace(" ", "")
            base64.b64decode(cleaned, validate=True)
            return cleaned
        except Exception:
            raise ValueError(
                f"无法识别的 File 输入：既不是有效的文件路径，也不是合法的 base64 字符串。"
                f"收到：{s!r}"
            ) from None

    raise TypeError(
        f"File 不支持的输入类型：{type(source).__name__}。"
        f"请传入文件路径（str/Path）、文件对象（BinaryIO）或 base64 字符串。"
    )


class File(Content):
    """文件内容。

    接受三种输入，并在构造时统一规范化为纯 base64 编码字符串存储于 ``value``：

    - **文件路径**（``str`` 或 ``Path``）：读取文件二进制内容并 base64 编码。
    - **文件对象**（``BinaryIO``，如 ``BytesIO``、``open(..., "rb")``）：读取并编码。
    - **base64 字符串**：自动剥离 ``data:...;base64,`` 或 ``base64|`` 前缀后存储。

    示例::

        # 文件路径
        f1 = File("report.pdf")

        # 文件对象
        from io import BytesIO
        f2 = File(BytesIO(b"hello"))

        # 纯 base64 字符串
        f3 = File("aGVsbG8=")

        # data URL
        f4 = File("data:application/pdf;base64,aGVsbG8=")

    ``value`` 始终是纯净的 base64 字符串，可直接用于传输或存储。
    """

    __slots__ = ("value",)

    value: str

    def __init__(
        self,
        source: Union[str, "PathLike[str]", BinaryIO],
    ) -> None:
        """构造 File 实例，将 source 规范化为 base64 字符串后存入 value。

        Args:
            source: 文件路径（str/Path）、文件对象（BinaryIO）或 base64 字符串。
        """
        normalized = _normalize_file_to_base64(source)
        object.__setattr__(self, "value", normalized)

    def __setattr__(self, name: str, value: object) -> None:
        """禁止属性修改，保持不可变语义。"""
        raise AttributeError("File 实例是不可变的，不允许修改属性。")

    def __delattr__(self, name: str) -> None:
        """禁止属性删除，保持不可变语义。"""
        raise AttributeError("File 实例是不可变的，不允许删除属性。")

    def __eq__(self, other: object) -> bool:
        """按 value 比较相等性。"""
        if isinstance(other, File):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        """基于 value 计算哈希值。"""
        return hash(self.value)

    def __repr__(self) -> str:
        """返回对象的字符串表示。"""
        preview = self.value[:16] + "..." if len(self.value) > 16 else self.value
        return f"File(value={preview!r})"


@dataclass(frozen=True, slots=True)
class Text(Content):
    """文本内容。"""

    text: str


class Image(File):
    """图片内容。

    继承自 :class:`File`，在构造时将输入统一规范化为纯 base64 字符串。

    支持与 :class:`File` 完全相同的三种输入形式：

    - **文件路径**（``str`` 或 ``Path``）：读取图片文件并 base64 编码。
    - **文件对象**（``BinaryIO``）：读取并编码。
    - **base64 / data URL / base64| 字符串**：剥离前缀后存储纯 base64。

    示例::

        img1 = Image("photo.jpg")                          # 文件路径
        img2 = Image(open("photo.jpg", "rb"))              # 文件对象
        img3 = Image("data:image/png;base64,iVBOR...")     # data URL
        img4 = Image("base64|iVBOR...")                    # base64| 前缀
        img5 = Image("iVBOR...")                           # 纯 base64 字符串
    """

    def __repr__(self) -> str:
        """返回对象的字符串表示。"""
        preview = self.value[:16] + "..." if len(self.value) > 16 else self.value
        return f"Image(value={preview!r})"


class Audio(File):
    """音频内容。

    继承自 :class:`File`，在构造时将输入统一规范化为纯 base64 字符串。

    支持与 :class:`File` 完全相同的三种输入形式：

    - **文件路径**（``str`` 或 ``Path``）：读取音频文件并 base64 编码。
    - **文件对象**（``BinaryIO``）：读取并编码。
    - **base64 / data URL / base64| 字符串**：剥离前缀后存储纯 base64。

    示例::

        a1 = Audio("speech.mp3")                           # 文件路径
        a2 = Audio(open("speech.mp3", "rb"))               # 文件对象
        a3 = Audio("data:audio/mp3;base64,//uQR...")       # data URL
        a4 = Audio("base64|//uQR...")                      # base64| 前缀
        a5 = Audio("//uQR...")                             # 纯 base64 字符串
    """

    def __repr__(self) -> str:
        """返回对象的字符串表示。"""
        preview = self.value[:16] + "..." if len(self.value) > 16 else self.value
        return f"Audio(value={preview!r})"
