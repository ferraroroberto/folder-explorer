"""Standalone restart for ``tray.bat --restart`` on a PC without the shared tray helper.

``tray.bat`` calls two steps around its own ``Start-Process``:

    python -m scripts.standalone_restart stop     # end THIS app's tray + server, nothing else
    python -m scripts.standalone_restart verify   # the fresh server answers with HEAD's build

and, for the shared helper's launch line in ``tray.bat``, one read-only step:

    python -m scripts.standalone_restart ports    # the owned ports, comma-separated, from the config

Run from the repo root with this checkout's ``.venv`` interpreter. Stdlib plus ``src/`` only.

**Which processes ``stop`` ends** (``select_targets``): a process is this app's only if its
command line carries this checkout's ``.venv`` directory (a venv-launched interpreter re-execs
the base python, so the image path is the shared one and only the command line still says
which app it is). Of those, it ends

* the tray: the command line also matches ``-m app.tray``, and
* whatever listens on this app's configured port (the web app) -- an orphan that outlived its
  tray and would otherwise keep serving the old build.

Never a match by a bare process name, never a PID found through another app's port, never this
script or its ancestors. A tray is ended with its whole tree (``taskkill /T``), which takes the
web app child with it.

Any step that cannot establish a fact exits nonzero with its own message: 1 = failed, 3 = could
not tell (process list unreadable, or HEAD's build unknown), so a restart is never reported as
done on a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from src import config
from src.build_info import resolve_git_sha
from src.no_window import NO_WINDOW

# abspath, not resolve(): the command lines to match carry the path tray.bat was started under,
# which a junction or subst drive would make differ from the resolved one.
ROOT = Path(os.path.abspath(__file__)).parent.parent
VENV_DIR = ROOT / ".venv"
APP_ID = "folder-explorer"
# The tray's launch arguments in tray.bat (TRAY_LAUNCH).
TRAY_PATTERN = re.compile(r"-m\s+app\.tray\b", re.IGNORECASE)
STOP_TIMEOUT_S = 15.0
VERIFY_TIMEOUT_S = 30.0
POLL_S = 0.5
# A cold PowerShell listing every process can take tens of seconds on a scanned PC; this only ends a hung one.
POWERSHELL_TIMEOUT_S = 60.0
EXIT_FAILED = 1
EXIT_NOT_CONFIRMED = 3


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    cmdline: str


def _norm(path_text: str) -> str:
    return path_text.replace("/", "\\").lower()


def under_venv(cmdline: str, venv_dir: Path) -> bool:
    """True when ``cmdline`` runs something inside ``venv_dir``.

    The trailing separator keeps a sibling such as ``.venv-old`` from matching.
    """
    return _norm(str(venv_dir)).rstrip("\\") + "\\" in _norm(cmdline)


def ancestors(processes: list[Proc], pid: int) -> set[int]:
    """``pid`` and every parent above it, so the script never ends its own caller chain."""
    parent = {p.pid: p.ppid for p in processes}
    chain = {pid}
    while parent.get(pid, 0) in parent and parent[pid] not in chain:
        pid = parent[pid]
        chain.add(pid)
    return chain


def select_targets(
    processes: list[Proc], listener_pids: set[int], venv_dir: Path, protected: set[int]
) -> list[int]:
    """The PIDs of this app to end: its tray plus the owners of its ports, both under ``venv_dir``."""
    owned = {p.pid for p in processes if under_venv(p.cmdline, venv_dir)}
    trays = {p.pid for p in processes if p.pid in owned and TRAY_PATTERN.search(p.cmdline)}
    return sorted(((trays | listener_pids) & owned) - protected)


def _powershell(script: str) -> str:
    exe = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    prefix = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    result = subprocess.run(
        [str(exe), "-NoProfile", "-NonInteractive", "-Command", prefix + script],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=POWERSHELL_TIMEOUT_S,
        check=False,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"powershell exited {result.returncode}: {detail}")
    return result.stdout.decode("utf-8", "replace")


def list_processes() -> list[Proc]:
    out = _powershell(
        "Get-CimInstance Win32_Process | ForEach-Object "
        "{ [pscustomobject]@{ pid = $_.ProcessId; ppid = $_.ParentProcessId; cmd = $_.CommandLine } } "
        "| ConvertTo-Json -Compress"
    ).strip()
    rows = json.loads(out) if out else []
    if isinstance(rows, dict):
        rows = [rows]
    return [Proc(int(r["pid"]), int(r["ppid"]), r["cmd"] or "") for r in rows]


def listener_pids(ports: list[int]) -> set[int]:
    if not ports:
        return set()
    # "No matching connections" is ObjectNotFound: an empty answer. Any other error is a failed
    # read and must surface, or a missing cmdlet would pass as "nothing is listening". The
    # explicit exit 0 is needed: a handled error still ends a -Command run with exit 1.
    out = _powershell(
        "try { Get-NetTCPConnection -State Listen -LocalPort "
        + ",".join(str(p) for p in ports)
        + " -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess } "
        "catch { if ($_.CategoryInfo.Category -ne 'ObjectNotFound') { throw } }; exit 0"
    )
    return {int(line) for line in out.split() if line.strip().isdigit()}


def app_ports() -> list[int]:
    """The ports this app owns, from the config resolver (``config.json`` or its override, else the default)."""
    return [int(config.load()["port"])]


def _targets_now() -> list[int]:
    processes = list_processes()
    return select_targets(processes, listener_pids(app_ports()), VENV_DIR, ancestors(processes, os.getpid()))


def _end_tree(pid: int) -> None:
    # A PID that already exited returns nonzero; the poll in stop() is what decides success.
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            check=False,
            creationflags=NO_WINDOW,
            timeout=STOP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(f"⚠️ taskkill for PID {pid} did not finish within {STOP_TIMEOUT_S:.0f}s; the poll decides")


def stop() -> int:
    try:
        targets = _targets_now()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"NOT CONFIRMED: could not read the process list, nothing was stopped ({exc})")
        return EXIT_NOT_CONFIRMED
    if not targets:
        print(f"ℹ️ no {APP_ID} process is running")
        return 0
    print(f"ℹ️ ending {APP_ID} PIDs {targets}")
    for pid in targets:
        _end_tree(pid)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while True:
        try:
            left = _targets_now()
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"NOT CONFIRMED: could not re-read the process list after stopping ({exc})")
            return EXIT_NOT_CONFIRMED
        if not left:
            print(f"✅ {APP_ID} stopped")
            return 0
        if time.monotonic() >= deadline:
            print(f"❌ still running after {STOP_TIMEOUT_S:.0f} s: PIDs {left}")
            return EXIT_FAILED
        time.sleep(POLL_S)


def _probe_urls() -> list[str]:
    port = app_ports()[0]
    return [f"https://127.0.0.1:{port}/api/version", f"http://127.0.0.1:{port}/api/version"]


def _served_sha(url: str) -> str | None:
    # Loopback and this app's own cert: the tailnet leaf names the PC, not 127.0.0.1.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=2, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return None
    # This app's version body carries git_sha and asset_hash (app/webapp/build.py); anything else is not it.
    if not isinstance(body, dict) or "asset_hash" not in body:
        return None
    sha = body.get("git_sha")
    return sha if isinstance(sha, str) else None


def verify() -> int:
    expected = resolve_git_sha(ROOT)
    urls = _probe_urls()
    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    served: str | None = None
    while True:
        for url in urls:
            served = _served_sha(url)
            if served is not None:
                break
        if served is not None and (served == expected or expected == "unknown"):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_S)
    if served is None:
        print(f"❌ {APP_ID} did not answer /api/version within {VERIFY_TIMEOUT_S:.0f} s on {urls}")
        return EXIT_FAILED
    if expected == "unknown":
        print(f"NOT CONFIRMED: {APP_ID} answers (git_sha {served}) but git cannot name HEAD here")
        return EXIT_NOT_CONFIRMED
    if served != expected:
        print(f"❌ {APP_ID} serves git_sha {served}, HEAD is {expected}")
        return EXIT_FAILED
    print(f"✅ Verified: {APP_ID} serves git_sha {served} (= HEAD)")
    return 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("stop", "verify", "ports"))
    step = parser.parse_args(argv[1:]).step
    if step == "ports":
        print(",".join(str(p) for p in app_ports()))
        return 0
    return stop() if step == "stop" else verify()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
