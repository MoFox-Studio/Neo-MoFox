"""内嵌的 Google Avatar Frame Generator（四色圆环叠加）。

算法移植自开源项目 Google-Avatar-Frame-Generator
（https://github.com/2010384626/Google-Avatar-Frame-Generator），
保留其标定的接缝角度、圆环比例与间隔比例，内嵌运行，不访问任何网页服务。

- Pillow 角度从 3 点钟方向起算、顺时针增加；
- 四段颜色接缝位置取自原项目的标定参考图；
- 头像按圆形裁剪，居中缩放（LANCZOS）。
"""

from __future__ import annotations

import base64
import binascii
import io

from PIL import Image, ImageDraw, ImageOps

__all__ = ["apply_google_frame", "FrameError"]

#: Google 品牌四色。
COLORS: dict[str, tuple[int, int, int]] = {
    "red": (0xEA, 0x43, 0x35),
    "blue": (0x42, 0x85, 0xF4),
    "yellow": (0xFB, 0xBC, 0x05),
    "green": (0x34, 0xA8, 0x53),
}

#: 标定接缝角度（Pillow 角度：3 点钟起算，顺时针增加）。
SEAMS: dict[str, int] = {
    "yellow_red": 206,
    "red_blue": 314,
    "blue_green": 48,
    "green_yellow": 138,
}

#: 四段圆弧（颜色, 起始角, 结束角）。
SEGMENTS: list[tuple[str, int, int]] = [
    ("red", SEAMS["yellow_red"], SEAMS["red_blue"]),
    ("blue", SEAMS["red_blue"], SEAMS["blue_green"]),
    ("green", SEAMS["blue_green"], SEAMS["green_yellow"]),
    ("yellow", SEAMS["green_yellow"], SEAMS["yellow_red"]),
]

_RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


class FrameError(Exception):
    """圆环叠加失败。"""


def _hex_to_rgb(color: dict[str, str]) -> dict[str, tuple[int, int, int]]:
    """把十六进制颜色转换为 RGB 元组（保持与原项目 COLORS 结构对齐）。"""
    resolved: dict[str, tuple[int, int, int]] = {}
    for name, hex_value in color.items():
        raw = hex_value.lstrip("#")
        resolved[name] = (
            int(raw[0:2], 16),
            int(raw[2:4], 16),
            int(raw[4:6], 16),
        )
    return resolved


def _draw_ring_segments(
    draw: ImageDraw.ImageDraw,
    arc_box: tuple[int, int, int, int],
    colors: dict[str, tuple[int, int, int]],
) -> None:
    """按标定接缝角度绘制四段圆弧填充。"""
    for color_name, start_angle, end_angle in SEGMENTS:
        if end_angle < start_angle:
            draw.pieslice(arc_box, start=start_angle, end=360, fill=colors[color_name])
            draw.pieslice(arc_box, start=0, end=end_angle, fill=colors[color_name])
        else:
            draw.pieslice(arc_box, start=start_angle, end=end_angle, fill=colors[color_name])


def _calculate_layout(output_size: int, border_ratio: float, gap_ratio: float) -> tuple[int, int]:
    """按比例计算圆环宽度与间隔宽度（与原项目 calculate_layout 一致）。"""
    if output_size < 32:
        raise FrameError("输出尺寸必须至少 32 像素。")
    border_width = max(1, round(output_size * border_ratio))
    gap = max(1, round(output_size * gap_ratio))
    return border_width, gap


def apply_google_frame(
    image_bytes: bytes,
    *,
    output_size: int,
    border_ratio: float = 0.04,
    gap_ratio: float = 0.02,
    jpeg_quality: int = 90,
) -> bytes:
    """给一张图片叠加 Google 四色圆环，返回 JPEG 字节。

    流程与原项目 build_avatar_canvas 一致：
    1. 源图居中裁剪为正方形并缩放到头像直径；
    2. 圆形蒙版裁剪头像；
    3. 绘制四色圆环（外环），中心挖空；
    4. 圆环下方垫白色圆盘（间隔色）；
    5. 粘贴圆形头像。

    Args:
        image_bytes: 源图字节（PNG/JPEG/WebP 等 Pillow 可读格式）。
        output_size: 输出边长（正方形）。
        border_ratio: 圆环宽度比例。
        gap_ratio: 白色间隔比例。
        jpeg_quality: JPEG 压缩质量。

    Returns:
        JPEG 编码的成品图字节。

    Raises:
        FrameError: 源图无法解码或尺寸过小。
    """
    if not image_bytes:
        raise FrameError("源图字节为空。")
    try:
        source = Image.open(io.BytesIO(image_bytes))
        source.load()
    except Exception as e:
        raise FrameError(f"源图解码失败: {e}") from e

    colors = COLORS
    border_width, gap = _calculate_layout(output_size, border_ratio, gap_ratio)

    avatar_diameter = output_size - (border_width + gap) * 2
    if avatar_diameter < 16:
        raise FrameError(
            f"头像直径过小（{avatar_diameter}px）：输出尺寸 {output_size} 与圆环比例不匹配。"
        )

    avatar = ImageOps.fit(
        source.convert("RGBA"),
        (avatar_diameter, avatar_diameter),
        method=_RESAMPLE,
    )

    avatar_mask = Image.new("L", (avatar_diameter, avatar_diameter), 0)
    mask_draw = ImageDraw.Draw(avatar_mask)
    mask_draw.ellipse((0, 0, avatar_diameter - 1, avatar_diameter - 1), fill=255)

    circular_avatar = Image.new("RGBA", (avatar_diameter, avatar_diameter), (0, 0, 0, 0))
    circular_avatar.paste(avatar, (0, 0), avatar_mask)

    canvas = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
    ring_layer = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring_layer)

    outer_ring_box = (0, 0, output_size - 1, output_size - 1)
    _draw_ring_segments(ring_draw, outer_ring_box, colors)

    hole_margin = border_width
    ring_hole_box = (
        hole_margin,
        hole_margin,
        output_size - hole_margin - 1,
        output_size - hole_margin - 1,
    )
    ring_draw.ellipse(ring_hole_box, fill=(0, 0, 0, 0))

    canvas.alpha_composite(ring_layer)

    draw = ImageDraw.Draw(canvas)
    inner_disc_box = (
        border_width,
        border_width,
        output_size - border_width - 1,
        output_size - border_width - 1,
    )
    draw.ellipse(inner_disc_box, fill=(255, 255, 255, 255))

    avatar_offset = border_width + gap
    canvas.paste(circular_avatar, (avatar_offset, avatar_offset), circular_avatar)

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="JPEG", quality=jpeg_quality)
    return output.getvalue()


def decode_image_b64(data: str) -> bytes:
    """解码 base64 图片数据（兼容 data URL / base64| 前缀 / 裸 base64）。"""
    stripped = (data or "").strip()
    if stripped.startswith("data:") and "base64," in stripped:
        stripped = stripped.split("base64,", 1)[1].strip()
    elif stripped.startswith("base64|"):
        stripped = stripped[len("base64|"):].strip()
    try:
        return base64.b64decode(stripped)
    except (binascii.Error, ValueError) as e:
        raise FrameError(f"base64 图片解码失败: {e}") from e


def encode_image_b64(image_bytes: bytes) -> str:
    """把图片字节编码为裸 base64 字符串。"""
    return base64.b64encode(image_bytes).decode("ascii")
