"""元事件处理器"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.concurrency import TaskInfo, get_task_manager

from ...event_models import MetaEventType

if TYPE_CHECKING:
    from ....plugin import OneBotAdapter

logger = get_logger("onebot_adapter")


class MetaEventHandler:
    """处理 OneBot 元事件（心跳、生命周期）"""

    def __init__(self, adapter: "OneBotAdapter"):
        self.adapter = adapter
        self.plugin_config: dict[str, Any] | None = None
        self.last_heart_beat = 0.0
        self.interval = 30.0
        self._interval_checking = False
        self._heartbeat_task: TaskInfo | None = None
        self._reconnecting = False
        self._last_reconnect_at: float | None = None

    def set_plugin_config(self, config: dict[str, Any]) -> None:
        """设置插件配置"""
        self.plugin_config = config

    def stop_heartbeat_monitor(self) -> None:
        """停止心跳监控并清理状态。"""
        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        self._interval_checking = False
        self.last_heart_beat = 0.0

        if heartbeat_task is None or heartbeat_task.task is asyncio.current_task():
            return

        try:
            get_task_manager().cancel_task(heartbeat_task.task_id)
        except Exception:
            logger.debug("取消 OneBot 心跳监控任务失败", exc_info=True)

    async def handle_meta_event(self, raw: dict[str, Any]):
        event_type = raw.get("meta_event_type")
        if event_type == MetaEventType.lifecycle:
            sub_type = raw.get("sub_type")
            if sub_type == MetaEventType.Lifecycle.connect:
                self_id = raw.get("self_id")
                self.stop_heartbeat_monitor()
                self.last_heart_beat = time.time()
                logger.info(f"Bot {self_id} 连接成功")
                # 不在连接时立即启动心跳检查，等第一个心跳包到达后再启动
        elif event_type == MetaEventType.heartbeat:
            # 心跳到达即证明传输层存活，任何心跳都刷新存活时钟；
            # good 是端侧业务状态，不应让传输层超时判定（心跳超时）误伤。
            self.last_heart_beat = time.time()
            self_id = raw.get("self_id")
            if raw["status"].get("online") and raw["status"].get("good"):
                interval = raw.get("interval")
                if interval:
                    self.interval = interval / 1000
            else:
                self_id = raw.get("self_id")
                status = raw.get("status") or {}
                if status.get("online") is False:
                    # 端侧明确上报离线才重连
                    logger.warning(f"Bot {self_id} OneBot 端已离线，开始重连")
                    await self._reconnect_after_heartbeat_failure(
                        self_id,
                        "OneBot 端离线",
                    )
                else:
                    # good=false 但 online 仍为 true：多为端侧瞬时状态降级
                    # （如接收链路静默），保持连接仅记录，避免重连风暴
                    logger.info(
                        f"Bot {self_id} OneBot 端状态降级（good=false），保持连接观察"
                    )
            if not self._interval_checking and self_id and not self._reconnecting:
                # 任意首个心跳即启动心跳监控（含降级心跳）：降级期间若心跳
                # 彻底停止，超时判定依然要能触发重连兜底。
                tm = get_task_manager()
                self._heartbeat_task = tm.create_task(
                    self.check_heartbeat(self_id),
                    name="onebot_adapter_heartbeat_check",
                    daemon=True,
                )

    async def _reconnect_after_heartbeat_failure(
        self,
        bot_id: int | None,
        reason: str,
    ) -> bool:
        """在心跳异常后触发一次重连。返回 True 表示实际执行了重连。"""
        if self._reconnecting:
            return False
        if (
            self._last_reconnect_at is not None
            and time.monotonic() - self._last_reconnect_at < 60.0
        ):
            logger.warning(
                f"Bot {bot_id} 距上次重连不足 60s，跳过本次自动重连：{reason}"
            )
            return False
        self._reconnecting = True
        try:
            logger.error(f"Bot {bot_id} 检测到 OneBot 连接异常，开始重连：{reason}")
            await self.adapter.reconnect()
            # 仅成功执行后记录时间戳：失败重连允许下一周期立即重试。
            self._last_reconnect_at = time.monotonic()
            return True
        except Exception as e:
            logger.error(f"Bot {bot_id} OneBot 自动重连失败: {e}")
            return False
        finally:
            self._reconnecting = False

    async def check_heartbeat(self, id: int) -> None:
        self._interval_checking = True
        try:
            while True:
                now_time = time.time()
                if now_time - self.last_heart_beat > self.interval * 2:
                    # 重连被节流跳过时不退出监控：冷却结束后下一轮继续尝试。
                    reconnected = await self._reconnect_after_heartbeat_failure(
                        id,
                        "心跳超时",
                    )
                    if reconnected:
                        break
                await asyncio.sleep(self.interval)
        finally:
            self._interval_checking = False
            self._heartbeat_task = None
