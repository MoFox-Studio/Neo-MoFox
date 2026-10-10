"""emoji_sender 插件入口。

加载后会注册一个 scheduler 周期任务：定时执行一次入库（对齐→抽取→VLM 决策→收藏入库）。
注意：scheduler 在 Bot.run() 阶段才 start()，因此这里使用 task_manager 后台协程等待 scheduler 就绪后再注册。

发送模式由 config.plugin.interaction_mode 决定：
- "direct"：注册 SendEmojiMemeAction（一步直达，插件自动挑选后直接发送）
- "picker"：注册 SearchEmojiMemesTool + SendEmojiMemeByIdAction（先查询候选，AI 亲自挑选后按 id 发送）
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from src.core.components import BasePlugin, register_plugin
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

from .action import (
    CollectEmojiMemeAction,
    RefreshEmojiMemeAction,
    SendEmojiMemeAction,
    SendEmojiMemeByIdAction,
)
from .config import EmojiSenderConfig
from .service import EmojiSenderService
from .tool import SearchEmojiMemesTool


logger = get_logger("emoji_sender")

MODE_DIRECT = "direct"
MODE_PICKER = "picker"

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "关于表情包的使用"
_EMOJI_COLLECTION_REMINDER = (
    "\n看到聊天中自己喜欢、符合自己的人设和表达习惯、以后用得上的表情包时，主动调用 collect_emoji_meme 收藏，不必等用户要求。"
    "想想以后会用它表达什么情绪、回应什么场景；有选择地收藏，不是看到图片就收。"
    "收藏不代表现在就要发送，也不改变表情包的发送频率限制。"
    "media_id 使用图片或表情包占位符括号里的完整哈希。"
    "有想留给以后自己的内容，可以用 note 附上备注；没什么想记的就不写，不用每张图都凑收藏理由。"
    "备注可以写理解、梗、前因后果、用法，也可以写其他值得记住的内容，不限一两句话，写到以后脱离当前聊天还能看懂即可，不编造背景。"
    "发现聊天图片或表情包的描述、文字可能认错时，可以调用 refresh_emoji_meme 重新识别，不需要先收藏。"
    "meme_id 使用当前聊天图片的完整 media_id，也可使用收藏返回的 id 或检索结果的 12 位 id；"
    "extra_prompt 可补充需要重点核对的疑点。原文件已不可用时请对方重新发送图片，不用旧描述代替看图。"
    "只想补充、修改或清除已收藏图片的备注时，调用 refresh_emoji_meme，传 note_only=true 和 note，不用原图或重新识别；"
    "note 不传或 null 保留原备注，空字符串清除。检索候选带有备注时，也看看它留下的含义和语境。"
    "收藏、重新识别和修改备注都不会自动发消息，不必每次向聊天对象宣布。"
)
_EMOJI_USAGE_REMINDER = (
    "表情包的使用在你的日常互动中扮演着非常重要的角色。它直接影响到你向他人所展示出的形象。"
    "如果你经常只发文字，不发表情包，则通常被认为是严肃、专业的象征，而经常使用表情包则更可能被认为是可爱、活力的象征，"
    "所以请你认真对待表情包的使用。\n"
    "同时，表情包使用的时机也是一门学问。当别人和你谈正事等严肃的场合时，随意使用表情包可能被认为嘻嘻哈哈惹人厌的家伙，"
    "而在日常聊天中使用更多表情包的而不是认真的回复则可能更贴合轻松诙谐的气氛；当你懒得专门打字回应，或者只是冒个泡刷个存在感时，"
    "简单发个表情包是个很常见的做法。因此，虽然使用表情包看起来很简单，但是也值得你认真思考，请你在接下来的对话中时刻牢记这个能力，"
    "把表情包用活、用的恰到好处。"
)
_EMOJI_USAGE_REMINDER_PICKER = (
    "表情包的使用在你的日常互动中扮演着非常重要的角色。它直接影响到你向他人所展示出的形象。"
    "如果你经常只发文字，不发表情包，则通常被认为是严肃、专业的象征，而经常使用表情包则更可能被认为是可爱、活力的象征，"
    "所以请你认真对待表情包的使用。\n"
    "同时，表情包使用的时机也是一门学问。当别人和你谈正事等严肃的场合时，随意使用表情包可能被认为嘻嘻哈哈惹人厌的家伙，"
    "而在日常聊天中使用更多表情包的而不是认真的回复则可能更贴合轻松诙谐的气氛；当你懒得专门打字回应，或者只是冒个泡刷个存在感时，"
    "简单发个表情包是个很常见的做法。\n"
    "你发送表情包的方式是两步：先调用 search_emoji_memes 按目标描述（可附带情感标签）检索表情包库，"
    "查看返回的候选列表（列表中每项含 id、标签、描述与距离，距离越小与你想要的越贴切），"
    "从中挑选最契合当前语境与你的形象的一张，再用它的 id 调用 send_emoji_meme_by_id 发送；"
    "如果对候选不满意，可以换更具体的描述重新查询，或翻页查看更多候选。"
    "请把表情包用活、用的恰到好处。"
)


def _get_interaction_mode(plugin: Any) -> str:
    """读取当前发送模式，非法值按 direct 处理并告警。"""
    config = plugin.config
    if not isinstance(config, EmojiSenderConfig):
        return MODE_DIRECT
    mode = str(config.plugin.interaction_mode).strip().lower()
    if mode not in (MODE_DIRECT, MODE_PICKER):
        logger.warning(f"未知 interaction_mode: {mode!r}，按 direct 处理")
        return MODE_DIRECT
    return mode


def build_emoji_sender_actor_reminder(plugin: Any) -> str:
    """构建 emoji_sender 的 actor reminder。"""

    config = getattr(plugin, "config", None)
    if isinstance(config, EmojiSenderConfig) and not config.plugin.inject_system_prompt:
        return ""
    if _get_interaction_mode(plugin) == MODE_PICKER:
        return _EMOJI_USAGE_REMINDER_PICKER + _EMOJI_COLLECTION_REMINDER
    return _EMOJI_USAGE_REMINDER + _EMOJI_COLLECTION_REMINDER


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

    configs: list[type] = [EmojiSenderConfig]
    dependent_components: list[str] = []

    def __init__(self, config: EmojiSenderConfig | None = None) -> None:
        """初始化插件的调度状态与运行期拒绝哈希集合。"""
        super().__init__(config)
        self._schedule_ids: list[str] = []
        self._register_task_id: str | None = None
        self._rejected_hashes: set[str] = set()

    def get_components(self) -> list[type]:
        """返回本插件提供的组件类（按 interaction_mode 分模式注册）。"""
        config = self.config
        if isinstance(config, EmojiSenderConfig) and not config.plugin.enabled:
            return []
        components: list[type] = [EmojiSenderService, CollectEmojiMemeAction, RefreshEmojiMemeAction]
        if _get_interaction_mode(self) == MODE_PICKER:
            return [*components, SearchEmojiMemesTool, SendEmojiMemeByIdAction]
        return [*components, SendEmojiMemeAction]

    async def on_plugin_loaded(self) -> None:
        """插件加载完成后：初始化配置并注册周期任务。"""
        config = self.config
        if isinstance(config, EmojiSenderConfig) and not config.plugin.enabled:
            return
        sync_emoji_sender_actor_reminder(self)

        # 将自定义场景说明追加到当前模式对应 LLM 组件的描述，使 Chatter 侧感知使用时机
        if isinstance(self.config, EmojiSenderConfig):
            custom = self.config.prompt.custom_instructions.strip()
            if custom:
                if _get_interaction_mode(self) == MODE_PICKER:
                    SearchEmojiMemesTool.description = (
                        SearchEmojiMemesTool.description.rstrip() + "\n\n自定义指令：\n" + custom
                    )
                    SendEmojiMemeByIdAction.description = (
                        SendEmojiMemeByIdAction.description.rstrip() + "\n\n自定义指令：\n" + custom
                    )
                    logger.debug("已将自定义场景说明追加到 picker 模式组件描述")
                else:
                    SendEmojiMemeAction.description = (
                        SendEmojiMemeAction.description.rstrip() + "\n\n自定义指令：\n" + custom
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
