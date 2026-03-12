"""prompt_api 示例脚本

展示 Prompt API 的注册与检索能力。

运行：
    uv run python examples/src/app/plugin_system/api/prompt_api_example.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.types import PromptTemplate


async def main() -> None:
    """演示 prompt_api 的基础功能。"""

    tmpl = PromptTemplate(name="demo.greet", template="你好，{name}！")
    prompt_api.register_template(tmpl)

    print(f"已注册模板: {prompt_api.list_templates()}")

    loaded = prompt_api.get_template("demo.greet")
    if loaded is None:
        raise RuntimeError("未能取回已注册模板")

    text = await loaded.set("name", "Neo-MoFox").build()
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
