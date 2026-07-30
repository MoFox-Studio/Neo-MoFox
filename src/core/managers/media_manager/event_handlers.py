"""媒体识别事件链默认处理器。

注册 ``ON_MEDIA_RECOGNIZE`` 事件的默认回调（VLM/ASR/VIDEO 三类），以
``priority=0`` 最低优先级订阅，第三方插件可用更高优先级先拦截改写。

视频识别无内置引擎，对应回调仅作占位（直接 PASS），由第三方插件订阅事件
回写 description。
"""

from __future__ import annotations

from typing import Any

from src.core.components.types import EventType, MediaEngine
from src.core.managers.media_manager.engines import ASREngine, VLMEngine
from src.kernel.event import EventDecision, get_event_bus
from src.kernel.logger import get_logger

logger = get_logger("media_manager")


class MediaEventHandlers:
    """注册并持有 ``ON_MEDIA_RECOGNIZE`` 事件的默认处理器。

    Args:
        vlm_engine: 内置 VLM 引擎
        asr_engine: 内置 ASR 引擎
    """

    def __init__(self, vlm_engine: VLMEngine, asr_engine: ASREngine) -> None:
        self._vlm_engine = vlm_engine
        self._asr_engine = asr_engine
        self.register()

    def register(self) -> None:
        """注册默认 VLM/ASR/VIDEO 识别回调到 EventBus。

        使用 ``priority=0``（最低优先级），第三方插件用更高 priority
        即可先拦截。应在 EventManager 构建订阅映射后调用。
        """
        bus = get_event_bus()
        event_name = EventType.ON_MEDIA_RECOGNIZE.value
        try:
            bus.subscribe(
                event_name, self.on_media_recognize_vlm, priority=0, timeout=60
            )
            bus.subscribe(
                event_name, self.on_media_recognize_asr, priority=0, timeout=60
            )
            bus.subscribe(
                event_name, self.on_media_recognize_video, priority=0, timeout=60
            )
            logger.debug("已注册默认媒体识别回调: vlm, asr, video")
        except Exception as e:
            logger.error(f"默认媒体识别回调注册失败:{e}")

    async def on_media_recognize_vlm(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """VLM 引擎的默认回调。

        当 ``engine == "vlm"`` 且未被前序处理器处理时，调用内置 VLM 引擎
        识别图片/表情包，回写 ``description`` 和 ``engine_processed``。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            (EventDecision, params)
        """
        if params.get("engine") != MediaEngine.VLM.value:
            return EventDecision.PASS, params
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        base64_data = params.get("base64_data")
        media_type = params.get("media_type", "image")
        if not isinstance(base64_data, str) or not base64_data:
            return EventDecision.PASS, params

        description = await self._vlm_engine.recognize(base64_data, media_type)
        if description:
            params["description"] = description
            params["engine_processed"] = True
            logger.debug(
                f"默认VLM识别成功: {params.get('media_hash', '')[:8]}... "
                f"→ {description[:50]}..."
            )
            return EventDecision.SUCCESS, params

        return EventDecision.PASS, params

    async def on_media_recognize_asr(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """ASR 引擎的默认回调。

        当 ``engine == "asr"`` 且未被前序处理器处理时，调用内置 ASR 引擎
        识别语音，回写 ``description`` 和 ``engine_processed``。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            (EventDecision, params)
        """
        if params.get("engine") != MediaEngine.ASR.value:
            return EventDecision.PASS, params
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        base64_data = params.get("base64_data")
        if not isinstance(base64_data, str) or not base64_data:
            return EventDecision.PASS, params

        description = await self._asr_engine.recognize(base64_data)
        if description:
            params["description"] = description
            params["engine_processed"] = True
            logger.debug(
                f"默认ASR识别成功: {params.get('media_hash', '')[:8]}... "
                f"→ {description[:50]}..."
            )
            return EventDecision.SUCCESS, params

        return EventDecision.PASS, params

    async def on_media_recognize_video(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        """VIDEO 占位回调。

        视频识别无内置引擎，此回调仅作为占位存在：当 ``engine == "video"``
        且未被前序处理器处理时，直接 PASS，由第三方插件订阅事件回写
        ``description``；若无插件处理，最终由
        :meth:`~src.core.managers.media_manager.recognition.MediaRecognition.call_builtin_engine`
        返回 None。

        Args:
            event_name: 事件名称
            params: 事件参数

        Returns:
            (EventDecision, params)
        """
        if params.get("engine") != MediaEngine.VIDEO.value:
            return EventDecision.PASS, params
        # 视频无内置引擎，交由第三方插件处理
        return EventDecision.PASS, params
