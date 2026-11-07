"""request_inspector_api 示例脚本。

展示插件如何读取请求监视快照和单条请求详情。

运行：
    uv run python examples/src/app/plugin_system/api/request_inspector_api_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import request_inspector_api


def main() -> None:
    """演示读取监视器快照和最新请求详情。"""
    snapshot = request_inspector_api.get_request_inspector_snapshot()
    requests = snapshot["records"]
    print(f"当前捕获 {len(requests)} 条请求，容量 {snapshot['capacity']} 条")

    if not requests:
        print("当前没有已捕获的请求")
        return

    latest = requests[-1]
    detail = request_inspector_api.get_request_inspector_request(latest["id"])
    if detail is None:
        print("请求在查询期间已被清空或淘汰")
        return

    print(f"最新请求: #{detail['id']} {detail['api_name']} ({detail['model']})")
    print(f"请求参数: {detail['params']}")


if __name__ == "__main__":
    main()
