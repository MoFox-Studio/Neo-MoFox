"""``:dedupe_tool_call`` 默认实现——构造签名 + 检查/登记 ``seen_signatures``。"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.base import BaseEventHandler
from src.kernel.event import EventDecision

from ....utils.event_publisher import NdfcEvent
from ....utils.tool_flow import _build_call_dedupe_key


class DedupeToolCallDefaultHandler(BaseEventHandler):
    """:dedupe_tool_call 的默认实现。

    用 ``_build_call_dedupe_key(call)`` 构造稳定签名（``call_name:{json}``），
    检查是否在 ``seen_signatures`` 中；若在则 ``is_duplicate=True``，否则把签名
    加入 ``seen_signatures``。

    ``seen_signatures`` 是共享可变对象（跨轮去重集合），handler 直接 ``.add()`` 修改
    其内部状态——即便 handler 返回 ``PASS``，修改也会生效（依据 §7.6 共享可变对象约束）。
    """

    name = "dedupe_tool_call_default"
    description = "默认 dedupe_tool_call：构造签名 + 检查/登记 seen_signatures"
    weight = 0
    init_subscribe = [NdfcEvent.DEDUPE_TOOL_CALL]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """执行 dedupe_tool_call，把 is_duplicate 填入 payload。"""
        try:
            call = params["call"]
            seen_signatures = params["seen_signatures"]

            args = call.args if isinstance(call.args, dict) else {}
            # 去重时忽略 reason 字段：相同动作不同理由仍视为同一调用
            dedupe_args = (
                {k: v for k, v in args.items() if k != "reason"}
                if isinstance(args, dict)
                else args
            )
            dedupe_key = _build_call_dedupe_key(call.name, dedupe_args)

            if dedupe_key in seen_signatures:
                params["is_duplicate"] = True
            else:
                seen_signatures.add(dedupe_key)
                params["is_duplicate"] = False
            return EventDecision.SUCCESS, params
        except Exception:
            return EventDecision.PASS, params


__all__ = ["DedupeToolCallDefaultHandler"]
