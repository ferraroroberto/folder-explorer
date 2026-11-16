"""A failure on the loopback port pool leads with its own headline and ends the run in its own section."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e._port_pool import PORT_POOL_HEADLINE, is_port_pool_failure

ROOT = Path(__file__).resolve().parents[2]

_CONFTEST = "from tests.e2e._port_pool import pytest_runtest_makereport, pytest_terminal_summary  # noqa: F401\n"
_CASES = (
    "def test_exhausted():\n"
    "    raise RuntimeError('Page.goto: net::ERR_ADDRESS_IN_USE at http://127.0.0.1:9/')\n"
    "def test_node_side():\n"
    "    raise RuntimeError('APIRequestContext.get: connect ENOBUFS 127.0.0.1:9')\n"
    "def test_ordinary():\n"
    "    raise RuntimeError('Locator expected to be visible')\n"
)


@pytest.mark.parametrize("text", ["net::ERR_ADDRESS_IN_USE at http://127.0.0.1:1/", "connect ENOBUFS 127.0.0.1:1"])
def test_the_two_faces_of_the_exhaustion_are_recognised(text: str) -> None:
    assert is_port_pool_failure(text)


def test_an_ordinary_page_failure_is_not_one() -> None:
    assert not is_port_pool_failure("Locator expected to be visible")


def test_the_gate_output_names_the_port_pool_and_lists_only_those_tests(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(_CONFTEST)
    (tmp_path / "test_cases.py").write_text(_CASES)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_cases.py"], cwd=str(tmp_path),
                          env=env, capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = done.stdout
    assert done.returncode == 1, out + done.stderr
    assert "= PORT POOL EXHAUSTED =" in out
    section = out.split("= PORT POOL EXHAUSTED =", 1)[1].split("short test summary info", 1)[0]
    assert "test_cases.py::test_exhausted" in section and "test_cases.py::test_node_side" in section
    assert "test_cases.py::test_ordinary" not in section
    assert out.count(PORT_POOL_HEADLINE) >= 3  # each of the two failures, then the section
