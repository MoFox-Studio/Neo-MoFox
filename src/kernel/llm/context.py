"""LLM 请求的高层上下文管理器。

``LLMContextManager`` 是一个门面，组合了更深的两类职责：
- ``context_structure.py`` 负责保证对话结构合法；
- ``context_budget.py`` 负责在接近 token 上限时做压缩和裁剪。

另外，reminder 注入也是这个文件负责的，因为它属于面向调用方暴露的上下文 API。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.core.prompt import SystemReminderInsertType

from .context_budget import (
    AsyncContextCompressionHandler,
    TokenCounter,
    build_qa_groups,
    compute_effective_context_budget,
    flatten_groups,
    maybe_compress_payloads,
    maybe_trim_payloads,
    prepare_payloads_for_model as prepare_payloads_for_model_impl,
    split_pinned_prefix,
    trim_payloads_by_tokens,
)
from .context_structure import append_payload, validate_payload_sequence
from .payload import LLMPayload
from .payload.content import Content, Text
from .payload.tooling import LLMUsable
from .roles import ROLE
from .types import ModelEntry

if TYPE_CHECKING:
    from .request import LLMRequest


@dataclass(slots=True, frozen=True)
class RegisteredReminder:
    """解析完成的 reminder 文本及其插入策略。"""
    text: str
    insert_type: SystemReminderInsertType


@dataclass(slots=True)
class RegisteredReminderSource:
    """运行时 reminder 源注册记录，带已渲染结果缓存。"""
    bucket: str
    names: tuple[str, ...] | None
    wrap_with_system_tag: bool
    last_rendered: tuple[RegisteredReminder, ...] = ()


@dataclass(slots=True, frozen=True)
class ReminderSourceSpec:
    """由调用方提供的不可变 reminder 源配置。"""
    bucket: str
    names: tuple[str, ...] | None = None
    wrap_with_system_tag: bool = False


@dataclass(slots=True)
class LLMContextManager:
    """统一承接 reminder、校验、压缩和裁剪的上下文门面。"""
    context_compression_handler: AsyncContextCompressionHandler | None = None
    reminder_sources: Sequence[ReminderSourceSpec] | None = None
    _reminder_sources: list[RegisteredReminderSource] | None = field(
        default=None, init=False, repr=False
    )
    _injected_reminder_texts: set[str] | None = None

    def __post_init__(self) -> None:
        """把配置期的 reminder 源规范化成运行时记录。"""
        if not self.reminder_sources:
            return

        normalized_sources: list[RegisteredReminderSource] = []
        for source in self.reminder_sources:
            normalized_bucket = str(source.bucket).strip()
            if not normalized_bucket:
                raise ValueError("bucket cannot be empty")

            normalized_names: tuple[str, ...] | None = None
            if source.names is not None:
                normalized_list: list[str] = []
                for name in source.names:
                    normalized_name = str(name).strip()
                    if not normalized_name:
                        raise ValueError("names contains an empty name")
                    normalized_list.append(normalized_name)
                normalized_names = tuple(normalized_list)

            normalized_sources.append(
                RegisteredReminderSource(
                    bucket=normalized_bucket,
                    names=normalized_names,
                    wrap_with_system_tag=bool(source.wrap_with_system_tag),
                )
            )

        self._reminder_sources = normalized_sources

    def validate_for_send(self, payloads: list[LLMPayload]) -> None:
        """在发起 provider 调用前校验最终 payload 序列。"""
        validate_payload_sequence(payloads, allow_incomplete_tail=False)

    def add_payload(
        self,
        payloads: list[LLMPayload],
        payload: LLMPayload,
        position: int | None = None,
    ) -> list[LLMPayload]:
        """追加 payload，注入 reminder，再裁剪并校验上下文。"""
        updated = append_payload(payloads, payload, position=position)
        if payload.role == ROLE.USER:
            updated = self._apply_reminders(updated)

        trimmed = self.maybe_trim(updated)
        validate_payload_sequence(trimmed, allow_incomplete_tail=True)
        return trimmed

    def _validate_payloads(
        self,
        payloads: list[LLMPayload],
        *,
        allow_incomplete_tail: bool,
    ) -> None:
        """为兼容旧调用路径保留的内部校验钩子。"""
        validate_payload_sequence(
            payloads,
            allow_incomplete_tail=allow_incomplete_tail,
        )

    def system(
        self,
        payloads: list[LLMPayload],
        content: Content | LLMUsable | list[Content | LLMUsable],
        position: int | None = None,
    ) -> list[LLMPayload]:
        """追加 system payload 的便捷方法。"""
        return self.add_payload(
            payloads,
            LLMPayload(ROLE.SYSTEM, content),
            position=position,
        )

    def tool(
        self,
        payloads: list[LLMPayload],
        content: Content | LLMUsable | list[Content | LLMUsable],
        position: int | None = None,
    ) -> list[LLMPayload]:
        """追加工具声明 payload 的便捷方法。"""
        return self.add_payload(
            payloads,
            LLMPayload(ROLE.TOOL, content),
            position=position,
        )

    def reminder_bucket(
        self,
        bucket: str,
        *,
        names: Sequence[str] | None = None,
        wrap_with_system_tag: bool = False,
    ) -> None:
        """注册一个会注入到 user payload 的 reminder bucket。"""
        normalized_bucket = str(bucket).strip()
        if not normalized_bucket:
            raise ValueError("bucket cannot be empty")

        normalized_names: tuple[str, ...] | None = None
        if names is not None:
            normalized_list: list[str] = []
            for name in names:
                normalized_name = str(name).strip()
                if not normalized_name:
                    raise ValueError("names contains an empty name")
                normalized_list.append(normalized_name)
            normalized_names = tuple(normalized_list)

        if self._reminder_sources is None:
            self._reminder_sources = []
        self._reminder_sources.append(
            RegisteredReminderSource(
                bucket=normalized_bucket,
                names=normalized_names,
                wrap_with_system_tag=wrap_with_system_tag,
            )
        )

    def _apply_reminders(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
        """把解析后的 reminder 注入到首个和/或最后一个 user payload。"""
        updated = list(payloads)
        user_indices = [
            index for index, payload in enumerate(updated) if payload.role == ROLE.USER
        ]
        if not user_indices:
            return updated

        resolved_reminders, strip_texts_by_type = self._resolve_reminders()
        if not resolved_reminders:
            self._injected_reminder_texts = set()
            return updated

        first_user_index = user_indices[0]
        last_user_index = user_indices[-1]

        new_parts: dict[int, list[Text]] = {}
        seen_targets: set[tuple[int, str]] = set()
        for reminder in resolved_reminders:
            target_index = (
                first_user_index
                if reminder.insert_type == SystemReminderInsertType.FIXED
                else last_user_index
            )
            target_key = (target_index, reminder.text)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            new_parts.setdefault(target_index, []).append(Text(reminder.text))

        if not new_parts:
            self._injected_reminder_texts = {
                reminder.text for reminder in resolved_reminders
            }
            return updated

        for user_index, prefix_parts in new_parts.items():
            user_payload = updated[user_index]
            content_parts = list(user_payload.content)
            target_strip_set: set[str] = set()

            if user_index == first_user_index:
                target_strip_set.update(
                    strip_texts_by_type[SystemReminderInsertType.FIXED]
                )
            if user_index == last_user_index:
                target_strip_set.update(
                    strip_texts_by_type[SystemReminderInsertType.FIXED]
                )
                target_strip_set.update(
                    strip_texts_by_type[SystemReminderInsertType.DYNAMIC]
                )

            if target_strip_set:
                prefix_end = 0
                while prefix_end < len(content_parts):
                    part = content_parts[prefix_end]
                    if not isinstance(part, Text) or part.text not in target_strip_set:
                        break
                    prefix_end += 1
                if prefix_end > 0:
                    content_parts = content_parts[prefix_end:]

            rebuilt = list(prefix_parts) + content_parts
            updated[user_index] = LLMPayload(ROLE.USER, rebuilt)

        self._injected_reminder_texts = {
            reminder.text for reminder in resolved_reminders
        }
        return updated

    def _resolve_reminders(
        self,
    ) -> tuple[list[RegisteredReminder], dict[SystemReminderInsertType, list[str]]]:
        """从 store 中解析 reminder bucket，并记录需要剥离的旧文本。"""
        resolved: list[RegisteredReminder] = []
        strip_texts_by_type: dict[SystemReminderInsertType, list[str]] = {
            SystemReminderInsertType.FIXED: [],
            SystemReminderInsertType.DYNAMIC: [],
        }

        if self._reminder_sources:
            from src.core.prompt import get_system_reminder_store

            store = get_system_reminder_store()
            for source in self._reminder_sources:
                for previous in source.last_rendered:
                    strip_texts_by_type[previous.insert_type].append(previous.text)

                items = store.get_items(source.bucket, names=source.names)
                current_items: list[RegisteredReminder] = []
                for item in items:
                    text = item.render()
                    if source.wrap_with_system_tag:
                        text = f"<system_reminder>\n{text}\n</system_reminder>"
                    reminder = RegisteredReminder(
                        text=text,
                        insert_type=item.insert_type,
                    )
                    current_items.append(reminder)
                    resolved.append(reminder)

                source.last_rendered = tuple(current_items)
                for current in current_items:
                    strip_texts_by_type[current.insert_type].append(current.text)

        deduped_strip_texts = {
            insert_type: list(dict.fromkeys(texts))
            for insert_type, texts in strip_texts_by_type.items()
        }
        return resolved, deduped_strip_texts

    def maybe_trim(
        self,
        payloads: list[LLMPayload],
        *,
        max_token_budget: int | None = None,
        token_counter: TokenCounter | None = None,
    ) -> list[LLMPayload]:
        return maybe_trim_payloads(
            payloads,
            max_token_budget=max_token_budget,
            token_counter=token_counter,
        )

    async def prepare_payloads_for_model(
        self,
        payloads: list[LLMPayload],
        model: ModelEntry,
        *,
        request: LLMRequest | None = None,
    ) -> list[LLMPayload]:
        return await prepare_payloads_for_model_impl(
            payloads,
            model,
            request=request,
            context_compression_handler=self.context_compression_handler,
        )

    def _compute_effective_context_budget(self, model: ModelEntry) -> int | None:
        return compute_effective_context_budget(model)

    async def _maybe_compress_payloads(
        self,
        payloads: list[LLMPayload],
        model: ModelEntry,
        *,
        request: LLMRequest | None = None,
    ) -> list[LLMPayload]:
        return await maybe_compress_payloads(
            payloads,
            model,
            request=request,
            context_compression_handler=self.context_compression_handler,
        )

    def _trim_by_tokens(
        self,
        payloads: list[LLMPayload],
        token_budget: int,
        token_counter: TokenCounter,
    ) -> list[LLMPayload]:
        return trim_payloads_by_tokens(payloads, token_budget, token_counter)

    def _split_pinned_prefix(
        self,
        payloads: list[LLMPayload],
    ) -> tuple[list[LLMPayload], list[LLMPayload]]:
        return split_pinned_prefix(payloads)

    def _build_qa_groups(self, payloads: list[LLMPayload]) -> list[list[LLMPayload]]:
        return build_qa_groups(payloads)

    def _flatten_groups(self, groups: list[list[LLMPayload]]) -> list[LLMPayload]:
        return flatten_groups(groups)
