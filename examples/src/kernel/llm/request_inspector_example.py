"""请求监视器离线示例：原样保留字段的输入、响应及缓冲快照。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 直接运行本文件无需先安装主程序包。
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from src.kernel.llm.request_inspector import RequestInspector


def main() -> None:
    """仅用虚构凭据演示请求观测，不使用 SDK、HTTP 服务或真实模型。"""
    inspector = RequestInspector()
    params = {
        "model": "offline-demo",
        "messages": [{"role": "user", "content": "展示一个请求体结构"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ],
        "extra_headers": {
            "Authorization": "Bearer demo-not-a-real-key",
            # 以下都是虚构值；监视器不脱敏，真实凭据也会原样出现在快照中。
            "x-goog-api-key": "demo-independent-vendor-key",
        },
    }
    record_id = inspector.capture("chat.completions.create", params, native=True)
    inspector.start(record_id, params)
    inspector.raw(record_id, {"choices": [{"message": {"content": "演示结果"}}]})
    inspector.finish(record_id, {"kind": "chat", "message": "演示结果"})
    detail = inspector.get_record(record_id)
    assert detail is not None
    assert detail["params"] == params
    assert detail["capture_info"]["redacted"] is False
    print(json.dumps(detail, ensure_ascii=False, indent=2))
    print(json.dumps(inspector.snapshot(), ensure_ascii=False, indent=2))
    assert inspector.clear_snapshot()["cleared_count"] == 1


if __name__ == "__main__":
    main()
