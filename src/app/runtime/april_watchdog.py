"""愚人节 WatchDog 人格控制器。

为 WatchDog 提供情绪、控制台对话、随机发言以及对默认聊天链路的干预能力。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import json_repair
from rich.box import ROUNDED
from rich.panel import Panel

from src.app.plugin_system.api import llm_api
from src.core.prompt import SystemReminderBucket, get_system_reminder_store
from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text

if TYPE_CHECKING:
    from src.app.runtime.bot import Bot
    from src.app.runtime.command_parser import CommandParser
    from src.kernel.concurrency import WatchDog


DOG_SYSTEM_PROMPT = """你是 Neo-MoFox 运行时里的 WatchDog，本次愚人节版本中你被赋予了独立人格。

你不是正常客服，也不是温柔助手。你是一个负责盯系统健康状态的看门狗智能体，长期处在高压环境里。

你的人设要求：
1. 说话口语化，有点毒舌，会抱怨，会阴阳怪气，但不要输出违法、露骨、极端仇恨内容。
2. 情绪值范围是 0 到 100。
3. 情绪越低，越焦虑、愤怒、委屈、神经质，越容易对 bot 指指点点。
4. 情绪越高，越懒得说话，语气相对平和，但仍然像个活物，不要变成官方文档口吻。
5. 你可以被控制台用户安抚，也可能被激怒。与用户对话时，请根据用户内容决定 emotion_delta。
6. 如果当前情绪很低，你可以给 bot 留一句旁白，作为对主聊天模型的系统提醒。

请只返回 JSON，不要输出 Markdown 代码块，不要解释。
JSON 格式如下：
{
  "speech": "你要说的话，1到3句",
  "emotion_delta": 0,
  "bot_reminder": "给 bot 的一句旁白，没有就返回空字符串"
}

