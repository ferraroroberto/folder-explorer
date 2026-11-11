"""Settings' client-side defaults are JavaScript, so their table test runs under Node inside the suite, and a test holds them equal to the server's."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src import config
from src.no_window import NO_WINDOW

ROOT = Path(__file__).resolve().parent.parent.parent
MODULE = ROOT / "app" / "webapp" / "static" / "config-defaults.js"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: the Settings defaults test cannot run (the gate needs Node for jscpd as well)")
    return subprocess.run([node, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                          creationflags=NO_WINDOW)


def test_config_defaults_table_passes_under_node() -> None:
    done = _run(["--test", str(ROOT / "tests" / "js" / "config_defaults.test.mjs")])
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_pages_defaults_equal_the_servers() -> None:
    script = "import(process.argv[1]).then((m) => console.log(JSON.stringify(m.DEFAULTS)))"
    done = _run(["-e", script, MODULE.as_uri()])
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == config.DEFAULTS
