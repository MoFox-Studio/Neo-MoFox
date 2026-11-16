"""固定本批子 pytest 模块的注解与说明约束，不导入或执行被审计模块。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


PROBE_FILES = (
    ("test/kernel/llm/test_handoff_fixture_cleanup.py", 2),
    ("test/core/transport/test_diagnostic_fixture_cleanup.py", 2),
    ("test/kernel/llm/test_pr181_gemini_fixture_cleanup.py", 2),
)
PROBE_NAMES = {"PROBE", "_PROBE", "_OFFLINE_PYTEST_CONFTEST", "_OFFLINE_PYTEST_CASE"}


def _generated_sources(path: Path) -> list[tuple[str, str]]:
    """提取具名探针和直接写出的源码字面量，排除报告数据与动态表达式。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    sources: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name in PROBE_NAMES for name in names):
                assert isinstance(node.value.value, str), (path, names)
                sources.append((names[0], node.value.value))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_text" and node.args):
            continue
        if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        source = node.args[0].value
        parsed = ast.parse(source, filename=f"{path}:write_text:{node.lineno}")
        if any(isinstance(child, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                  ast.AsyncFunctionDef, ast.ClassDef)) for child in parsed.body):
            sources.append((f"write_text:{node.lineno}", source))
    return sources


def _contract_gaps(source: str, label: str) -> list[str]:
    """检查生成模块及其全部函数/类，编译只检查语法，不运行任何探针。"""
    tree = ast.parse(source, filename=label)
    compile(tree, label, "exec", dont_inherit=True)
    gaps: list[str] = []
    if not ast.get_docstring(tree):
        gaps.append(f"{label}:module:docstring")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        prefix = f"{label}:{node.lineno}:{node.name}"
        if not ast.get_docstring(node):
            gaps.append(f"{prefix}:docstring")
        if isinstance(node, ast.ClassDef):
            continue
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None]
        gaps.extend(f"{prefix}:argument:{arg.arg}" for arg in arguments
                    if arg.arg not in {"self", "cls"} and arg.annotation is None)
        if node.returns is None:
            gaps.append(f"{prefix}:return")
    return gaps


@pytest.mark.parametrize("relative_path, expected_sources", PROBE_FILES)
def test_generated_pytest_modules_follow_project_conventions(
    relative_path: str, expected_sources: int,
) -> None:
    """覆盖全部六份生成源码，防止提取器漏掉模块而产生空集合假绿。"""
    root = Path(__file__).resolve().parents[1]
    sources = _generated_sources(root / relative_path)
    assert len(sources) == expected_sources, (relative_path, sources)
    gaps = [gap for label, source in sources
            for gap in _contract_gaps(source, f"{relative_path}:{label}")]
    assert not gaps, "\n".join(gaps)


def test_probe_checker_rejects_missing_function_contracts() -> None:
    """未注解且无说明的函数必须留下三项独立诊断，不能被检查器忽略。"""
    source = '"""测试模块。"""\ndef observe(value):\n    return value\n'
    gaps = _contract_gaps(source, "untyped")
    assert set(gaps) == {
        "untyped:2:observe:docstring", "untyped:2:observe:argument:value",
        "untyped:2:observe:return",
    }


def test_probe_checker_accepts_complete_function_contracts() -> None:
    """完整模块、函数说明及注解可以通过，不要求改写测试的业务操作。"""
    source = ('"""测试模块。"""\ndef observe(value: int) -> int:\n'
              '    """原样返回输入。"""\n    return value\n')
    assert _contract_gaps(source, "typed") == []
