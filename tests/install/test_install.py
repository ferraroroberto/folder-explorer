"""``setup.bat`` (the vendored ``src/installer.py``) installs this app and keeps a PC's settings.

Each run is against a temp checkout holding copies of this repo's tracked templates and its real ``[install]`` table,
with a fake runner in place of ``subprocess.run``: no venv is created, nothing is installed, and no test reads or
writes this PC's own config, ``.env`` or Startup folder.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src import config, installer
from src.install import probes, startup

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = json.loads((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"))
FIXED_NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)
TRACKED = (".fleet.toml", "config.example.json", ".env.example", "mailboxes.sample.json", "requirements.txt",
           "requirements-tray.txt", "requirements-email.txt", "requirements-outlook.txt")

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


class FakeRunner:
    """Stands in for ``subprocess.run``: records commands, plays the venv's creation, answers every probe ``ok``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        if command[1:3] == ["-m", "venv"]:
            interpreter = installer.venv_python(self.root)
            interpreter.parent.mkdir(parents=True, exist_ok=True)
            interpreter.write_text("", encoding="utf-8")
        # No log-on entry exists on a fresh PC, so the autostart probe says "missing" (exit 1).
        code = 1 if command[-3:-1] == ["-m", "src.install.startup"] and command[-1] == "status" else 0
        return subprocess.CompletedProcess(command, code)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    for rel in TRACKED:
        shutil.copy(REPO_ROOT / rel, root / rel)
    return root


def _run(root: Path, runner: FakeRunner | None = None, *, what_if: bool = False) -> installer.Report:
    return installer.run(root, what_if=what_if, runner=runner or FakeRunner(root), now=lambda: FIXED_NOW)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _statuses(report: installer.Report) -> dict[str, str]:
    return {item: status for status, item, _ in report.lines}


def test_a_fresh_checkout_gets_the_venv_the_packages_and_both_files_from_their_templates(checkout: Path) -> None:
    runner = FakeRunner(checkout)
    report = _run(checkout, runner)
    assert not report.failed
    assert _statuses(report) == {".venv": "created", "requirements": "installed", "dependency step": "installed",
                                 "config.json": "created", ".env": "created", "start at log on": "not registered"}
    assert (checkout / "config.json").read_bytes() == (checkout / "config.example.json").read_bytes()
    assert (checkout / ".env").read_bytes() == (checkout / ".env.example").read_bytes()
    pip = [call for call in runner.calls if "pip" in call]
    assert len(pip) == 2 and "requirements-email.txt" in pip[1] and "requirements-outlook.txt" in pip[1]


def test_the_mailbox_registry_is_never_created_from_its_invented_sample(checkout: Path) -> None:
    _run(checkout)
    assert not (checkout / "mailboxes.json").exists()


def test_a_second_run_reports_unchanged_and_writes_nothing(checkout: Path) -> None:
    _run(checkout)
    before = _snapshot(checkout)
    runner = FakeRunner(checkout)
    report = _run(checkout, runner)
    assert report.changes == []
    assert "-> unchanged: nothing to do" in installer.render(report)
    assert not [call for call in runner.calls if "pip" in call]
    assert _snapshot(checkout) == before


def test_what_if_lists_the_changes_and_makes_none(checkout: Path) -> None:
    before = _snapshot(checkout)
    runner = FakeRunner(checkout)
    report = _run(checkout, runner, what_if=True)
    assert list(_statuses(report).values()).count("would create") == 3
    assert runner.calls == []
    assert _snapshot(checkout) == before


def test_an_existing_config_keeps_every_value_gains_the_missing_keys_and_is_backed_up(checkout: Path) -> None:
    target = checkout / "config.json"
    original = json.dumps(EXISTING, indent=2) + "\n"
    target.write_text(original, encoding="utf-8")
    report = _run(checkout)
    assert _statuses(report)["config.json"] == "added"
    result = json.loads(target.read_text(encoding="utf-8"))
    for key, value in EXISTING.items():
        if not isinstance(value, dict):
            assert result[key] == value
    assert result["email"]["custom_key"] == "kept" and result["email"]["download_budget_mb"] == 50
    assert result["unknown_block"] == {"a": 1}
    assert set(TEMPLATE) <= set(result) and set(TEMPLATE["snap"]) <= set(result["snap"])
    assert (checkout / "config.json.bak-20260102-030405").read_text(encoding="utf-8") == original
    detail = next(detail for _status, item, detail in report.lines if item == "config.json")
    assert "9001" not in detail and "invented" not in detail  # key names only, never a value


def test_added_keys_never_change_what_the_app_runs_with() -> None:
    """The template is the defaults written out, so filling a gap from it leaves the effective settings as they were."""
    merged = json.loads(json.dumps(EXISTING))
    installer._add_missing(merged, TEMPLATE)
    assert config._clean(merged) == config._clean(EXISTING)
    assert config._clean(TEMPLATE) == config._clean({})


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_a_config_that_does_not_parse_is_left_alone_and_reported(checkout: Path, text: str) -> None:
    (checkout / "config.json").write_text(text, encoding="utf-8")
    report = _run(checkout)
    assert report.failed and _statuses(report)["config.json"] == "left alone"
    assert (checkout / "config.json").read_text(encoding="utf-8") == text
    assert not list(checkout.glob("config.json.bak-*"))


