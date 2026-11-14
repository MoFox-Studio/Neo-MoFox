"""neo_emoji_sender 插件入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .actions.send_emoji import (
    DeleteEmojiMemeByIdAction,
    SendEmojiMemeAction,
    SendEmojiMemeByIdAction,
)
from .config import NeoEmojiSenderConfig
from .event_handlers.vectorizer import EmojiVectorizer
from .manager.reconciler import EmojiReconciler
from .manager.runtime import EmojiRuntime
from .manager.service import EmojiService
from .sources.base import EmojiSource
from .sources.manual_dir import ManualDirectorySource
from .sources.media_cache import MediaCacheSource
from .tools.search_emoji import SearchEmojiMemesTool

logger = get_logger("neo_emoji_sender")

MODE_DIRECT = "direct"
MODE_PICKER = "picker"

_TARGET_REMINDER_BUCKET = "actor"
_TARGET_REMINDER_NAME = "关于表情包的使用"
_REMINDER = (
    "表情包的使用在你的日常互动中扮演着非常重要的角色。它直接影响到你向他人所展示出的形象。"
    "如果你经常只发文字，不发表情包，则通常被认为是严肃、专业的象征，而经常使用表情包则更可能被认为是可爱、活力的象征，"
    "所以请你认真对待表情包的使用。\n"
    "同时，表情包使用的时机也是一门学问。当别人和你谈正事等严肃的场合时，随意使用表情包可能被认为嘻嘻哈哈惹人厌的家伙，"
    "而在日常聊天中使用更多表情包的而不是认真的回复则可能更贴合轻松诙谐的气氛；当你懒得专门打字回应，或者只是冒个泡刷个存在感时，"
    "简单发个表情包是个很常见的做法。因此，虽然使用表情包看起来很简单，但是也值得你认真思考，请你在接下来的对话中时刻牢记这个能力，"
    "把表情包用活、用的恰到好处。"
)
_REMINDER_PICKER = (
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


def _get_interaction_mode(plugin: BasePlugin) -> str:
    """读取当前发送模式。

    Args:
        plugin: 插件实例。

    Returns:
        ``direct`` 或 ``picker``。
    """
    config = plugin.config
    if not isinstance(config, NeoEmojiSenderConfig):
        return MODE_DIRECT
    mode = str(config.plugin.interaction_mode).strip().lower()
    if mode not in (MODE_DIRECT, MODE_PICKER):
        logger.warning(f"未知 interaction_mode: {mode!r}，按 direct 处理")
        return MODE_DIRECT
    return mode


def build_actor_reminder(plugin: BasePlugin) -> str:
    """构建 actor reminder。

    Args:
        plugin: 插件实例。

    Returns:
        reminder 文本；关闭注入时为空串。
    """
    config = getattr(plugin, "config", None)
    if isinstance(config, NeoEmojiSenderConfig) and not config.plugin.inject_system_prompt:
        return ""
    return _REMINDER_PICKER if _get_interaction_mode(plugin) == MODE_PICKER else _REMINDER


def sync_actor_reminder(plugin: BasePlugin) -> str:
    """同步 actor reminder。

    Args:
        plugin: 插件实例。

    Returns:
        写入的 reminder 文本；清理时为空串。
    """
    from src.core.prompt import get_system_reminder_store

    store = get_system_reminder_store()
    content = build_actor_reminder(plugin)
    if not content:
        store.delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)
        return ""
    store.set(_TARGET_REMINDER_BUCKET, name=_TARGET_REMINDER_NAME, content=content)
    return content


@register_plugin
class NeoEmojiSenderPlugin(BasePlugin):
    """neo_emoji_sender 插件。"""

    plugin_name = "neo_emoji_sender"

    configs: list[type] = [NeoEmojiSenderConfig]
    dependencies: list[str] = []

    def __init__(self, config: NeoEmojiSenderConfig | None = None) -> None:
        """初始化插件。

        Args:
            config: 插件配置。
        """
        super().__init__(config)
        self.runtime: EmojiRuntime | None = None

    def get_components(self) -> list[type]:
        """返回插件组件类。

        注册集合随 ``interaction_mode`` 变化：direct 模式只注册一步直达
        动作，picker 模式注册检索工具与按 id 发送 / 删除动作。
        ``manifest.json`` 的 ``include`` 声明全部组件，具体启用项以本方法为准。

        Returns:
            组件类列表。
        """
        config = self.config
        if isinstance(config, NeoEmojiSenderConfig) and not config.plugin.enabled:
            return []
        common: list[type] = [EmojiService, EmojiVectorizer]
        if _get_interaction_mode(self) == MODE_PICKER:
            return [
                *common,
                SearchEmojiMemesTool,
                SendEmojiMemeByIdAction,
                DeleteEmojiMemeByIdAction,
            ]
        return [*common, SendEmojiMemeAction]

    async def on_plugin_loaded(self) -> None:
        """插件加载后初始化运行时并注册调度。"""
        config = self.config
        if isinstance(config, NeoEmojiSenderConfig) and not config.plugin.enabled:
            return

        self.runtime = EmojiRuntime(self)
        await self.runtime.initialize()
        sync_actor_reminder(self)
        self._apply_custom_instructions()

        self.runtime.reconciler = EmojiReconciler(self.runtime)
        self.runtime.sources = self._build_sources()
        await self._register_schedules()

    async def on_plugin_unloaded(self) -> None:
        """插件卸载前清理调度与资源。"""
        from src.core.prompt import get_system_reminder_store

        if self.runtime is not None:
            await self.runtime.close()
            self.runtime = None
        get_system_reminder_store().delete(_TARGET_REMINDER_BUCKET, _TARGET_REMINDER_NAME)

    def _build_sources(self) -> list[EmojiSource]:
        """按配置构造来源。

        Returns:
            启用的来源实例列表。
        """
        config = self.config
        sources: list[EmojiSource] = []
        if not isinstance(config, NeoEmojiSenderConfig):
            return sources
        if config.sources.manual.enabled:
            sources.append(ManualDirectorySource(self))
        if config.sources.media.enabled:
            sources.append(MediaCacheSource(self))
        return sources

    def _apply_custom_instructions(self) -> None:
        """把自定义场景说明追加到组件描述。"""
        config = self.config
        if not isinstance(config, NeoEmojiSenderConfig):
            return
        custom = config.prompt.custom_instructions.strip()
        if not custom:
            return
        targets: list[type] = []
        if _get_interaction_mode(self) == MODE_PICKER:
            targets = [SearchEmojiMemesTool, SendEmojiMemeByIdAction]
        else:
            targets = [SendEmojiMemeAction]
        for target in targets:
            base = getattr(target, "_base_description", None)
            if base is None:
                base = target.description
                target._base_description = base  # type: ignore[attr-defined]
            target.description = base.rstrip() + "\n\n自定义指令：\n" + custom

    async def _register_schedules(self) -> None:
        """注册来源与对账调度任务。"""
        from src.kernel.scheduler import TriggerType, get_unified_scheduler

        runtime = self.runtime
        if runtime is None:
            return
        scheduler = get_unified_scheduler()
        try:
            for source in runtime.sources:
                runtime.schedule_ids.extend(await source.start())
            if runtime.reconciler is not None:
                config = runtime.config
                runtime.schedule_ids.append(
                    await scheduler.create_schedule(
                        callback=runtime.reconciler.run_once,
                        trigger_type=TriggerType.TIME,
                        trigger_config={
                            "delay_seconds": int(config.reconcile.startup_delay_seconds)
                        },
                        is_recurring=False,
                        task_name="neo_emoji_sender_reconcile_once",
                        force_overwrite=True,
                    )
                )
                runtime.schedule_ids.append(
                    await scheduler.create_schedule(
                        callback=runtime.reconciler.run_once,
                        trigger_type=TriggerType.TIME,
                        trigger_config={
                            "interval_seconds": max(60, int(config.reconcile.interval_seconds))
                        },
                        is_recurring=True,
                        task_name="neo_emoji_sender_reconcile_recurring",
                        force_overwrite=True,
                    )
                )
            logger.info("neo_emoji_sender 调度任务已注册")
        except Exception as e:
            logger.warning(f"注册 neo_emoji_sender 调度失败: {e}")
