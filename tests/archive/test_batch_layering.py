"""The document-building verbs share one small base and never import each other.

``common`` says so in its docstring; this makes the claim fail a build
instead of waiting for the next audit to find a verb reaching through ``verbs``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "email" / "batch"

# Every module that builds on the shared base. ``verbs`` is the one verb module
# they must not reach through.
BASE_USERS = ["common", "move", "dismiss", "draft", "find", "mail_filter", "send", "sync"]


def _imports_batch(source: str) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.module == "src.email.batch.verbs":
                hits.append(f"from {node.module} import ...")
            elif node.module == "src.email.batch":
                hits.extend(
                    f"from src.email.batch import {a.name}" for a in node.names if a.name == "verbs"
                )
        elif isinstance(node, ast.Import):
            hits.extend(f"import {a.name}" for a in node.names if a.name == "src.email.batch.verbs")
    return hits


@pytest.mark.parametrize("module", BASE_USERS)
def test_the_module_does_not_import_the_batch_verb_module(module):
    source = (PACKAGE / f"{module}.py").read_text(encoding="utf-8")
    assert _imports_batch(source) == []


def test_the_check_sees_each_way_of_importing_batch():
    assert _imports_batch("from src.email.batch.verbs import SCHEMA_VERSION")
    assert _imports_batch("from src.email.batch import verbs")
    assert _imports_batch("import src.email.batch.verbs")
    assert not _imports_batch("from src.email.batch.common import header")
