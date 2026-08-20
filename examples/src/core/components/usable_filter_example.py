"""LLMUsable 细粒度过滤与作用域评估通用使用示例。

本示例展示如何在 Action / Tool 组件中声明通用作用域规则
（聊天器、聊天类型、平台、聊天流、内容格式等部署/设计期静态维度），
并使用 evaluate_usable_filter 引擎在不同上下文下进行静态评估。

注意：群组 / 用户等运行时实体名单**不**通过组件类属性声明，也不由本引擎评估。
它们由插件经配置 + 事件处理器（BEFORE_*_FILTER）或组件 ``go_activate``
在运行时自行判定。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.components.base.action import BaseAction  # noqa: E402
from src.core.components.base.tool import BaseTool  # noqa: E402
from src.core.components.types import ChatType  # noqa: E402
from src.core.components.usable_filter import (  # noqa: E402
    UsableFilterContext,
    evaluate_usable_filter,
)


class GroupScopedAction(BaseAction):
    """受聊天类型与聊天器作用域限制的通用 Action 示例。"""

    name = "group_scoped_action"
    description = "执行特定群聊上下文下的操作"

    # 1. 聊天类型作用域：仅限群聊
    chat_type = ChatType.GROUP

    # 2. Chatter 作用域黑名单：在特定测试 Chatter 中禁用
    chatter_deny = ["mock_debug_chatter"]

    # 群组 / 用户实体名单不在此声明：
    # 如需按群或按用户限制，请通过 BEFORE_ACTION_FILTER 事件处理器或
    # 读取插件配置在 go_activate 中判定。

    async def execute(
        self, target_id: Annotated[str, "目标实体标识"], action_type: Annotated[str, "操作类型"]
    ) -> tuple[bool, str]:
        """执行动作。"""
        return True, f"已对 {target_id} 执行 {action_type}"


class StreamScopedQueryTool(BaseTool):
    """受聊天流唯一标识限制的通用 Tool 示例。"""

    name = "stream_scoped_query_tool"
    description = "执行特定聊天流作用域下的查询操作"

    # 1. 聊天流白名单：仅在指定流中允许调用
    stream_allow = ["dedicated_stream_001"]

    async def execute(self) -> tuple[bool, dict[str, str]]:
        """执行查询。"""
        return True, {"status": "ok", "scope": "dedicated_stream_001"}


def run_example() -> None:
    """运行过滤引擎通用评估演示。"""
    # 场景 1: 群聊上下文下的群聊 Action（仅校验 chat_type / chatter 等静态维度）
    ctx_group = UsableFilterContext(
        stream_id="stream_group_100001",
        chat_type="group",
        platform="qq",
        group_id="100001",
        user_id="user_guest_99",
        chatter_name="default_chatter",
    )

    is_ok, reason = evaluate_usable_filter(GroupScopedAction, ctx_group)
    print(f"[场景 1] 群聊中的群聊 Action: 可用={is_ok}, 拒绝原因={reason}")

    # 场景 2: 私聊上下文调用群聊 Action（chat_type 不匹配，应被拒绝）
    ctx_private = UsableFilterContext(
        stream_id="stream_private_1",
        chat_type="private",
        platform="qq",
        user_id="user_guest_99",
        chatter_name="default_chatter",
    )

    is_ok, reason = evaluate_usable_filter(GroupScopedAction, ctx_private)
    print(f"[场景 2] 私聊中的群聊 Action: 可用={is_ok}, 拒绝原因={reason}")

    # 场景 3: 在专属流中调用专属查询工具
    ctx_dedicated_stream = UsableFilterContext(
        stream_id="dedicated_stream_001",
        chat_type="private",
        platform="qq",
        user_id="user_admin_01",
        chatter_name="default_chatter",
    )

    is_ok, reason = evaluate_usable_filter(
        StreamScopedQueryTool, ctx_dedicated_stream
    )
    print(f"[场景 3] 专属流中调用工具: 可用={is_ok}, 拒绝原因={reason}")

    # 群组 / 用户实体名单不再由引擎评估：
    # 需要按群或按用户做运行时过滤时，订阅 BEFORE_*_FILTER 事件改写组件集合，
    # 或在组件 go_activate 中读取配置判定（参见 snowluma_extension 插件）。


if __name__ == "__main__":
    run_example()
