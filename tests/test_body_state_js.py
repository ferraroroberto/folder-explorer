"""The Search preview's body-state module is JavaScript, so its table test runs under Node inside the suite."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.no_window import NO_WINDOW

ROOT = Path(__file__).resolve().parent.parent


def test_body_state_table_passes_under_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: the body-state table test cannot run (the gate needs Node for jscpd as well)")
    done = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / "body_state.test.mjs")], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stdout + done.stderr
