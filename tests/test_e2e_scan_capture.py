"""The e2e fixture scan's output is decoded as UTF-8 even when the parent runs in the cp1252 locale mode."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Cyrillic "Ё" is the UTF-8 bytes d0 81, and 0x81 is undefined in cp1252, so a cp1252 decode raises.
_CHILD = "import sys; sys.stdout.write('scan \\u0401 ok\\n'); sys.stderr.write('warn \\u0401\\n')"
_PARENT = (
    "import sys\n"
    "from pathlib import Path\n"
    "from tests.e2e._scan_capture import run_utf8\n"
    "r = run_utf8([sys.executable, '-c', sys.argv[1]], cwd=Path.cwd(), env={**__import__('os').environ, 'PYTHONUTF8': '1'}, timeout=30)\n"
    "sys.stdout.buffer.write(repr((r.stdout, r.stderr)).encode('ascii', 'backslashreplace'))\n"
)


def test_non_ascii_child_output_survives_a_cp1252_parent() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    # -X utf8=0 pins the parent to the Windows locale code page, the mode pytest runs in by default.
    done = subprocess.run([sys.executable, "-X", "utf8=0", "-c", _PARENT, _CHILD], cwd=str(ROOT), env=env,
                          capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert b"Exception in thread" not in done.stderr and b"UnicodeDecodeError" not in done.stderr
    assert done.stdout == repr(("scan Ё ok\n", "warn Ё\n")).encode("ascii", "backslashreplace")
