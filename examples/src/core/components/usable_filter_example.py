"""LLMUsable 细粒度过滤与作用域评估通用使用示例。

本示例展示如何在 Action / Tool 组件中声明通用作用域规则
（包括群组实体、聊天流、用户与平台白名单/黑名单等），并使用
evaluate_usable_filter 引擎在不同上下文下进行静态评估。
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
    """受群组与用户作用域限制的通用 Action 示例。"""

    name = "group_scoped_action"
    description = "执行特定群组与指定用户作用域下的操作"

    # 1. 聊天类型作用域：仅限群聊
    chat_type = ChatType.GROUP

    # 2. 群组作用域白名单：仅在指定群生效
    group_allow = [100001, 100002]

    # 3. 用户作用域白名单：仅限指定用户
    user_allow = ["user_admin_01", "user_admin_02"]

    # 4. Chatter 作用域黑名单：在特定测试 Chatter 中禁用
    chatter_deny = ["mock_debug_chatter"]

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
    # 场景 1: 非授权用户在目标群组中触发
    ctx_unauthorized_user = UsableFilterContext(
        stream_id="stream_group_100001",
        chat_type="group",
        platform="qq",
        group_id="100001",
        user_id="user_guest_99",
        chatter_name="default_chatter",
    )

    is_ok, reason = evaluate_usable_filter(
        GroupScopedAction, ctx_unauthorized_user
    )
    print(f"[场景 1] 非授权用户在目标群: 可用={is_ok}, 拒绝原因={reason}")

    # 场景 2: 授权用户在目标群组中触发
    ctx_authorized_user = UsableFilterContext(
        stream_id="stream_group_100001",
        chat_type="group",
        platform="qq",
        group_id="100001",
        user_id="user_admin_01",
        chatter_name="default_chatter",
    )

    is_ok, reason = evaluate_usable_filter(
        GroupScopedAction, ctx_authorized_user
    )
    print(f"[场景 2] 授权用户在目标群: 可用={is_ok}, 拒绝原因={reason}")

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


if __name__ == "__main__":
    run_example()
