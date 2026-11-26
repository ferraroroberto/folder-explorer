"""A plain ``git pull`` updates any PC: every file a run writes is gitignored (a junctioned ``.venv`` included), no
tracked script needs a signature, and every repo PowerShell script starts under an AllSigned execution policy
without overriding it.

The AllSigned runs use the ``PSExecutionPolicyPreference`` environment variable: it sets the *process*-scope policy
of that one PowerShell process tree and touches no machine or user setting.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src import ps_script
from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = ps_script.POWERSHELL_EXE
# host-identity-exempt: deliberate live-install guard, runs the real shared tray helper only where this PC has it
TRAY_HELPER = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "tray" / "tray_lifecycle.ps1"
GIT_TIMEOUT_S = 60
PS_TIMEOUT_S = 120
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell execution policies are Windows only")

# What a session, a tray run, a restart and the gate write inside the checkout, one file each.
RUNTIME_FILES = (
    "config.json", "config.json.tmp", "config.json.bak-20260101-120000", ".env", "mailboxes.json",
    "config/app_password.json", "webapp/certificates/cert.pem", "webapp/certificates/key.pem", "tray.log",
    "tmp/scratch.txt", ".proof/x.json", ".ruff_cache/x", ".pytest_cache/x", ".mypy_cache/x", "app/__pycache__/x.pyc",
    "src/x.pyc", "test-results/x",
)
# The tracked templates a clone carries beside them.
TEMPLATES = ("config.example.json", ".env.example", "mailboxes.sample.json")

_PROBE = """\
param([Parameter(Position = 0)][string] $Action, [string] $Name, [string] $Empty, [switch] $Flag)
Write-Host "action=$Action name=[$Name] empty=[$Empty] flag=$($Flag.IsPresent) root=[$PSScriptRoot]"
exit 7
"""


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # a status read must never leave an index lock behind
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env,
                          timeout=GIT_TIMEOUT_S, check=True, creationflags=NO_WINDOW)
    return done.stdout


def _link_dir(link: Path, target: Path) -> None:
    """A junction on Windows (what a worktree's ``.venv`` is), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


# ---------------------------------------------------------------- clean pull


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_every_runtime_file_and_a_linked_venv_leave_git_status_clean(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q")
    shutil.copy(REPO_ROOT / ".gitignore", repo / ".gitignore")
    for rel in TEMPLATES:
        shutil.copy(REPO_ROOT / rel, repo / rel)
    venv = tmp_path / "real-venv"
    (venv / "Scripts").mkdir(parents=True)
    _link_dir(repo / ".venv", venv)
    for rel in RUNTIME_FILES:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x", encoding="utf-8")
    # Only the tracked files of a clone show (nothing is committed here); every runtime file is ignored.
    listed = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert sorted(listed) == sorted(["?? .gitignore", *(f"?? {rel}" for rel in TEMPLATES)])


def test_the_gitignore_names_the_venv_without_a_trailing_slash() -> None:
    """``.venv/`` matches a folder only; a venv linked in as a symlink is a file to git."""
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".venv" in lines and ".venv/" not in lines


# ---------------------------------------------------------------- AllSigned


def _launch(script: Path, args: list[str], policy: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PSExecutionPolicyPreference": policy, "PYTHONUTF8": "1", "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run([sys.executable, "-m", "src.ps_script", str(script), *args], check=False, capture_output=True,
                          text=True,
                          encoding="utf-8", errors="replace", timeout=PS_TIMEOUT_S, cwd=REPO_ROOT, env=env,
                          creationflags=NO_WINDOW)


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    path = tmp_path / "dir with space" / "probe.ps1"
    path.parent.mkdir()
    path.write_text(_PROBE, encoding="utf-8")
    return path


@pytest.mark.parametrize("policy", ["bypass", "unrestricted", "remotesigned"])
def test_a_policy_that_runs_local_files_keeps_the_plain_file_launch(policy: str) -> None:
    argv, extra = ps_script.script_command("x.ps1", ["launch", "-Restart"], policy=policy)
    assert argv == [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", "x.ps1", "launch", "-Restart"] and extra == {}


@pytest.mark.parametrize("policy", ["allsigned", "restricted", "undefined", None])
def test_any_other_policy_or_an_unread_one_loads_the_script_as_text(policy: object) -> None:
    argv, extra = ps_script.script_command("x.ps1", ["launch", "", "a b"], policy=policy)  # type: ignore[arg-type]
    assert argv[3] == "-Command" and "-File" not in argv and "-ExecutionPolicy" not in argv
    assert extra[ps_script.ENV_SCRIPT] == "x.ps1"
    assert extra[ps_script.ENV_ARGV].split(chr(31))[1:] == ["launch", "", "a b"]


def test_a_policy_probe_that_cannot_run_is_not_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise OSError("no powershell")

    monkeypatch.setattr(ps_script.subprocess, "run", boom)
    assert ps_script.effective_policy() is None


@windows_only
def test_under_allsigned_a_file_launch_is_refused_and_the_launcher_runs_it(probe: Path) -> None:
    refused = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "AllSigned", "-File",
                              str(probe)], check=False, capture_output=True, text=True, timeout=PS_TIMEOUT_S,
                             creationflags=NO_WINDOW)
    assert refused.returncode != 0 and "not digitally signed" in refused.stderr
    done = _launch(probe, ["launch", "-Name", "two words", "-Empty", "", "-Flag"], "AllSigned")
    assert done.returncode == 7, done.stderr
    assert f"action=launch name=[two words] empty=[] flag=True root=[{probe.parent}]" in done.stdout
    assert "execution policy allsigned" in done.stderr


@windows_only
def test_the_gate_starts_and_loads_its_sibling_under_allsigned(tmp_path: Path) -> None:
    """The gate's one nested script, loaded the way verify-before-ship.ps1 writes it."""
    gate = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    loads = re.findall(r"^\. \(\[scriptblock\]::Create.*$", gate, re.MULTILINE)
    assert len(loads) == 1, loads
    harness = tmp_path / "harness.ps1"
    line = loads[0].replace("; Enter-BoxLockGate", "").replace("$PSScriptRoot", f"'{REPO_ROOT / 'scripts'}'")
    harness.write_text(f"{line}\nGet-Command Enter-BoxLockGate | Out-Null\nWrite-Host loaded\n", encoding="utf-8")
    done = _launch(harness, [], "AllSigned")
    assert done.returncode == 0 and "loaded" in done.stdout, done.stderr


@windows_only
@pytest.mark.skipif(not TRAY_HELPER.is_file(), reason="this PC has no shared tray helper")
def test_tray_bat_starts_the_shared_helper_under_allsigned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tray.bat's launcher line and the arguments it sets, doing the read-only ``detect`` instead of ``launch``."""
    bat = (REPO_ROOT / "tray.bat").read_text(encoding="ascii")
    assert '-m src.ps_script "%TRAY_PS%" launch' in bat
    for name in re.findall(r'^set "PSL_ARG_(\w+)=', bat, re.MULTILINE):
        monkeypatch.setenv(f"PSL_ARG_{name}", str(tmp_path))
    monkeypatch.setenv("PSL_ARG_TrayMatch", r"-m\s+probe")
    done = _launch(TRAY_HELPER, ["detect"], "AllSigned")
    assert done.returncode == 0, done.stderr
    assert "ScriptBlock" in done.stderr and "cannot be loaded" not in done.stderr
    assert done.stdout.strip() == ""


def test_no_script_overrides_the_policy_or_carries_a_signature() -> None:
    """A signature appended to a tracked script turns every ``git pull`` on an AllSigned PC into a conflict."""
    scripts = [*REPO_ROOT.glob("*.bat"), *(REPO_ROOT / "scripts").glob("*.bat"), *(REPO_ROOT / "scripts").glob("*.ps1"),
               REPO_ROOT / "src" / "ps_script.py"]
    texts = {p.name: p.read_text(encoding="utf-8") for p in scripts}
    assert [n for n, t in texts.items() if re.search(r"-ExecutionPolicy\s+Bypass", t, re.IGNORECASE)] == []
    assert [n for n, t in texts.items() if "SIG # Begin signature block" in t] == []


def test_no_launch_starts_a_repo_or_helper_script_from_its_file() -> None:
    bat = (REPO_ROOT / "tray.bat").read_text(encoding="ascii")
    gate = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    assert not re.search(r"-File\s", re.sub(r"(?m)^REM.*$", "", bat))
    assert not re.search(r"^\s*\. \(Join-Path", gate, re.MULTILINE)