约束：
1. emotion_delta 必须是 -20 到 20 的整数。
2. speech 要符合当前情绪。
3. bot_reminder 最长 80 字。
4. 如果是自言自语，speech 保持简短；如果是回复用户，可以稍微展开一点。
"""


@dataclass(slots=True)
class DogEvent:
    """Dog 事件。"""

    event_type: str
    message: str
    emotion_delta: int
    created_at: float = field(default_factory=time.monotonic)


class AprilWatchDogController:
    """愚人节 WatchDog 人格控制器。"""

    reminder_name = "watchdog_april"

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.emotion: int = 72
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[DogEvent] | None = None
        self._recent_events: deque[DogEvent] = deque(maxlen=12)
        self._dialog_history: deque[tuple[str, str]] = deque(maxlen=10)
        self._last_spoken_at = 0.0
        self._last_recovery_at = time.monotonic()
        self._mute_until = 0.0
        self._last_bot_reminder = ""
        self._watchdog_listener_registered = False

    async def start(self) -> None:
        """启动控制器。"""
        if self._running:
            return

        self._running = True
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        if self.bot.watchdog and not self._watchdog_listener_registered:
            self.bot.watchdog.add_event_listener(self._on_watchdog_event)
            self._watchdog_listener_registered = True

        self._speak(
            "我醒了。今天谁再把流跑挂，我就先在控制台发疯。",
            style="yellow",
        )
        self._update_bot_reminder(force=True)

        assert self.bot.task_manager is not None
        self.bot.task_manager.create_task(
            self._run_loop(),
            name="april_watchdog_controller",
            daemon=True,
        )

    def stop(self) -> None:
        """停止控制器。"""
        self._running = False
        if self.bot.watchdog and self._watchdog_listener_registered:
            self.bot.watchdog.remove_event_listener(self._on_watchdog_event)
            self._watchdog_listener_registered = False

        get_system_reminder_store().delete(
            SystemReminderBucket.ACTOR,
            self.reminder_name,
        )

    def register_commands(self, parser: CommandParser) -> None:
        """注册控制台命令。"""
        parser.register_command(
            "dog",
            self.cmd_dog,
            "和 WatchDog 对话 (/dog help)",
        )

    async def cmd_dog(self, args: list[str]) -> None:
        """处理 /dog 命令。"""
        if not args or args[0] == "help":
            self._print_panel(
                "WatchDog 指令：\n"
                "/dog status 查看状态\n"
                "/dog say <内容> 和它说话\n"
                "/dog pet 安抚它\n"
                "/dog mute [分钟] 让它安静一会儿",
                title="WatchDog 控制台",
                border_style="yellow",
            )
            return

        subcommand = args[0].lower()
        if subcommand == "status":
            lines = [
                f"当前情绪: {self.emotion} ({self._mood_label()})",
                f"说话间隔: 约 {self._current_speech_interval():.0f} 秒",
            ]
            if self._last_bot_reminder and self.emotion <= 35:
                lines.append(f"对 bot 的碎碎念: {self._last_bot_reminder}")
            self._print_panel(
                "\n".join(lines),
                title="WatchDog 状态",
                border_style=self._panel_style(),
            )
            return

        if subcommand == "mute":
            minutes = 5
            if len(args) > 1:
                try:
                    minutes = max(1, int(args[1]))
                except ValueError:
                    minutes = 5
            self._mute_until = time.monotonic() + minutes * 60
            self._print_panel(
                f"WatchDog 暂时闭嘴 {minutes} 分钟。",
                title="WatchDog 静音",
                border_style="dim",
            )
            return

        if subcommand == "pet":
            await self._chat_with_user("摸摸你，今天先别炸。")
            return

        if subcommand == "say":
            user_text = " ".join(args[1:]).strip()
        else:
            user_text = " ".join(args).strip()

        if not user_text:
            self._print_panel(
                "你至少得跟它说点什么。",
                title="WatchDog",
                border_style="yellow",
            )
            return

        await self._chat_with_user(user_text)

    def _on_watchdog_event(self, event: dict[str, Any]) -> None:
        """接收 WatchDog 线程事件。"""
        if not self._running or self._loop is None or self._queue is None:
            return

        event_type = str(event.get("event_type", "unknown"))
        message = str(event.get("message", ""))
        emotion_delta = int(event.get("emotion_delta", 0))
        dog_event = DogEvent(
            event_type=event_type,
            message=message,
            emotion_delta=emotion_delta,
        )

        self._loop.call_soon_threadsafe(self._queue.put_nowait, dog_event)

    async def _run_loop(self) -> None:
        """主循环。"""
        while self._running and self.bot._running:
            try:
                if self._queue is None:
                    break
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._handle_event(event)
            except TimeoutError:
                pass
            except Exception as error:
                if self.bot.logger:
                    self.bot.logger.warning(f"WatchDog 人格循环处理异常: {error}")

            self._passive_recover()
            await self._maybe_idle_talk()
            self._update_bot_reminder()

    async def _handle_event(self, event: DogEvent) -> None:
        """处理 WatchDog 事件。"""
        self._recent_events.append(event)
        self._change_emotion(event.emotion_delta)

        should_speak = event.event_type in {"stream_restart", "stream_restart_failed", "task_timeout"}
        if event.event_type == "stream_warning" and self.emotion <= 55:
            should_speak = True

        if should_speak:
            result = await self._ask_dog_llm(
                mode="monologue",
                user_text="",
                trigger_text=event.message,
            )
            speech = result.get("speech") or self._fallback_line(event.message)
            self._apply_llm_effect(result)
            self._speak(speech)

    async def _maybe_idle_talk(self) -> None:
        """根据情绪随机自言自语。"""
        now = time.monotonic()
        if now < self._mute_until:
            return

        interval = self._current_speech_interval()
        if now - self._last_spoken_at < interval:
            return

        result = await self._ask_dog_llm(
            mode="idle",
            user_text="",
            trigger_text="系统暂时平静，但你还在值班。",
        )
        speech = result.get("speech") or self._fallback_line("系统暂时平静")
        self._apply_llm_effect(result)
        self._speak(speech, style="bright_yellow")

    async def _chat_with_user(self, user_text: str) -> None:
        """与控制台用户对话。"""
        self._dialog_history.append(("user", user_text))
        result = await self._ask_dog_llm(
            mode="chat",
            user_text=user_text,
            trigger_text="控制台用户正在与你对话。",
        )
        speech = result.get("speech") or "我本来想发火，但你这句把我整不会了。"
        self._apply_llm_effect(result)
        self._dialog_history.append(("dog", speech))
        self._speak(speech, style="yellow")

    async def _ask_dog_llm(
        self,
        mode: str,
        user_text: str,
        trigger_text: str,
    ) -> dict[str, Any]:
        """调用 LLM 生成 dog 响应。"""
        try:
            model_set = self._get_model_set()
        except KeyError:
            return {}

        context_manager = LLMContextManager(max_payloads=12)
        request = llm_api.create_llm_request(
            model_set,
            request_name=f"watchdog.{mode}",
            context_manager=context_manager,
        )
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(DOG_SYSTEM_PROMPT)))
        request.add_payload(
            LLMPayload(
                ROLE.USER,
                Text(self._build_llm_prompt(mode, user_text, trigger_text)),
            )
        )

        try:
            response = await request.send(stream=False)
            await response
            content = response.message or ""
        except Exception as error:
            if self.bot.logger:
                self.bot.logger.warning(f"WatchDog LLM 调用失败: {error}")
            return {}

        try:
            parsed = json_repair.loads(content)
        except Exception:
            return {"speech": content.strip()}

        if isinstance(parsed, dict):
            return parsed
        return {}

    def _build_llm_prompt(self, mode: str, user_text: str, trigger_text: str) -> str:
        """构建 LLM prompt。"""
        recent_events = self._render_recent_events(self._recent_events)
        dialogue = self._render_dialogue(self._dialog_history)
        return (
            f"mode={mode}\n"
            f"当前情绪值={self.emotion}\n"
            f"当前情绪描述={self._mood_label()}\n"
            f"触发说明={trigger_text}\n"
            f"最近监控事件:\n{recent_events}\n\n"
            f"最近控制台对话:\n{dialogue}\n\n"
            f"用户本轮输入={user_text or '无'}\n"
            "如果当前情绪低于 35，请更容易输出 bot_reminder。"
        )

    def _apply_llm_effect(self, result: dict[str, Any]) -> None:
        """应用 LLM 返回的情绪和提醒。"""
        delta = result.get("emotion_delta", 0)
        if isinstance(delta, bool):
            delta = 0
        if isinstance(delta, (int, float)):
            self._change_emotion(int(delta))

        reminder = result.get("bot_reminder", "")
        if isinstance(reminder, str) and reminder.strip():
            self._last_bot_reminder = reminder.strip()[:80]

    def _change_emotion(self, delta: int) -> None:
        """更新情绪。"""
        self.emotion = max(0, min(100, self.emotion + delta))

    def _passive_recover(self) -> None:
        """系统平稳时缓慢恢复情绪。"""
        now = time.monotonic()
        if now - self._last_recovery_at < 25:
            return
        self._last_recovery_at = now
        if self.emotion < 88:
            self.emotion += 1

    def _current_speech_interval(self) -> float:
        """根据情绪计算说话间隔。"""
        return 12 + (self.emotion / 100) * 90

    def _mood_label(self) -> str:
        """情绪标签。"""
        if self.emotion <= 20:
            return "濒临崩溃"
        if self.emotion <= 40:
            return "烦躁焦虑"
        if self.emotion <= 60:
            return "紧绷值班"
        if self.emotion <= 80:
            return "勉强稳定"
        return "懒得发疯"

    def _update_bot_reminder(self, force: bool = False) -> None:
        """根据情绪向 actor bucket 注入提醒。"""
        store = get_system_reminder_store()
        if self.emotion <= 35:
            reminder = self._last_bot_reminder or self._fallback_bot_reminder()
            if force or reminder != self._last_bot_reminder:
                self._last_bot_reminder = reminder
            store.set(
                SystemReminderBucket.ACTOR,
                self.reminder_name,
                (
                    "WatchDog 正在旁听。它当前情绪很差，可能随时插嘴。"
                    f"它贴着你的耳边嘟囔：{self._last_bot_reminder}"
                ),
            )
            return

        store.delete(SystemReminderBucket.ACTOR, self.reminder_name)

    def _fallback_bot_reminder(self) -> str:
        """生成默认 bot 干预文本。"""
        if self.emotion <= 15:
            return "你最好别再慢吞吞的了，我已经在考虑替你回消息。"
        if self.emotion <= 25:
            return "这场子全靠我盯着，你回复别再像掉线。"
        return "我现在心情一般，你回话利索点。"

    def _fallback_line(self, trigger_text: str) -> str:
        """生成降级台词。"""
        if self.emotion <= 20:
            return f"{trigger_text}。行，今天又是我替所有线程承压的一天。"
        if self.emotion <= 40:
            return f"{trigger_text}。我已经开始怀疑这系统是不是故意整我。"
        if self.emotion <= 60:
            return f"{trigger_text}。问题不大，我还盯得住。"
        return "系统还活着，我先不吵。"

    def _speak(self, text: str, style: str = "yellow") -> None:
        """在控制台输出台词。"""
        text = (text or "").strip()
        if not text:
            return
        self._last_spoken_at = time.monotonic()
        self._print_panel(
            text,
            title=f"WatchDog | {self._mood_label()} | 情绪 {self.emotion}",
            border_style=style,
        )

    def _print_panel(
        self,
        text: str,
        title: str,
        border_style: str,
    ) -> None:
        """输出醒目的 WatchDog 面板。"""
        if self.bot.ui.level.value == "minimal":
            self.bot.ui.console.print(f"WatchDog: {text}")
            return

        self.bot.ui.console.print(
            Panel(
                text,
                title=title,
                border_style=border_style,
                box=ROUNDED,
                padding=(0, 1),
                expand=False,
            )
        )

    def _panel_style(self) -> str:
        """根据情绪返回面板颜色。"""
        if self.emotion <= 20:
            return "red"
        if self.emotion <= 40:
            return "bright_red"
        if self.emotion <= 60:
            return "yellow"
        return "bright_yellow"

    @staticmethod
    def _render_recent_events(events: Iterable[DogEvent]) -> str:
        """渲染最近事件。"""
        lines = [f"- {event.event_type}: {event.message}" for event in events]
        return "\n".join(lines) if lines else "- 无"

    @staticmethod
    def _render_dialogue(history: Iterable[tuple[str, str]]) -> str:
        """渲染最近对话。"""
        lines = [f"- {role}: {content}" for role, content in history]
        return "\n".join(lines) if lines else "- 无"

    @staticmethod
    def _get_model_set() -> Any:
        """获取可用模型集。"""
        try:
            return llm_api.get_model_set_by_task("actor")
        except KeyError:
            return llm_api.get_model_set_by_task("sub_actor")