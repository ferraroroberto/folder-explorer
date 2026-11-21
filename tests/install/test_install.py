"""The installer keeps a PC's settings: it creates what is missing, adds only missing keys, and overwrites nothing.

Every run is against a temp checkout holding copies of the tracked templates, with an explicit empty environment, so
no test reads or writes this PC's own config, ``.env`` or Startup folder. Venv creation and pip are faked where a
test reaches them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app import install
from src import config
from src.install import checklist, config_merge, startup

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = json.loads((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"))


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    for rel in ("config.example.json", ".env.example", "requirements.txt", "requirements-tray.txt",
                "requirements-email.txt", "requirements-outlook.txt"):
        shutil.copy(REPO_ROOT / rel, root / rel)
    return root


def _run(root: Path, *, what_if: bool = False, env: dict[str, str] | None = None) -> list[install.Step]:
    return install.install(root, env or {}, what_if=what_if, with_startup=False, with_deps=False)


def _leaves(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict) and value:
            out.update(_leaves(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


# An existing config as a PC that set it up long ago might hold it: changed values, keys the template lacks, and
# whole blocks missing.
EXISTING = {
    "remote_access": False,
    "port": 9001,
    "roots": ["X:\\invented"],
    "email": {"roots": ["Y:\\mail"], "download_budget_mb": 50, "custom_key": "kept"},
    "snap": {"jpeg_quality": 70},
    "features": "not an object",
    "unknown_block": {"a": 1},
}


def test_a_fresh_checkout_gets_both_files_from_their_templates(checkout: Path) -> None:
    steps = _run(checkout)
    assert [(s.name, s.status) for s in steps] == [("config.json", "created"), (".env", "created")]
    assert (checkout / "config.json").read_text(encoding="utf-8") == (checkout / "config.example.json").read_text(
        encoding="utf-8")
    assert (checkout / ".env").read_text(encoding="utf-8") == (checkout / ".env.example").read_text(encoding="utf-8")


def test_an_existing_config_keeps_every_value_gains_the_missing_keys_and_is_backed_up(checkout: Path) -> None:
    target = checkout / "config.json"
    target.write_text(json.dumps(EXISTING, indent=4), encoding="utf-8")
    original = target.read_bytes()

    steps = _run(checkout)

    after = json.loads(target.read_text(encoding="utf-8"))
    for name, value in _leaves(EXISTING).items():
        assert _leaves(after)[name] == value, name
    assert config_merge.missing_keys(after, TEMPLATE) == []
    added = steps[0].detail
    assert "schedule" in added and "email.exclude" in added and "snap.hotkey" in added
    assert "features" not in added.split("; ")[0].split(", ")  # a non-object value is kept, never entered
    backups = list(checkout.glob("config.json.*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert steps[0].status == config_merge.ADDED and steps[0].changed


def test_a_second_run_reports_unchanged_and_writes_nothing(checkout: Path) -> None:
    (checkout / "config.json").write_text(json.dumps(EXISTING), encoding="utf-8")
    _run(checkout)
    snapshot = {p.name: p.read_bytes() for p in checkout.iterdir()}

    steps = _run(checkout)

    assert [(s.name, s.status) for s in steps] == [("config.json", "unchanged"), (".env", "kept")]
    assert not any(s.changed for s in steps)
    assert {p.name: p.read_bytes() for p in checkout.iterdir()} == snapshot


def test_what_if_lists_the_changes_and_makes_none(checkout: Path) -> None:
    fresh = _run(checkout, what_if=True)
    assert [(s.status, s.changed) for s in fresh] == [("would be created", True), ("would be created", True)]
    assert not (checkout / "config.json").exists() and not (checkout / ".env").exists()

    target = checkout / "config.json"
    target.write_text(json.dumps(EXISTING), encoding="utf-8")
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    steps = _run(checkout, what_if=True)
    assert steps[0].status == "would be added keys" and "schedule" in steps[0].detail
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert list(checkout.glob("*.bak")) == []


@pytest.mark.parametrize("text", ["{ not json", "[1, 2]", ""])
def test_a_config_that_does_not_parse_is_left_alone_and_reported(checkout: Path, text: str) -> None:
    target = checkout / "config.json"
    target.write_text(text, encoding="utf-8")
    steps = _run(checkout)
    assert steps[0].status == config_merge.UNREADABLE and steps[0].failed
    assert target.read_text(encoding="utf-8") == text
    assert list(checkout.glob("*.bak")) == []


def test_an_existing_env_is_never_read_or_changed(checkout: Path) -> None:
    env_file = checkout / ".env"
    env_file.write_text("OTHER=1\n", encoding="utf-8")  # lacks every template key
    steps = _run(checkout)
    assert steps[1].status == config_merge.KEPT
    assert env_file.read_text(encoding="utf-8") == "OTHER=1\n"


def test_the_config_override_is_the_file_the_installer_keeps(checkout: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere" / "settings.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(json.dumps({"port": 9002}), encoding="utf-8")
    _run(checkout, env={config_merge.CONFIG_ENV: str(elsewhere)})
    assert not (checkout / "config.json").exists()
    assert json.loads(elsewhere.read_text(encoding="utf-8"))["port"] == 9002
    assert config_merge.missing_keys(json.loads(elsewhere.read_text(encoding="utf-8")), TEMPLATE) == []


def test_added_keys_never_change_what_the_app_runs_with() -> None:
    """The template is the defaults written out, so filling a gap from it leaves the effective settings as they were."""
    assert config._clean(config_merge.merged(EXISTING, TEMPLATE)) == config._clean(EXISTING)
    assert config._clean(TEMPLATE) == config._clean({})


def test_the_feature_blocks_choose_the_optional_requirements(checkout: Path) -> None:
    every = install.requirement_files(checkout, {"features": {"email": True, "archive": True}})
    assert every == ["requirements.txt", "requirements-tray.txt", "requirements-email.txt", "requirements-outlook.txt"]
    assert install.requirement_files(checkout, {"features": {"email": False, "archive": False}}) == [
        "requirements.txt", "requirements-tray.txt"]
    assert install.requirement_files(checkout, None) == every


@pytest.mark.parametrize(("what_if", "stdout", "status", "changed"), [
    (False, "Requirement already satisfied: fastapi\n", "unchanged", False),
    (False, "Successfully installed rapidfuzz-3.9.0\n", "installed", True),
    (True, "Would install rapidfuzz-3.9.0\n", "would install", True),
    (True, "Requirement already satisfied: fastapi\n", "unchanged", False),
])
def test_pip_reports_unchanged_when_every_requirement_is_met(checkout: Path, monkeypatch: pytest.MonkeyPatch,
                                                             what_if: bool, stdout: str, status: str,
                                                             changed: bool) -> None:
    py = install.venv_python(checkout)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)
    step = install.install_requirements(checkout, ["requirements.txt"], what_if=what_if)
    assert (step.status, step.changed, step.failed) == (status, changed, False)
    assert ("--dry-run" in seen[0]) is what_if


def test_what_if_never_creates_the_venv(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: pytest.fail("ran a process under -WhatIf"))
    steps = install.install(checkout, {}, what_if=True, with_startup=False)
    assert steps[0].status == "would create" and not (checkout / ".venv").exists()
    assert steps[-1].status == "would install"


# ---------------------------------------------------------------- start at sign-in


def _startup(tmp_path: Path) -> tuple[dict[str, str], Path]:
    env = {"APPDATA": str(tmp_path / "appdata")}
    folder = startup.startup_dir(env)
    assert folder is not None
    folder.mkdir(parents=True)
    return env, folder


def test_an_existing_start_entry_is_kept_whoever_made_it(checkout: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    env, folder = _startup(tmp_path)
    foreign = folder / "my own tray.lnk"
    foreign.write_bytes(b"\x4c\x00" + str(checkout / "tray.bat").encode("utf-16-le"))
    monkeypatch.setattr(startup, "create_shortcut", lambda *_a: pytest.fail("created a second entry"))
    step = install.startup_step(checkout, env, what_if=False)
    assert (step.status, step.detail, step.changed) == ("kept", "my own tray.lnk", False)


def test_a_missing_start_entry_is_created_only_when_asked(checkout: Path, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    env, folder = _startup(tmp_path)
    assert install.startup_step(checkout, env, what_if=True).status == "would create"
    assert list(folder.iterdir()) == []

    def fake_create(where: Path, root: Path) -> Path:
        link = where / startup.SHORTCUT_NAME
        link.write_text(f"{root / 'tray.bat'}", encoding="utf-8")
        return link

    monkeypatch.setattr(startup, "create_shortcut", fake_create)
    assert install.startup_step(checkout, env, what_if=False).status == "created"
    assert install.startup_step(checkout, env, what_if=False).status == "kept"
    assert _run(checkout) and [p.name for p in folder.iterdir()] == [startup.SHORTCUT_NAME]


def test_uninstall_removes_only_its_own_shortcut_to_this_checkout(checkout: Path, tmp_path: Path) -> None:
    env, folder = _startup(tmp_path)
    foreign = folder / "my own tray.lnk"
    foreign.write_text(str(checkout / "tray.bat"), encoding="utf-8")
    other_checkout = folder / startup.SHORTCUT_NAME
    other_checkout.write_text("Z:\\another-copy\\tray.bat", encoding="utf-8")

    assert install.uninstall(checkout, env, what_if=False)[0].status == "unchanged"
    assert foreign.exists() and other_checkout.exists()

    other_checkout.write_text(str(checkout / "tray.bat"), encoding="utf-8")
    assert install.uninstall(checkout, env, what_if=True)[0].status == "would remove"
    assert other_checkout.exists()
    assert install.uninstall(checkout, env, what_if=False)[0].status == "removed"
    assert not other_checkout.exists() and foreign.exists()


# ---------------------------------------------------------------- checklist


def test_the_checklist_names_what_still_needs_the_owner(checkout: Path, tmp_path: Path) -> None:
    env, _folder = _startup(tmp_path)
    env["APP_PASSWORD_FILE"] = str(tmp_path / "no-password.json")
    (checkout / ".env").write_text("FOLDER_EXPLORER_TOKEN=\n", encoding="utf-8")
    checks = checklist.build(checkout, env, {"remote_access": True}, venv_ok=True, deps_state=checklist.OK,
                             deps_detail="unchanged", probe=False)
    states = {c.name: c.state for c in checks}
    assert states == {".venv": "ok", "requirements": "ok", "config": "ok", "sign-in password": "missing",
                      "sign-in token": "missing", "tailnet certificate": "missing", "start at sign-in": "missing"}


def test_the_checklist_never_prints_the_token_and_skips_sign_in_when_remote_access_is_off(checkout: Path) -> None:
    (checkout / ".env").write_text("FOLDER_EXPLORER_TOKEN=s3cret-value\n", encoding="utf-8")
    on = checklist.build(checkout, {}, {"remote_access": True}, venv_ok=True, deps_state="ok", deps_detail="",
                         probe=False)
    assert "s3cret-value" not in repr(on)
    assert {c.name: c.state for c in on}["sign-in token"] == "ok"
    assert {c.name: c.state for c in on}["start at sign-in"] == "unknown"  # no APPDATA: not confirmed
    off = checklist.build(checkout, {}, {"remote_access": False}, venv_ok=True, deps_state="ok", deps_detail="",
                          probe=False)
    assert not any(c.name.startswith("sign-in") for c in off)


def test_the_report_says_unchanged_or_counts_the_changes(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="install")
    assert install.report([install.Step("config.json", "unchanged")], [], what_if=False) == 0
    assert "Result: unchanged." in caplog.text
    assert install.report([install.Step("config.json", "unreadable", failed=True)], [], what_if=False) == 1
    install.report([install.Step(".venv", "would create", changed=True)], [], what_if=True)
    assert "-WhatIf: 1 change(s) would be made; nothing was changed." in caplog.text
