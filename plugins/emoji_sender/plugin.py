"""emoji_sender 插件入口。

加载后会注册一个 scheduler 周期任务：定时执行一次入库（对齐→抽取→VLM 决策→收藏入库）。
注意：scheduler 在 Bot.run() 阶段才 start()，因此这里使用 task_manager 后台协程等待 scheduler 就绪后再注册。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from src.core.components import BasePlugin, register_plugin
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

from .action import SendEmojiMemeAction
from .config import EmojiSenderConfig
from .recognize_action import RecognizeEmojiAction
from .service import EmojiSenderService


logger = get_logger("emoji_sender")

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "关于表情包的使用"
_EMOJI_USAGE_REMINDER = (
    "表情包的使用在你的日常互动中扮演着非常重要的角色。它直接影响到你向他人所展示出的形象。"
    "如果你经常只发文字，不发表情包，则通常被认为是严肃、专业的象征，而经常使用表情包则更可能被认为是可爱、活力的象征，"
    "所以请你认真对待表情包的使用。\n"
    "同时，表情包使用的时机也是一门学问。当别人和你谈正事等严肃的场合时，随意使用表情包可能被认为嘻嘻哈哈惹人厌的家伙，"
    "而在日常聊天中使用更多表情包的而不是认真的回复则可能更贴合轻松诙谐的气氛；当你懒得专门打字回应，或者只是冒个泡刷个存在感时，"
    "简单发个表情包是个很常见的做法。因此，虽然使用表情包看起来很简单，但是也值得你认真思考，请你在接下来的对话中时刻牢记这个能力，"
    "把表情包用活、用的恰到好处。"
)


def build_emoji_sender_actor_reminder(plugin: Any) -> str:
    """构建 emoji_sender 的 actor reminder。"""

    config = getattr(plugin, "config", None)
    if isinstance(config, EmojiSenderConfig) and not config.plugin.inject_system_prompt:
        return ""
    return _EMOJI_USAGE_REMINDER


def sync_emoji_sender_actor_reminder(plugin: Any) -> str:
    """同步 emoji_sender 的 actor reminder。"""

    from src.core.prompt import get_system_reminder_store

    store = get_system_reminder_store()
    reminder_content = build_emoji_sender_actor_reminder(plugin)
    if not reminder_content:
        store.delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)
        logger.debug("emoji_sender actor reminder 已清理")
        return ""

    store.set(
        _TARGET_REMINDER_BUCKET,
        name=_TARGET_REMINDER_NAME,
        content=reminder_content,
    )
    logger.debug("emoji_sender actor reminder 已同步")
    return reminder_content


@register_plugin
class EmojiSenderPlugin(BasePlugin):
    """emoji_sender 插件。"""

    plugin_name: str = "emoji_sender"
    plugin_description: str = "从 media cache 收藏表情包并按 tag+向量检索发送"
    plugin_version: str = "1.0.0"

    configs: list[type] = [EmojiSenderConfig]
    dependent_components: list[str] = []

    def __init__(self, config: EmojiSenderConfig | None = None) -> None:
        super().__init__(config)
        self._schedule_ids: list[str] = []
        self._register_task_id: str | None = None

    def get_components(self) -> list[type]:
        """返回本插件提供的组件类。"""
        return [EmojiSenderService, SendEmojiMemeAction, RecognizeEmojiAction]

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后：初始化配置并注册周期任务。"""
        sync_emoji_sender_actor_reminder(self)

        # 将自定义场景说明追加到 action 的描述，使 Chatter 侧感知使用时机
        if isinstance(self.config, EmojiSenderConfig):
            custom = self.config.prompt.custom_instructions.strip()
            if custom:
                SendEmojiMemeAction.action_description = (
                    SendEmojiMemeAction.action_description.rstrip() + "\n\n自定义指令：\n" + custom
                )
                logger.debug("已将自定义场景说明追加到 send_emoji_meme 描述")

        tm = get_task_manager()
        task = tm.create_task(
            self._register_schedule_when_ready(),
            name="emoji_sender_register_schedule",
            daemon=True,
        )
        self._register_task_id = task.task_id

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前：移除 schedule，并取消后台注册任务。"""
        from src.kernel.scheduler import get_unified_scheduler
        from src.core.prompt import get_system_reminder_store

        scheduler = get_unified_scheduler()

        for schedule_id in list(self._schedule_ids):
            try:
                await scheduler.remove_schedule(schedule_id)
            except Exception:
                pass
        self._schedule_ids.clear()

        if self._register_task_id:
            try:
                get_task_manager().cancel_task(self._register_task_id)
            except Exception:
                pass
            self._register_task_id = None

        get_system_reminder_store().delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)

    async def _register_schedule_when_ready(self) -> None:
        """等待 scheduler 运行后注册周期任务。"""
        from src.kernel.scheduler import get_unified_scheduler, TriggerType

        if not isinstance(self.config, EmojiSenderConfig):
            logger.warning("emoji_sender config 未加载，无法注册 schedule")
            return

        scheduler = get_unified_scheduler()
        interval = int(self.config.scheduler.interval_seconds)
        task_name_once = "emoji_sender_ingest_once"
        task_name_recurring = "emoji_sender_ingest_recurring"

        # scheduler.start() 发生在 Bot.run()；这里等待其就绪。
        for attempt in range(600):
            try:
                # 先注册一次性任务，立即跑一遍（满足“启动后尽快入库”的直觉）
                once_id = await scheduler.create_schedule(
                    callback=self._ingest_job,
                    trigger_type=TriggerType.TIME,
                    trigger_config={"delay_seconds": 0},
                    is_recurring=False,
                    task_name=task_name_once,
                    force_overwrite=True,
                )

                recurring_id = await scheduler.create_schedule(
                    callback=self._ingest_job,
                    trigger_type=TriggerType.TIME,
                    trigger_config={"interval_seconds": interval},
                    is_recurring=True,
                    task_name=task_name_recurring,
                    force_overwrite=True,
                )

                self._schedule_ids = [once_id, recurring_id]
                logger.info(
                    f"emoji_sender 入库任务已注册: once={once_id} recurring={recurring_id}"
                )
                return
            except RuntimeError:
                await asyncio.sleep(0.5)
                continue
            except Exception as e:
                logger.warning(f"注册 emoji_sender 入库任务失败: {e}")
                await asyncio.sleep(2.0)

        logger.warning("等待 scheduler 就绪超时，emoji_sender 入库任务未注册")

    async def _ingest_job(self) -> None:
        """scheduler 回调：创建一个 service 实例并执行入库。"""
        from src.app.plugin_system.api.service_api import get_service

        service = get_service("emoji_sender:service:emoji_sender")
        if service is None:
            return
        await cast(EmojiSenderService, service).ingest_once()
