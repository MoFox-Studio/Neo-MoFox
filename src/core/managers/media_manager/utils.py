"""媒体处理纯函数工具。

提供 base64 清洗、MIME 类型识别、GIF 关键帧提取与哈希计算等无状态工具，
供 :class:`~src.core.managers.media_manager.engines.VLMEngine`、
:class:`~src.core.managers.media_manager.recognition.MediaRecognition` 等组件复用。
"""

from __future__ import annotations

import base64
import hashlib
import io

from src.core.utils.base64_helper import base64_decode_to_bytes
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


def extract_clean_base64(data: str) -> str:
    """提取纯净的 base64 数据（移除前缀和多余字符）。

    Args:
        data: 可能包含 ``data:`` URL 前缀或 ``base64|`` 前缀的字符串

    Returns:
        去除前缀、换行与空格后的纯净 base64 字符串
    """
    if data.startswith("data:"):
        if "base64," in data:
            data = data.split("base64,", 1)[1]
    elif data.startswith("base64|"):
        data = data[7:]

    data = data.replace("\n", "").replace("\r", "").replace(" ", "")
    return data


def extract_image_mime_type(data: str) -> str:
    """从 data URL 中提取图片 MIME 类型。

    Args:
        data: 可能包含 MIME 信息的 data URL 字符串

    Returns:
        形如 ``image/png`` 的 MIME 类型；无法识别时回退为 ``image/png``
    """
    if data.startswith("data:") and ";base64," in data:
        mime_type = data.split(";", 1)[0][len("data:") :].strip().lower()
        if mime_type.startswith("image/"):
            return mime_type
    return "image/png"


def extract_gif_key_frames(clean_base64: str, max_frames: int = 8) -> str | None:
    """从 GIF base64 数据中提取关键帧并横向拼接为单张 PNG。

    通过均匀采样最多 ``max_frames`` 帧，横向拼接为一张 PNG 图，
    兼容不支持 GIF 动图格式的 VLM 模型。

    Args:
        clean_base64: 纯净的 GIF base64 字符串（无 data URL 前缀）。
        max_frames: 最多提取的帧数，默认 8。

    Returns:
        拼接后 PNG 的 base64 字符串；提取失败返回 None。
    """
    try:
        from PIL import Image as PILImage
    except ImportError:
        logger.warning("Pillow 未安装，无法提取 GIF 关键帧")
        return None

    try:
        raw_bytes = base64_decode_to_bytes(clean_base64)
        gif = PILImage.open(io.BytesIO(raw_bytes))

        frames: list[PILImage.Image] = []
        try:
            while True:
                frame = gif.copy()
                if frame.mode not in ("RGB", "RGBA"):
                    frame = frame.convert("RGBA")
                frames.append(frame)
                gif.seek(gif.tell() + 1)
        except EOFError:
            pass

        if not frames:
            return None

        if len(frames) == 1:
            buf = io.BytesIO()
            frames[0].save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")

        if len(frames) > max_frames:
            step = len(frames) / max_frames
            sampled = [frames[int(i * step)] for i in range(max_frames)]
        else:
            sampled = frames

        max_h = max(f.height for f in sampled)
        total_w = sum(f.width for f in sampled)
        canvas = PILImage.new("RGBA", (total_w, max_h), (255, 255, 255, 255))

        x_off = 0
        for frame in sampled:
            canvas.paste(frame, (x_off, 0))
            x_off += frame.width

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

    except Exception as e:
        logger.warning(f"提取 GIF 关键帧失败: {e}")
        return None


def compute_hash(data: str) -> str:
    """计算 base64 数据的 SHA256 哈希值。

    先清洗掉前缀与空白字符，再对纯净 base64 字符串计算哈希，
    与 :func:`extract_clean_base64` 行为一致，确保跨调用哈希稳定。

    Args:
        data: 待哈希的数据（可能含 data URL 前缀）

    Returns:
        64 位十六进制哈希字符串
    """
    clean_data = extract_clean_base64(data)
    return hashlib.sha256(clean_data.encode()).hexdigest()


def compute_media_hash(data: str) -> str:
    """计算媒体数据的哈希值（即 Images 表的 image_id）。

    与内部识别流程使用相同的哈希算法，确保哈希值可在 Images 表中回查。
    供外部模块（如 StreamManager 序列化入库时）在剔除 base64 ``data`` 前
    计算并保留 image_id，避免 data 被丢弃后无法按哈希找回图片信息。

    Args:
        data: 待哈希的数据（base64 字符串）

    Returns:
        十六进制哈希字符串
    """
    return compute_hash(data)
