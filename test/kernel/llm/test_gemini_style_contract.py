"""Gemini-owned declarations must follow the project's annotation/docstring rules."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

from src.kernel.llm.request import LLMRequest

import pytest

from src.kernel.llm.response import LLMResponse

ROOT = Path(__file__).resolve().parents[3]
SCOPED_FUNCTIONS = {
    "request_execution.py": {"terminal", "resume"},
    "response.py": {
        "_close_managed_after", "generate", "consume", "__aexit__", "__await__",
        "__aiter__", "_apply_stream_result", "add_payload", "_maybe_record_stream_stats",
    },
    "request.py": {"add_payload", "send"},
    "monitor.py": {"RequestTimer.__init__", "RequestTimer.__enter__", "RequestTimer.__exit__"},
    "observation.py": {"recorder"},
    "model_client/registry.py": {"__post_init__"},
}
NEW_MODULES = [
    "provider_state.py", "request_attempt.py", "model_client/gemini_client.py",
    "model_client/gemini_codec.py", "payload/payload.py", "model_client/base.py",
]


def _scope(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Find declarations with class-qualified names without importing source files."""
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, owner: str = "") -> None:
        """Keep class ownership while also visiting nested callback functions."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((owner + child.name, child))
                visit(child)
            else:
                visit(child, owner)

    visit(tree)
    return found


@pytest.mark.parametrize("relative", sorted(SCOPED_FUNCTIONS) + NEW_MODULES)
def test_owned_source_annotations_and_docstrings(relative: str) -> None:
    """Check only this Gemini change's declarations, not unrelated historical code."""
    source = ROOT / "src/kernel/llm" / relative
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), f"{relative}: missing module description"
    selected = SCOPED_FUNCTIONS.get(relative)
    checked: set[str] = set()
    issues: list[str] = []
    for qualified, node in _scope(tree):
        if selected is not None and qualified not in selected and node.name not in selected:
            continue
        checked.add(qualified if qualified in (selected or set()) else node.name)
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if node.args.vararg:
            arguments += [node.args.vararg]
        if node.args.kwarg:
            arguments += [node.args.kwarg]
        missing = [arg.arg for arg in arguments if arg.arg not in {"self", "cls"} and arg.annotation is None]
        if missing or node.returns is None or not ast.get_docstring(node):
            issues.append(f"{node.lineno}:{qualified}: untyped={missing}, return={node.returns is not None}, doc={bool(ast.get_docstring(node))}")
    if selected is not None:
        assert selected <= checked, f"Scoped declarations disappeared: {selected - checked}"
    assert not issues, "\n".join(issues)


@pytest.mark.parametrize("name", ["stream_events", "__aiter__", "stream_with_callback", "stream_events_with_callback", "stream_with_buffer"])
def test_stream_wrapper_keeps_public_signature(name: str) -> None:
    """Generic wrappers must retain the original runtime signature and documentation."""
    method = getattr(LLMResponse, name)
    assert inspect.signature(method) == inspect.signature(method.__wrapped__)
    assert inspect.getdoc(method) == inspect.getdoc(method.__wrapped__)
    assert bool(method.__annotations__)


def test_request_send_return_annotation_is_resolvable() -> None:
    """Keep the public response type available to runtime annotation consumers."""
    assert get_type_hints(LLMRequest.send)["return"] is LLMResponse
