"""在项目根目录启动 Booku 到 Engram 的隔离迁移、预览或索引续跑。"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    """转交全部命令行参数，复用插件迁移器的确认、校验和退出状态。"""
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    runpy.run_module("plugins.engram_memory.scripts.migrate_booku", run_name="__main__")


if __name__ == "__main__":
    main()