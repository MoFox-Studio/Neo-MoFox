"""元事件处理器"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger

from ...event_models import MetaEventType

if TYPE_CHECKING:
    from ....plugin import NapcatAdapter

logger = get_logger("napcat_adapter")


class MetaEventHandler:
    """处理 Napcat 元事件（心跳、生命周期）"""

    def __init__(self, adapter: "NapcatAdapter"):
        self.adapter = adapter
        self.plugin_config: dict[str, Any] | None = None
        self._interval_checking = False
        self._heartbeat_task: asyncio.Task | None = None

    def set_plugin_config(self, config: dict[str, Any]) -> None:
        """设置插件配置"""
        self.plugin_config = config

    async def handle_meta_event(self, raw: dict[str, Any]):
        event_type = raw.get("meta_event_type")
        if event_type == MetaEventType.lifecycle:
            sub_type = raw.get("sub_type")
            if sub_type == MetaEventType.Lifecycle.connect:
                self_id = raw.get("self_id")
                self.last_heart_beat = time.time()
                logger.info(f"Bot {self_id} 连接成功")
                # 不在连接时立即启动心跳检查，等第一个心跳包到达后再启动
        elif event_type == MetaEventType.heartbeat:
            if raw["status"].get("online") and raw["status"].get("good"):
                self_id = raw.get("self_id")
                if not self._interval_checking and self_id:
                    # 第一次收到心跳包时才启动心跳检查
                    self._heartbeat_task = asyncio.create_task(self.check_heartbeat(self_id))
                self.last_heart_beat = time.time()
                interval = raw.get("interval")
                if interval:
                    self.interval = interval / 1000
            else:
                self_id = raw.get("self_id")
                logger.warning(f"Bot {self_id} Napcat 端异常！")

    async def check_heartbeat(self, id: int) -> None:
        self._interval_checking = True
        while True:
            now_time = time.time()
            if now_time - self.last_heart_beat > self.interval * 2:
                logger.error(f"Bot {id} 可能发生了连接断开，被下线，或者Napcat卡死！")
                break
            await asyncio.sleep(self.interval)
