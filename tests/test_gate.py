"""The ship gate runs every ratchet the repo declares, and the static ratchet
reports an undetectable tree as NOT CONFIRMED (exit 3) rather than a pass."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _enabled_ratchets() -> list[str]:
    """Names of the `[<name>_ratchet]` tables in .fleet.toml with `enabled = true`."""
    text = (ROOT / ".fleet.toml").read_text(encoding="utf-8")
    tables = re.split(r"^\[(\w+_ratchet)\]\s*$", text, flags=re.M)[1:]
    return [
        name
        for name, body in zip(tables[0::2], tables[1::2])
        if re.search(r"^enabled\s*=\s*true\b", body, flags=re.M)
    ]


def test_gate_runs_every_enabled_ratchet() -> None:
    gate = (ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    names = _enabled_ratchets()
    assert len(names) == 10
    for name in names:
        stage = re.compile(r'^Invoke-Stage\s+"[^"]+"\s+\{\s*&\s*\$py\s+"scripts/%s\.py"\s*\}\s*$' % name, re.M)
        assert stage.search(gate), "%s has no gate stage" % name


def test_static_ratchet_without_npx_is_not_confirmed() -> None:
    env = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
    env["PATH"] = ""
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "static_ratchet.py")],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.returncode == 3
    assert "NOT CONFIRMED" in proc.stdout + proc.stderr
