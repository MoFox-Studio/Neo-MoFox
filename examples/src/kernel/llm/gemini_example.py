"""通过 kernel public 请求接口演示 Gemini Developer API 非流式文本调用。

从仓库根运行：uv run python examples/src/kernel/llm/gemini_example.py --help
实际运行会联网并可能计费；模型由调用方提供，不保证账户可用性。
密钥来自 GEMINI_API_KEY 或 getpass 隐藏输入，不打印配置、签名或原始响应。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from src.kernel.llm import (  # noqa: E402
    ROLE,
    LLMError,
    LLMPayload,
    LLMRequest,
    ModelSet,
    Text,
)
from src.kernel.llm.model_client import ModelClientRegistry  # noqa: E402
from src.kernel.llm.model_client.gemini_client import GeminiChatClient  # noqa: E402


def parse_args() -> argparse.Namespace:
    """读取显式模型、端点与生成参数；--help 不读取密钥或发请求。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL_ID"))
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"
        ),
        help="HTTPS 服务根，不含 API 版本或 generateContent 操作路径",
    )
    parser.add_argument(
        "--api-version",
        choices=("v1", "v1beta", "v1alpha"),
        default=os.environ.get("GEMINI_API_VERSION", "v1beta"),
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--prompt", default="请用一句话解释什么是异步编程。")
    args = parser.parse_args()
    if not args.model:
        parser.error("请设置 GEMINI_MODEL_ID 或 --model；示例不预设可用模型")
    if args.max_tokens <= 0:
        parser.error("--max-tokens 必须为正整数")
    return args


async def run_example(args: argparse.Namespace, api_key: str) -> None:
    """发出一个真实请求，并在同一事件循环内释放自有客户端资源。"""
    models: ModelSet = [
        {
            "api_provider": "GeminiNativeExample",
            "client_type": "gemini",
            "api_key": api_key,
            "base_url": args.base_url,
            "model_identifier": args.model,
            "timeout": 30.0,
            "max_retry": 1,
            "retry_interval": 1.0,
            # 价格为未配置占位值，不代表真实服务免费。
            "price_in": 0.0,
            "cache_hit_price_in": 0.0,
            "price_out": 0.0,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            # 这是示例本地预算，不声明目标模型真实上下文窗口。
            "max_context": 8192,
            "tool_call_compat": False,
            "force_stream_mode": False,
            "extra_params": {
                "trust_env": False,
                "force_ipv4": False,
                "gemini": {
                    "api_version": args.api_version,
                    "generation_config": {},
                },
            },
        }
    ]
    client = GeminiChatClient()
    try:
        # 默认 registry 也已装配 Gemini；这里注入独立实例以明确关闭所有权。
        request = LLMRequest(
            model_set=models,
            request_name="example.gemini.text",
            clients=ModelClientRegistry(gemini=client),
            enable_metrics=False,
        )
        request.add_payload(LLMPayload(ROLE.USER, Text(args.prompt)))
        response = await request.send(stream=False)
        print(await response)
        print(f"结束原因：{response.stop_reason}")
        # 不打印 provider_state；其中可能含不透明的 thought signature。
    finally:
        # 非流式请求完成/失败后 lease 已释放；退出 loop 前关闭 SDK 与 httpx。
        await client.aclose()


def main() -> int:
    """安全读取凭据后启动；失败只展示异常类型，不转储敏感上下文。"""
    args = parse_args()
    api_key = os.environ.get("GEMINI_API_KEY") or getpass("Gemini API key（隐藏输入）: ")
    if not api_key.strip():
        print("未提供 API key，未发送请求。", file=sys.stderr)
        return 2
    print("将向指定 Gemini 原生端点发出一次逻辑请求（可能计费）。")
    try:
        asyncio.run(run_example(args, api_key))
    except LLMError as exc:
        print(f"请求失败：{type(exc).__name__}；请检查配置与模型权限。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
