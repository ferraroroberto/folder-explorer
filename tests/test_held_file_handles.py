"""A file handle kept on ``self`` is closed before the same method opens it again.

Reassigning the attribute leaves the old handle open: a leak per call, and on
Windows a file nothing can unlink or rename until the process exits.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A new object holds no earlier handle.
FRESH = ("__init__", "__enter__")


def _self_attr(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr
    return None


def _opens(node: ast.expr | None) -> bool:
    """``open(...)``, ``io.open(...)`` or ``some_path.open(...)`` anywhere in the assigned value."""
    if node is None:
        return False
    return any(
        isinstance(call, ast.Call)
        and (getattr(call.func, "id", None) == "open" or getattr(call.func, "attr", None) == "open")
        for call in ast.walk(node)
    )


def _unclosed(source: str) -> list[str]:
    hits: list[str] = []
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or func.name in FRESH:
            continue
        closes: list[tuple[str, int]] = []
        opens: list[tuple[str, int]] = []
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close":
                attr = _self_attr(node.func.value)
                if attr:
                    closes.append((attr, node.lineno))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _opens(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                opens.extend((attr, node.lineno) for attr in map(_self_attr, targets) if attr)
        hits.extend(
            "%s:%d self.%s" % (func.name, line, attr)
            for attr, line in opens
            if not any(closed == attr and at < line for closed, at in closes)
        )
    return hits


def test_no_method_reopens_a_handle_it_still_holds() -> None:
    hits = [
        "%s %s" % (path.relative_to(ROOT).as_posix(), hit)
        for folder in ("app", "src", "scripts")
        for path in sorted((ROOT / folder).rglob("*.py"))
        for hit in _unclosed(path.read_text(encoding="utf-8-sig"))
    ]
    assert hits == []


def test_the_check_sees_a_reopen_without_a_close() -> None:
    reopen = "class A:\n    def start(self):\n        self._log = open('x', 'ab')\n"
    assert _unclosed(reopen) == ["start:3 self._log"]
    assert _unclosed(reopen.replace("open('x', 'ab')", "path.open('ab')"))
    closed_first = reopen.replace("        self._log =", "        self._log.close()\n        self._log =")
    assert _unclosed(closed_first) == []
    assert _unclosed(reopen.replace("start", "__init__")) == []
