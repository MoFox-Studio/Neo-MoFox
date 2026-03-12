"""LLM context management utilities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .payload import LLMPayload
from .payload.content import Content, Text
from .payload.tooling import LLMUsable, ToolCall, ToolResult
from .roles import ROLE
from .exceptions import LLMContextError

CompressionHook = Callable[[list[list[LLMPayload]], list[LLMPayload]], list[LLMPayload]]
TokenCounter = Callable[[list[LLMPayload]], int]


@dataclass(slots=True)
class LLMContextManager:
    """上下文管理器。

    默认职责：
    1. 接管 payload 列表写入（add_payload/system/tool）；
    2. 接管 reminder 的延迟登记；
    3. 在写入后执行结构校验（strict，不做自动修复）；
    4. 最后按 max_payloads/token_budget 执行裁剪。

    对于 reminder：固定注入到“首个真实 USER 消息的首段”；若尚无 USER，则继续等待后续 USER。
    """

    max_payloads: int | None = None
    compression_hook: CompressionHook | None = None
    _reminders: list[Text] | None = None

    def validate_for_send(self, payloads: list[LLMPayload]) -> None:
        """在发起 LLM 请求前校验上下文结构。

        不允许任何未闭合的 tool 调用链（包括尾部）。
        """

        self._validate_payloads(payloads, allow_incomplete_tail=False)

    def add_payload(
        self,
        payloads: list[LLMPayload],
        payload: LLMPayload,
        position: int | None = None,
    ) -> list[LLMPayload]:
        """向上下文追加 payload，并进行规范化与裁剪。

        Args:
            payloads: 现有 payload 列表。
            payload: 待追加 payload。
            position: 可选插入位置。

        Returns:
            list[LLMPayload]: 规范化后的 payload 列表。
        """

        updated = list(payloads)

        if position is not None:
            updated.insert(int(position), payload)
        elif updated and updated[-1].role == payload.role:
            updated[-1].content.extend(payload.content)
        else:
            updated.append(payload)

        updated = self._apply_reminders(updated)
        trimmed = self.maybe_trim(updated)

        # strict：不做自动修复，但要尽早暴露“非尾部”的链路错误。
        # 允许“尾部未闭合”的中间态（例如 assistant(tool_calls) 刚写入，tool_result 还没追加）。
        self._validate_payloads(trimmed, allow_incomplete_tail=True)

        return trimmed

    def _validate_payloads(self, payloads: list[LLMPayload], *, allow_incomplete_tail: bool) -> None:
        """校验 payloads 是否满足 OpenAI 兼容 messages 的基本结构约束。

        约束（对对话消息 USER/ASSISTANT/TOOL_RESULT 生效；SYSTEM/TOOL 作为 pinned 不参与链路判断）：
        - TOOL_RESULT 必须紧随带 tool_calls 的 ASSISTANT 之后；
        - 若 ASSISTANT 含 tool_calls，则必须补齐所有对应 call_id 的 TOOL_RESULT；
        - TOOL_RESULT 之后必须有 ASSISTANT 承接，才能进入下一条 USER。

        Args:
            payloads: 待校验的 payload 列表。
            allow_incomplete_tail: 是否允许“尾部未闭合”的中间态。
                - True：允许末尾是 ASSISTANT(tool_calls) 或 TOOL_RESULT（等待后续补齐）。
                - False：必须完整闭合。
        """

        pinned_roles = {ROLE.SYSTEM, ROLE.TOOL}
        convo = [p for p in payloads if p.role not in pinned_roles]

        def _err(message: str) -> None:
            roles = [p.role.value for p in convo]
            raise LLMContextError(f"LLM 上下文不合法: {message}; roles={roles}")

        idx = 0
        while idx < len(convo):
            payload = convo[idx]

            if payload.role == ROLE.USER:
                idx += 1
                continue

            if payload.role == ROLE.ASSISTANT:
                if idx == 0:
                    _err("对话不能以 assistant 开始")
                prev_role = convo[idx - 1].role
                if prev_role not in {ROLE.USER, ROLE.TOOL_RESULT}:
                    _err("assistant 前必须是 user 或 tool_result")

                tool_calls = [part for part in payload.content if isinstance(part, ToolCall)]
                if not tool_calls:
                    idx += 1
                    continue

                expected_ids: set[str] = set()
                for part in tool_calls:
                    if not part.id:
                        # 自动补全缺失的 ID 并保持同步
                        object.__setattr__(part, "id", f"call_{uuid.uuid4().hex[:8]}")
                    expected_ids.add(str(part.id))

                j = idx + 1
                if j >= len(convo):
                    if allow_incomplete_tail:
                        return
                    _err("assistant(tool_calls) 后缺少 tool_result")

                seen: set[str] = set()
                while j < len(convo) and convo[j].role == ROLE.TOOL_RESULT:
                    results = [part for part in convo[j].content if isinstance(part, ToolResult)]
                    if not results:
                        _err("tool_result payload 中缺少 ToolResult 内容")
                    for result in results:
                        if not result.call_id:
                            _err("ToolResult 缺少 call_id")
                        call_id = str(result.call_id)
                        if call_id not in expected_ids:
                            _err(f"ToolResult.call_id={call_id} 不匹配任何 tool_call")
                        if call_id in seen:
                            _err(f"重复的 ToolResult.call_id={call_id}")
                        seen.add(call_id)
                    j += 1

                missing = expected_ids - seen
                if missing:
                    if allow_incomplete_tail and j >= len(convo):
                        return
                    _err(f"tool_result 未覆盖全部 tool_call: missing={sorted(missing)}")

                # tool_result 后如果直接进入下一条 USER，是不合法的。
                # 但 tool_result 作为尾部是合法且常见的（下一条 assistant 将由本次请求生成）。
                if j < len(convo) and convo[j].role == ROLE.USER:
                    _err("tool_result 后不能直接跟 user（缺少 assistant 承接）")

                # 若后续还有消息且不是 USER，则必须是 ASSISTANT 才能继续对话。
                if j < len(convo) and convo[j].role != ROLE.ASSISTANT:
                    _err("tool_result 后只能是 assistant 或结束")

                idx = j
                continue

            if payload.role == ROLE.TOOL_RESULT:
                # 孤立 tool_result：一定非法（是否允许尾部取决于前面是否有 tool_calls）
                _err("孤立的 tool_result（未紧随 assistant.tool_calls）")

            _err(f"未知的对话角色: {payload.role}")

    def system(
        self,
        payloads: list[LLMPayload],
        content: Content | LLMUsable | list[Content | LLMUsable],
        position: int | None = None,
    ) -> list[LLMPayload]:
        """追加 SYSTEM payload，语义等同于 add_payload。"""

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
        """追加 TOOL payload，语义等同于 add_payload。"""

        return self.add_payload(
            payloads,
            LLMPayload(ROLE.TOOL, content),
            position=position,
        )

    def reminder(
        self,
        content: str | Text | list[str | Text],
        *,
        wrap_with_system_tag: bool = False,
    ) -> None:
        """仅登记 reminder，不立即注入到 payload 列表。

        reminder 作为仅次于 system 的固有提示词，不单独作为 role 出现。
        真正的注入会在后续 add_payload/system/tool 时由 _apply_reminders 完成。
        """

        items = content if isinstance(content, list) else [content]
        if self._reminders is None:
            self._reminders = []

        for item in items:
            text = item.text if isinstance(item, Text) else str(item)
            if wrap_with_system_tag:
                text = (
                    "<system_reminder>\n"
                    f"{text}\n"
                    "</system_reminder>"
                )
            text_part = Text(text)
            self._reminders.append(text_part)

    def _apply_reminders(self, payloads: list[LLMPayload]) -> list[LLMPayload]:
        """将 reminder 固定注入首个 USER 消息首段。"""

        if not self._reminders:
            return payloads

        reminder_parts: list[Content | LLMUsable] = [Text(item.text) for item in self._reminders]
        updated = list(payloads)

        user_index = next((idx for idx, p in enumerate(updated) if p.role == ROLE.USER), None)
        if user_index is None:
            return updated

        first_user = updated[user_index]
        existing = first_user.content

        matched_prefix_len = 0
        while (
            matched_prefix_len < len(reminder_parts)
            and matched_prefix_len < len(existing)
            and self._is_same_text_part(existing[matched_prefix_len], reminder_parts[matched_prefix_len])
        ):
            matched_prefix_len += 1

        if matched_prefix_len == len(reminder_parts):
            return updated

        rebuilt = reminder_parts + existing[matched_prefix_len:]
        updated[user_index] = LLMPayload(ROLE.USER, rebuilt)
        return updated

    def _is_same_text_part(self, left: Content | LLMUsable, right: Content | LLMUsable) -> bool:
        """判断两个内容片段是否为同一文本片段。"""

        return isinstance(left, Text) and isinstance(right, Text) and left.text == right.text

    def maybe_trim(
        self,
        payloads: list[LLMPayload],
        *,
        max_token_budget: int | None = None,
        token_counter: TokenCounter | None = None,
    ) -> list[LLMPayload]:
        """
        根据 max_payloads 和 max_token_budget 对 payloads 进行裁剪。

        裁剪策略：
        1. 保留开头的系统/工具消息（pinned prefix）。
        2. 将剩余消息按用户/助手对话分组，整体裁剪掉较早的对话组。
        3. 如果提供了 compression_hook，则在裁剪掉一批对话组后，调用该 hook 生成压缩后的消息，并将其插入剩余消息的开头。
        4. 如果 max_token_budget 仍然超出，则继续裁剪剩余的对话组，直到满足预算。
        """

        trimmed = payloads

        # 首先根据 max_payloads 进行裁剪
        if (
            self.max_payloads is not None
            and self.max_payloads > 0
            and len(trimmed) > self.max_payloads
        ):
            trimmed = self._trim_by_payloads(trimmed, self.max_payloads)

        # 然后根据 max_token_budget 进行裁剪
        if (
            max_token_budget is not None
            and max_token_budget > 0
            and token_counter is not None
            and token_counter(trimmed) > max_token_budget
        ):
            trimmed = self._trim_by_tokens(trimmed, max_token_budget, token_counter)

        return trimmed

    def _trim_by_tokens(
        self,
        payloads: list[LLMPayload],
        token_budget: int,
        token_counter: TokenCounter,
    ) -> list[LLMPayload]:
        """
        根据 token_budget 对 payloads 进行裁剪
        """

        pinned, tail = self._split_pinned_prefix(payloads)
        groups = self._build_qa_groups(tail)
        if not groups:
            return payloads

        kept_groups = list(groups)
        dropped_groups: list[list[LLMPayload]] = []

        # 先尝试直接裁剪对话组，保留 pinned 和尽可能多的对话组，直到满足 token 预算
        while len(kept_groups) > 1:
            candidate = pinned + self._flatten_groups(kept_groups)
            if token_counter(candidate) <= token_budget:
                break
            dropped_groups.append(kept_groups.pop(0))

        remaining_payloads = self._flatten_groups(kept_groups)

        # 如果提供了 compression_hook，则在裁剪掉一批对话组后，调用该 hook 生成压缩后的消息，并将其插入剩余消息的开头
        hook_payloads = self._apply_compression_hook(dropped_groups, remaining_payloads)
        if hook_payloads:
            combined = pinned + hook_payloads + remaining_payloads
            while len(kept_groups) > 1 and token_counter(combined) > token_budget:
                kept_groups.pop(0)
                remaining_payloads = self._flatten_groups(kept_groups)
                combined = pinned + hook_payloads + remaining_payloads
            return combined

        return pinned + remaining_payloads

    def _trim_by_payloads(
        self, payloads: list[LLMPayload], max_payloads: int
    ) -> list[LLMPayload]:
        """
        根据 max_payloads 对 payloads 进行裁剪
        """
        pinned, tail = self._split_pinned_prefix(payloads)
        groups = self._build_qa_groups(tail)
        if not groups:
            return payloads

        kept_groups = list(groups)
        dropped_groups: list[list[LLMPayload]] = []

        # 先尝试直接裁剪对话组，保留 pinned 和尽可能多的对话组，直到满足 max_payloads 约束
        while (
            len(kept_groups) > 1
            and self._payload_len(pinned, kept_groups) > max_payloads
        ):
            dropped_groups.append(kept_groups.pop(0))

        remaining_payloads = self._flatten_groups(kept_groups)

        # 如果提供了 compression_hook，则在裁剪掉一批对话组后，调用该 hook 生成压缩后的消息，并将其插入剩余消息的开头
        hook_payloads = self._apply_compression_hook(dropped_groups, remaining_payloads)
        if hook_payloads:
            remaining_payloads = self._flatten_groups(kept_groups)

        # 最后检查一次总长度，如果仍然超出 max_payloads，则继续裁剪剩余的对话组，直到满足约束
        while len(kept_groups) > 1 and (
            len(pinned) + len(hook_payloads) + len(remaining_payloads) > max_payloads
        ):
            kept_groups.pop(0)
            remaining_payloads = self._flatten_groups(kept_groups)

        return pinned + hook_payloads + remaining_payloads

    def _split_pinned_prefix(
        self, payloads: list[LLMPayload]
    ) -> tuple[list[LLMPayload], list[LLMPayload]]:
        """将 payloads 拆分为 pinned 消息和对话消息两部分。

        pinned 消息定义为：所有 SYSTEM 和 TOOL 角色的消息，无论其出现在列表的任何位置，
        均视为固定部分，始终被保留，不参与裁剪。
        对话消息为剩余的 USER 和 ASSISTANT 消息，按原始顺序保留。
        """
        pinned_roles = {ROLE.SYSTEM, ROLE.TOOL}
        pinned = [p for p in payloads if p.role in pinned_roles]
        tail = [p for p in payloads if p.role not in pinned_roles]
        return pinned, tail

    def _build_qa_groups(self, payloads: list[LLMPayload]) -> list[list[LLMPayload]]:
        """将消息分组。一个组作为一个不可分割的最小裁剪单位。

        分组策略：
        1. 每一个 USER 角色开始一个新组。
        2. 后续的 ASSISTANT 和 TOOL_RESULT 消息紧跟在该 USER 组内。
        3. 如果在第一个 USER 之前有孤立的消息（如历史遗留），它们会各自独立成组。
        """
        groups: list[list[LLMPayload]] = []
        current: list[LLMPayload] = []

        for payload in payloads:
            # 遇到 USER 角色，开启新组
            if payload.role == ROLE.USER:
                if current:
                    groups.append(current)
                current = [payload]
            elif not current:
                # 处理第一个 USER 之前的孤立消息
                groups.append([payload])
            else:
                # 归入当前组（确保 user-assistant-tool_result 连带关系）
                current.append(payload)

        if current:
            groups.append(current)

        return groups

    def _apply_compression_hook(
        self,
        dropped_groups: list[list[LLMPayload]],
        remaining_payloads: list[LLMPayload],
    ) -> list[LLMPayload]:
        """调用压缩 hook 生成压缩后的消息"""
        if not self.compression_hook or not dropped_groups:
            return []
        return self.compression_hook(dropped_groups, remaining_payloads)

    def _flatten_groups(self, groups: list[list[LLMPayload]]) -> list[LLMPayload]:
        """将分组扁平化为单一列表"""
        return [payload for group in groups for payload in group]

    def _payload_len(
        self, pinned: list[LLMPayload], groups: list[list[LLMPayload]]
    ) -> int:
        """
        计算有效负载的总长度，包括 pinned 消息和对话组消息
        """
        return len(pinned) + sum(len(group) for group in groups)
