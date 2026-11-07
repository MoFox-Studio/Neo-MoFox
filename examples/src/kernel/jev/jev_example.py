"""演示 Jev System One 独立客户端。

Jev 接收一个 state 与一组 typed question，返回结构化的 Noul/Choice/Score 判断。
它不生成聊天文本，也不接入 LLM 的 ModelClientRegistry。

运行前请在服务端设置环境变量：
    TYPESAFE_API_KEY=<你的 TypeSafe API key>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.app.plugin_system.api.jev_api import evaluate_jev
from src.kernel.jev import JevConfigurationError, choice, noul, score


async def main() -> None:
    """对一个工单同时评估紧急度、负责团队和严重等级。"""
    response = await evaluate_jev(
        state={
            "ticket": "所有客户的生产 API 请求都返回 500，订单无法处理。",
            "service": "order-api",
        },
        questions={
            "urgent": noul("该工单是否描述需要立即处理的生产事故？"),
            "team": choice(
                "哪个团队应首先处理？",
                {
                    "technical": "产品缺陷、集成或服务故障",
                    "billing": "付款、账单或退款",
                    "other": "不属于上述类别",
                },
            ),
            "severity": score(
                "根据影响范围和业务中断程度评估严重性。",
                ["影响轻微且有替代方案", "部分用户受影响", "服务全面中断"],
            ),
        },
    )

    print(f"model={response.model}")
    print(f"urgent_probability={response.answer('urgent').noul}")
    print(f"team={response.answer('team').choice}")
    print(f"severity_score={response.answer('severity').score}")
    print(f"usage={response.usage}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except JevConfigurationError as exc:
        # 最常见的原因是服务端没有设置 TYPESAFE_API_KEY。
        print(f"Jev 配置错误：{exc}")
        raise SystemExit(1) from exc