def test_an_existing_env_is_never_read_or_changed(checkout: Path) -> None:
    (checkout / ".env").write_text("FOLDER_EXPLORER_TOKEN=s3cret-value\n", encoding="utf-8")
    report = _run(checkout)
    assert _statuses(report)[".env"] == "unchanged"
    assert (checkout / ".env").read_text(encoding="utf-8") == "FOLDER_EXPLORER_TOKEN=s3cret-value\n"
    assert "s3cret-value" not in installer.render(report)


def test_the_dependency_step_reruns_when_a_requirements_file_changes(checkout: Path) -> None:
    _run(checkout)
    (checkout / "requirements-email.txt").write_text("extract-msg>=0.49\n", encoding="utf-8")
    runner = FakeRunner(checkout)
    assert _statuses(_run(checkout, runner))["dependency step"] == "installed"
    assert len([call for call in runner.calls if "pip" in call]) == 1


def test_the_checklist_names_the_sign_in_and_certificate_items_with_their_commands(checkout: Path) -> None:
    report = _run(checkout)
    names = [item for _, item, _ in report.optional]
    for wanted in ("Sign-in password", "Sign-in token", "Tailnet certificate", "App answering on its port", "Start at log on"):
        assert wanted in names
    cert = next(detail for _state, item, detail in report.optional if item == "Tailnet certificate")
    assert "gen_tailscale_cert.py" in cert


# ---------------------------------------------------------------- the entries .fleet.toml declares


def test_the_token_probe_never_prints_the_token(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert probes.main(["token"], checkout) == probes.MISSING
    (checkout / ".env").write_text("FOLDER_EXPLORER_TOKEN=\n", encoding="utf-8")
    assert probes.main(["token"], checkout) == probes.MISSING
    (checkout / ".env").write_text("FOLDER_EXPLORER_TOKEN=s3cret-value\n", encoding="utf-8")
    assert probes.main(["token"], checkout) == probes.OK
    assert "s3cret-value" not in capsys.readouterr().out


def test_a_probe_that_cannot_read_its_file_is_unknown_and_a_bad_name_is_refused(checkout: Path) -> None:
    (checkout / ".env").mkdir()  # a folder where the file should be: unreadable, not absent
    assert probes.token_set(checkout / ".env") is None
    assert probes.main(["token"], checkout) == probes.UNKNOWN
    assert probes.main(["nonsense"], checkout) == probes.UNKNOWN


def test_the_running_probe_reads_the_port_from_the_config(checkout: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(probes, "served_sha", lambda port: seen.append(port) or None)
    assert probes.main(["running"], checkout) == probes.MISSING
    (checkout / "config.json").write_text('{"port": 9123}', encoding="utf-8")
    probes.main(["running"], checkout)
    assert seen == [probes.DEFAULT_PORT, 9123]


def _startup(tmp_path: Path) -> tuple[dict[str, str], Path]:
    env = {"APPDATA": str(tmp_path / "appdata")}
    folder = startup.startup_dir(env)
    assert folder is not None
    folder.mkdir(parents=True)
    return env, folder


def test_an_existing_start_entry_counts_whoever_made_it_and_no_second_one_is_created(
        checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, folder = _startup(tmp_path)
    assert startup.main(["status"], checkout, env) == 1
    (folder / "my own tray.lnk").write_bytes(b"\x4c\x00" + str(checkout / "tray.bat").encode("utf-16-le"))
    monkeypatch.setattr(startup, "create_shortcut", lambda *_a: pytest.fail("created a second entry"))
    assert startup.main(["status"], checkout, env) == 0
    assert startup.main(["enable"], checkout, env) == 0


def test_enable_creates_the_shortcut_once_and_disable_removes_only_its_own(
        checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, folder = _startup(tmp_path)

    def fake_create(where: Path, root: Path) -> Path:
        link = where / startup.SHORTCUT_NAME
        link.write_text(str(root / "tray.bat"), encoding="utf-8")
        return link

    monkeypatch.setattr(startup, "create_shortcut", fake_create)
    assert startup.main(["enable"], checkout, env) == 0
    assert [p.name for p in folder.iterdir()] == [startup.SHORTCUT_NAME]
    foreign = folder / "my own tray.lnk"
    foreign.write_text(str(checkout / "tray.bat"), encoding="utf-8")
    assert startup.main(["disable"], checkout, env) == 0
    assert not (folder / startup.SHORTCUT_NAME).exists() and foreign.exists()
    other = folder / startup.SHORTCUT_NAME
    other.write_text("Z:\\another-copy\\tray.bat", encoding="utf-8")
    assert startup.main(["disable"], checkout, env) == 0
    assert other.exists()  # a shortcut pointing at another checkout is never this one's to remove


def test_a_missing_startup_folder_is_not_confirmed(checkout: Path) -> None:
    assert startup.main(["status"], checkout, {}) == 2
    assert startup.main(["bogus"], checkout, {}) == 2
