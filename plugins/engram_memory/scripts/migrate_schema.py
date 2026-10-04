"""将独立 Engram v1、v2、v3 或 v4 副本迁移到 v5，保留来源库与记忆记录。"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "main.py").is_file()
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if not __package__:
    __package__ = ".".join(Path(__file__).resolve().relative_to(ROOT).parts[:-1])

from ..vnext.schema import SCHEMA_VERSION  # noqa: E402
from ..vnext.schema_migration import migrate_snapshot  # noqa: E402


def _resolve_project_path(path: Path) -> Path:
    """将相对输入解析为项目根目录下的绝对路径。"""
    candidate = path if path.is_absolute() else ROOT / path
    if candidate.is_symlink():
        raise ValueError("迁移路径不能是符号链接")
    return candidate.resolve()


def _configured_production_path() -> Path:
    """返回配置指向的生产数据库路径，只读取路径字段。"""
    default_path = ROOT / "data/engram_memory/vnext.db"
    config_path = ROOT / "config/plugins/engram_memory/config.toml"
    if not config_path.is_file():
        return default_path.resolve()
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError("无法安全读取 Engram 数据库路径") from error

    storage = config.get("storage", {})
    if not isinstance(storage, dict):
        raise ValueError("Engram 存储配置格式无效")
    configured_path = storage.get("vnext_db_path")
    if configured_path is None:
        return default_path.resolve()
    if not isinstance(configured_path, str) or not configured_path.strip():
        raise ValueError("Engram 数据库路径配置无效")
    path = Path(configured_path)
    return (path if path.is_absolute() else ROOT / path).resolve()


def _validate_paths(source: Path, target: Path) -> tuple[Path, Path]:
    """检查副本路径、输出路径及当前生产数据库隔离。"""
    source = _resolve_project_path(source).resolve(strict=True)
    target = _resolve_project_path(target)
    production_path = _configured_production_path()
    if source == production_path or target == production_path:
        raise ValueError("来源和目标必须避开当前配置使用的 Engram 数据库")
    if not source.is_file():
        raise ValueError("来源必须是独立数据库文件")
    if source == target or target.exists():
        raise ValueError("目标必须是尚不存在的独立文件")
    if any(Path(f"{source}{suffix}").exists() for suffix in ("-wal", "-shm")):
        raise ValueError("来源副本仍有 SQLite sidecar；请先完成离线备份")
    return source, target


def migrate_copy(source: Path, target: Path) -> dict[str, object]:
    """创建 v5 数据库副本并检查数据守恒，不修改来源或替换生产路径。"""
    source, target = _validate_paths(source, target)
    return migrate_snapshot(source, target)


def main() -> None:
    """显式接收副本输入输出路径并打印脱敏守恒结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    source, target = _validate_paths(args.source, args.target)
    print(f"将从来源数据库创建新的 Schema v{SCHEMA_VERSION} 副本；来源文件不会修改。")
    if input("输入 yes 确认继续：").strip().lower() != "yes":
        raise SystemExit("已取消迁移。")
    print(json.dumps(migrate_copy(source, target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
