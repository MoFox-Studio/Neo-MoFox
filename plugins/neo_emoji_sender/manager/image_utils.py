"""图像预处理与 VLM 标注辅助函数。"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from src.core.config import get_core_config
from src.kernel.logger import get_logger

logger = get_logger("neo_emoji_sender.image")

try:
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover - 运行环境缺 Pillow 时给出明确错误
    PILImage = None


def base64_encode(data: bytes) -> str:
    """把字节编码为 base64 字符串。

    Args:
        data: 原始字节。

    Returns:
        base64 文本。
    """
    return base64.b64encode(data).decode("ascii")


def compress_image_for_vlm(
    image_bytes: bytes,
    mime: str,
    *,
    max_size_mb: float = 5.0,
) -> tuple[bytes, str, bool]:
    """压缩图片用于 VLM 识别。

    静态图逐步降低质量与分辨率；GIF 均匀采样最多 6 帧拼成网格后转 JPEG。

    Args:
        image_bytes: 原始图片字节。
        mime: 原始 MIME。
        max_size_mb: 压缩目标上限（MB）。

    Returns:
        (用于 VLM 的字节, MIME, 是否为 GIF 拼图)。

    Raises:
        RuntimeError: Pillow 未安装或图片无法处理时抛出。
    """
    if PILImage is None:
        raise RuntimeError("PIL 未安装。请运行: uv add pillow")

    max_bytes = int(max_size_mb * 1024 * 1024)

    if mime == "image/gif":
        try:
            image = PILImage.open(io.BytesIO(image_bytes))
            total_frames = int(getattr(image, "n_frames", 1))
            max_frames = 6
            if total_frames <= max_frames:
                indices = list(range(total_frames))
            else:
                step = total_frames / max_frames
                indices = [int(index * step) for index in range(max_frames)]

            frames: list[Any] = []
            for index in indices:
                try:
                    image.seek(index)
                    frames.append(image.convert("RGB").copy())
                except EOFError:
                    break
            if not frames:
                raise RuntimeError("无法提取 GIF 帧")

            cols = min(3, len(frames))
            rows = (len(frames) + cols - 1) // cols
            width, height = frames[0].size
            grid = PILImage.new("RGB", (width * cols, height * rows), (255, 255, 255))
            for index, frame in enumerate(frames):
                grid.paste(frame.resize((width, height)), ((index % cols) * width, (index // cols) * height))

            output = io.BytesIO()
            grid.save(output, format="JPEG", quality=80)
            result = output.getvalue()
            if len(result) > max_bytes:
                scale = (max_bytes / len(result)) ** 0.5
                grid = grid.resize(
                    (max(1, int(grid.width * scale)), max(1, int(grid.height * scale))),
                    PILImage.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                grid.save(output, format="JPEG", quality=75)
                result = output.getvalue()
            return result, "image/jpeg", True
        except Exception as e:
            raise RuntimeError(f"GIF 处理失败: {e}") from e

    if len(image_bytes) <= max_bytes:
        return image_bytes, mime, False

    try:
        image = PILImage.open(io.BytesIO(image_bytes))
    except Exception as e:
        raise RuntimeError(f"无法打开图片: {e}") from e

    output_format = {
        "image/png": "JPEG",
        "image/jpeg": "JPEG",
        "image/jpg": "JPEG",
        "image/webp": "WEBP",
    }.get(mime, "JPEG")
    output_mime = "image/jpeg" if output_format == "JPEG" else mime

    quality = 85
    scale = 1.0
    compressed = b""
    while quality >= 30 or scale > 0.4:
        output = io.BytesIO()
        try:
            target = image.convert("RGB") if output_format == "JPEG" else image
            if scale < 1.0:
                target = target.resize(
                    (max(1, int(target.width * scale)), max(1, int(target.height * scale))),
                    PILImage.Resampling.LANCZOS,
                )
            target.save(output, format=output_format, quality=quality)
            compressed = output.getvalue()
            if len(compressed) <= max_bytes:
                return compressed, output_mime, False
        except Exception as e:
            raise RuntimeError(f"压缩图片失败: {e}") from e
        if quality > 30:
            quality = max(30, quality - 10)
        else:
            scale = round(scale - 0.1, 1)
    return compressed, output_mime, False


def build_persona_prompt() -> str:
    """从主配置人格字段组装 persona 指令片段。

    Returns:
        人格描述文本。
    """
    persona = get_core_config().personality
    alias = "、".join(persona.alias_names) if persona.alias_names else ""
    safety = "\n".join(f"- {item}" for item in (persona.safety_guidelines or []))
    negatives = "\n".join(f"- {item}" for item in (persona.negative_behaviors or []))
    parts = [
        f"你的昵称：{persona.nickname}",
        f"你的别名：{alias}" if alias else "",
        f"核心人格：{persona.personality_core}",
        f"人格侧面：{persona.personality_side}" if persona.personality_side else "",
        f"身份：{persona.identity}",
        f"背景故事（不应主动复述）：{persona.background_story}" if persona.background_story else "",
        f"回复风格：{persona.reply_style}",
        "安全与互动底线：\n" + safety if safety else "",
        "禁止行为：\n" + negatives if negatives else "",
    ]
    return "\n".join(item for item in parts if item)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON object。

    Args:
        text: 模型原始输出。

    Returns:
        解析成功时返回 dict，否则返回 None。
    """
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except Exception:
        return None
    return value if isinstance(value, dict) else None


__all__ = [
    "base64_encode",
    "compress_image_for_vlm",
    "build_persona_prompt",
    "extract_json_object",
]
