"""框架内置媒体识别事件处理器。

提供默认的 VLM（图片/表情包）和 ASR（语音）识别处理器，
订阅 ``ON_MEDIA_RECOGNIZE`` 事件，在第三方插件不拦截时执行默认识别。

第三方插件可用更高 ``weight`` 订阅同一事件，提前拦截或改写识别结果。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.base import BaseEventHandler
from src.core.components.types import EventType, MediaEngine
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.components.base import BasePlugin

logger = get_logger("media_recognition_handlers")


class DefaultVlmHandler(BaseEventHandler):
    """默认 VLM 识别处理器。

    订阅 ``ON_MEDIA_RECOGNIZE`` 事件，当 ``engine == "vlm"`` 时调用
    MediaManager 内置的 VLM 引擎识别图片/表情包，将结果回写到
    ``params["description"]`` 并标记 ``engine_processed=True``。

    使用 ``weight=0``（最低优先级），第三方插件用更高 weight
    即可先拦截——拦截后可通过 ``STOP`` 终止链路，或设
    ``skip_engine=True`` 跳过引擎调用（如屏蔽裸体内容）。
    """

    handler_name = "default_vlm_handler"
    handler_description = "默认 VLM 图片/表情包识别处理器（weight=0，可被第三方覆盖）"
    weight = 0
    init_subscribe = [EventType.ON_MEDIA_RECOGNIZE]

    def __init__(self, plugin: BasePlugin | None = None) -> None:
        """初始化默认 VLM 处理器。

        Args:
            plugin: 所属插件实例；框架内置处理器可为 None
        """
        self.plugin = plugin
        self._subscribed_events: set[Any] = set()
        self.signature = ""
        for event in self.init_subscribe:
            self.subscribe(event)

    def _make_bus_callback(self) -> Any:
        """创建符合 EventBus 协议的 async 回调。

        Returns:
            async callable，签名 (event_name, params) -> (EventDecision, params)
        """

        async def _callback(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
            return await self.execute(event_name, params)

        return _callback

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理媒体识别事件：engine=vlm 时调 VLM 回写 description。

        Args:
            event_name: 事件名称
            params: 事件参数，必须包含 engine、base64_data、media_type 等 key

        Returns:
            (EventDecision, params)：识别成功返回 SUCCESS + 回写的 params；
            非 vlm 引擎或不满足条件返回 PASS
        """
        # 仅处理 VLM 引擎的媒体
        if params.get("engine") != MediaEngine.VLM.value:
            return EventDecision.PASS, params

        # 已被前序处理器回写或跳过
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        base64_data = params.get("base64_data")
        media_type = params.get("media_type", "image")
        if not isinstance(base64_data, str) or not base64_data:
            return EventDecision.PASS, params

        try:
            from src.core.managers.media_manager import get_media_manager

            manager = get_media_manager()
            description = await manager._recognize_with_vlm(base64_data, media_type)

            if description:
                params["description"] = description
                params["engine_processed"] = True
                logger.debug(
                    f"默认VLM识别成功: {params.get('media_hash', '')[:8]}... "
                    f"→ {description[:50]}..."
                )
                return EventDecision.SUCCESS, params

            logger.debug("默认VLM识别失败，未返回描述")
        except Exception as e:
            logger.error(f"默认VLM处理器执行失败: {e}", exc_info=True)

        return EventDecision.PASS, params


class DefaultAsrHandler(BaseEventHandler):
    """默认 ASR 识别处理器。

    订阅 ``ON_MEDIA_RECOGNIZE`` 事件，当 ``engine == "asr"`` 时调用
    MediaManager 内置的 ASR 引擎识别语音，将结果回写到
    ``params["description"]`` 并标记 ``engine_processed=True``。

    使用 ``weight=0``（最低优先级），第三方插件可用更高 weight 拦截。
    """

    handler_name = "default_asr_handler"
    handler_description = "默认 ASR 语音识别处理器（weight=0，可被第三方覆盖）"
    weight = 0
    init_subscribe = [EventType.ON_MEDIA_RECOGNIZE]

    def __init__(self, plugin: BasePlugin | None = None) -> None:
        """初始化默认 ASR 处理器。

        Args:
            plugin: 所属插件实例；框架内置处理器可为 None
        """
        self.plugin = plugin
        self._subscribed_events: set[Any] = set()
        self.signature = ""
        for event in self.init_subscribe:
            self.subscribe(event)

    def _make_bus_callback(self) -> Any:
        """创建符合 EventBus 协议的 async 回调。

        Returns:
            async callable，签名 (event_name, params) -> (EventDecision, params)
        """

        async def _callback(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
            return await self.execute(event_name, params)

        return _callback

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理媒体识别事件：engine=asr 时调 ASR 回写 description。

        Args:
            event_name: 事件名称
            params: 事件参数，必须包含 engine、base64_data 等 key

        Returns:
            (EventDecision, params)：识别成功返回 SUCCESS + 回写的 params；
            非 asr 引擎或不满足条件返回 PASS
        """
        # 仅处理 ASR 引擎的媒体
        if params.get("engine") != MediaEngine.ASR.value:
            return EventDecision.PASS, params

        # 已被前序处理器回写或跳过
        if params.get("engine_processed") or params.get("skip_engine"):
            return EventDecision.PASS, params

        base64_data = params.get("base64_data")
        if not isinstance(base64_data, str) or not base64_data:
            return EventDecision.PASS, params

        try:
            from src.core.managers.media_manager import get_media_manager

            manager = get_media_manager()
            description = await manager._recognize_with_asr(base64_data)

            if description:
                params["description"] = description
                params["engine_processed"] = True
                logger.debug(
                    f"默认ASR识别成功: {params.get('media_hash', '')[:8]}... "
                    f"→ {description[:50]}..."
                )
                return EventDecision.SUCCESS, params

            logger.debug("默认ASR识别失败，未返回文本")
        except Exception as e:
            logger.error(f"默认ASR处理器执行失败: {e}", exc_info=True)

        return EventDecision.PASS, params
