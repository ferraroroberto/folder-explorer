"""The front end's pure modules are JavaScript, so their tests run under Node inside the suite.

The Search preview's body-state table, the Markdown editor's text helpers and the document API answer classifier.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["body_state.test.mjs", "md_source.test.mjs", "doc_answer.test.mjs"])
def test_the_js_module_tests_pass_under_node(name: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: %s cannot run (the gate needs Node for jscpd as well)" % name)
    done = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / name)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stdout + done.stderr
