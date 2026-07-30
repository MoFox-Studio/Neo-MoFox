"""内置识别引擎组件。

提供 VLM（图片/表情包）与 ASR（语音）两套识别引擎，作为
:class:`~src.core.managers.media_manager.event_handlers.MediaEventHandlers`
未拦截时的兜底实现。视频识别无内置引擎。
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.app.plugin_system.api.llm_api import create_llm_request
from src.core.managers.media_manager.config import MediaConfig
from src.core.managers.media_manager.utils import (
    extract_clean_base64,
    extract_gif_key_frames,
    extract_image_mime_type,
)
from src.core.prompt import get_prompt_manager
from src.core.utils.base64_helper import base64_decode_to_bytes
from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text, Image
from src.kernel.llm.model_client.registry import ModelClientRegistry
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class VLMEngine:
    """VLM 图片/表情包识别引擎。

    Args:
        config: 提供模型集与可用性的配置组件
    """

    def __init__(self, config: MediaConfig) -> None:
        self._config = config

    @property
    def is_available(self) -> bool:
        """VLM 模型是否可用。"""
        return self._config.vlm_available and self._config.vlm_model_set is not None

    async def recognize(self, base64_data: str, media_type: str) -> str | None:
        """使用 VLM 识别单个媒体。

        Args:
            base64_data: base64 编码的媒体数据
            media_type: 媒体类型（image 或 emoji）

        Returns:
            识别结果文本，失败返回 None
        """
        try:
            if not self._config.vlm_model_set:
                logger.debug("VLM 模型不可用")
                return None

            context_manager = LLMContextManager()
            request = create_llm_request(
                self._config.vlm_model_set,
                "image_recognition",
                context_manager=context_manager,
            )

            prompt_manager = get_prompt_manager()
            if media_type == "emoji":
                template = prompt_manager.get_template("media.emoji_recognition")
            else:
                template = prompt_manager.get_template("media.image_recognition")

            prompt = ""
            if template:
                prompt = await template.build()

            clean_base64 = extract_clean_base64(base64_data)
            mime_type = extract_image_mime_type(base64_data)

            if mime_type == "image/gif":
                frames_b64 = extract_gif_key_frames(clean_base64)
                if frames_b64:
                    image_value = f"data:image/png;base64,{frames_b64}"
                    gif_note = (
                        "这是一张GIF表情包的关键帧横向拼接图，"
                        "从左到右依次为动画的各帧画面，请综合理解其动态内容。"
                    )
                    prompt = f"{prompt}\n\n{gif_note}"
                else:
                    image_value = f"data:image/png;base64,{clean_base64}"
            else:
                image_value = f"data:{mime_type};base64,{clean_base64}"

            request.add_payload(
                LLMPayload(ROLE.USER, [Text(prompt), Image(image_value)])
            )
            response = await request.send(stream=False)
            await response

            description = response.message.strip() if response.message else ""

            if len(description) > 100:
                description = description[:97] + "..."

            return description if description else None

        except Exception as e:
            logger.error(f"VLM 识别失败: {e}", exc_info=True)
            return None


class ASREngine:
    """ASR 语音识别引擎。

    Args:
        config: 提供模型集与可用性的配置组件
    """

    def __init__(self, config: MediaConfig) -> None:
        self._config = config

    @property
    def is_available(self) -> bool:
        """ASR 模型是否可用。"""
        return self._config.asr_available and self._config.asr_model_set is not None

    async def recognize(self, audio_base64: str) -> str | None:
        """调用 ASR 客户端执行语音转文字。

        Args:
            audio_base64: base64 编码的 WAV 音频数据。

        Returns:
            识别出的文字，失败返回 None。
        """
        try:
            registry = ModelClientRegistry()
            model_set = self._config.asr_model_set
            if not isinstance(model_set, list) or not model_set:
                logger.debug("ASR model_set 中无可用模型")
                return None

            model_entry: dict[str, Any] = model_set[0]
            client = registry.get_asr_client_for_model(model_entry)
            model_name = (
                model_entry.get("model_identifier")
                if isinstance(model_entry, dict)
                else str(model_entry)
            )

            clean_b64 = extract_clean_base64(audio_base64)
            audio_bytes = await asyncio.to_thread(
                base64_decode_to_bytes,
                clean_b64,
            )

            text = await client.create_transcription(
                model_name=model_name,
                audio_bytes=audio_bytes,
                request_name="voice_recognition",
                model_set=model_entry,
            )
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"ASR 请求失败: {e}", exc_info=True)
            return None
