"""锁定本分支新增测试与公开说明的规范边界，防止收口后再次遗漏。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LLM_TEST_FILES = (
    "test_gemini_boundaries.py",
    "test_gemini_client.py",
    "test_gemini_context_boundaries.py",
    "test_gemini_coverage_edges.py",
    "test_gemini_errors.py",
    "test_gemini_execution_edges.py",
    "test_gemini_final_review.py",
    "test_gemini_lifecycle.py",
    "test_gemini_sdk_contract.py",
    "test_managed_stream.py",
    "test_managed_stream_edges.py",
)
OWNED_TESTS = tuple("test/kernel/llm/" + name for name in LLM_TEST_FILES) + (
    "test/kernel/jev/test_client_fallbacks_and_api.py",
    "test/kernel/jev/test_client_response_and_retry.py",
    "test/kernel/llm/test_review_response_ownership.py",
    "test/kernel/llm/test_review_gemini_resources.py",
    "test/kernel/llm/test_review_gemini_protocol.py",
    "test/app/test_review_gemini_runtime.py",
    "test/kernel/llm/test_review_norm_contract.py",
)


def declaration_issues(tree: ast.AST) -> list[str]:
    """返回指定语法树中声明缺失的文档、参数和返回注解，不扫描其他文件。"""
    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        prefix = f"{node.lineno}:{node.name}"
        if not ast.get_docstring(node):
            issues.append(prefix + ": 缺少文档字符串")
        if isinstance(node, ast.ClassDef):
            continue
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if node.args.vararg is not None:
            arguments += [node.args.vararg]
        if node.args.kwarg is not None:
            arguments += [node.args.kwarg]
        missing = [
            arg.arg for arg in arguments
            if arg.arg not in {"self", "cls"} and arg.annotation is None
        ]
        if missing:
            issues.append(prefix + ": 缺少参数注解 " + ", ".join(missing))
        if node.returns is None:
            issues.append(prefix + ": 缺少返回注解")
    return issues


@pytest.mark.parametrize("relative", OWNED_TESTS)
def test_owned_test_declarations_are_documented_and_typed(relative: str) -> None:
    """本分支新增文件中的测试、局部替身及类均遵循声明硬规则。"""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), relative + ": 缺少模块简介"
    assert not (issues := declaration_issues(tree)), relative + "\n" + "\n".join(issues)


def test_rerank_changed_test_declarations_are_typed() -> None:
    """仅检查本分支修改的 rerank 回归子树，不追溯同文件历史测试。"""
    relative = "test/kernel/llm/test_openai_client.py"
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "test_body_cannot_override_core_rerank_fields"
    ]
    assert len(matches) == 1, "对应回归声明必须保留且唯一"
    assert not (issues := declaration_issues(matches[0])), "\n".join(issues)


@pytest.mark.parametrize(
    ("relative", "name", "parameters"),
    [
        ("src/kernel/llm/model_client/gemini_client.py", "create",
         ("model_name", "payloads", "tools", "request_name", "model_set", "stream")),
        ("src/kernel/llm/model_client/gemini_codec.py", "encode_payloads",
         ("payloads", "target", "tools")),
        ("src/kernel/llm/model_client/gemini_codec.py", "decode_response",
         ("response", "target", "require_finish")),
        ("src/kernel/llm/request_attempt.py", "__init__",
         ("source", "timeout", "started_at", "context", "terminal", "resume")),
    ],
)
def test_public_parameter_contract_is_explained(
    relative: str, name: str, parameters: tuple[str, ...],
) -> None:
    """非平凡公开入口逐一说明命名参数，而非只写一句笼统职责。"""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    nodes = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(nodes) == 1
    documentation = ast.get_docstring(nodes[0]) or ""
    assert all(parameter in documentation for parameter in parameters)


def test_result_and_registry_documentation_matches_current_contract() -> None:
    """结构化结果和默认 Gemini 装配已进入实现，说明不能退回旧契约。"""
    from src.kernel.llm.model_client.base import ChatModelClient
    from src.kernel.llm.model_client.registry import ModelClientRegistry
    from src.kernel.llm.request_execution import normalize_client_create_result

    assert "ModelClientResult" in (ChatModelClient.create.__doc__ or "")
    assert "ModelClientResult" in (normalize_client_create_result.__doc__ or "")
    assert "Gemini" in (ModelClientRegistry.get_client_for_model.__doc__ or "")
    assert "gemini/bedrock 系列别名" not in (ModelClientRegistry.get_client_for_model.__doc__ or "")
