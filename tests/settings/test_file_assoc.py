"""The Markdown "Open with" association: per-user keys only, removed exactly, its state read back; over a fake registry.

The autouse ``no_registry`` fixture keeps every test off this PC's real
registry; these pass a :class:`FakeRegistry` instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import file_assoc

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)
ROOT = Path(__file__).resolve().parents[2]
CMD = '"X:\\Invented\\pythonw.exe" -c "..." "%1"'


class FakeRegistry:
    """``HKCU\\Software\\Classes`` as a dict of key -> {value name: text}."""

    def __init__(self, keys: Optional[dict[str, dict[str, str]]] = None, broken: bool = False) -> None:
        self.keys = {k.lower(): dict(v) for k, v in (keys or {}).items()}
        self.broken = broken

    def read(self, key: str, name: str) -> Optional[str]:
        if self.broken:
            raise PermissionError("access denied")
        return self.keys.get(key.lower(), {}).get(name)

    def write(self, key: str, name: str, value: str) -> None:
        self.keys.setdefault(key.lower(), {})[name] = value

    def delete_value(self, key: str, name: str) -> None:
        self.keys.get(key.lower(), {}).pop(name, None)

    def delete_tree(self, key: str) -> None:
        prefix = key.lower()
        for k in [k for k in self.keys if k == prefix or k.startswith(prefix + "\\")]:
            del self.keys[k]


# What was there before: another app's ProgId and the extension's own default value, which must survive.
BEFORE = {".md": {"": "Other.Editor"}, ".md\\OpenWithProgids": {"Other.Editor": ""}}


def test_on_writes_the_handler_for_both_extensions() -> None:
    reg = FakeRegistry(BEFORE)
    assert file_assoc.state(reg, CMD) == "off"
    assert file_assoc.set_on(True, reg, CMD) == "on"
    assert reg.keys["folderexplorer.markdown\\shell\\open\\command"] == {"": CMD}
    for ext in (".md", ".markdown"):
        assert reg.keys[ext + "\\openwithprogids"][file_assoc.PROG_ID] == ""
        assert reg.keys["systemfileassociations\\" + ext + "\\shell\\folderexplorer"] == {"": "Open in Folder Explorer"}
        assert reg.keys["systemfileassociations\\" + ext + "\\shell\\folderexplorer\\command"] == {"": CMD}
    assert reg.keys[".md"] == {"": "Other.Editor"}


def test_off_removes_exactly_what_on_added() -> None:
    reg = FakeRegistry(BEFORE)
    file_assoc.set_on(True, reg, CMD)
    assert file_assoc.set_on(False, reg, CMD) == "off"
    left = {k: v for k, v in reg.keys.items() if v}
    assert left == {".md": {"": "Other.Editor"}, ".md\\openwithprogids": {"Other.Editor": ""}}


def test_state_is_partial_for_another_checkout_and_unknown_when_unreadable() -> None:
    reg = FakeRegistry()
    file_assoc.set_on(True, reg, CMD)
    assert file_assoc.state(reg, CMD.replace("Invented", "Elsewhere")) == "partial"
    reg.delete_tree("SystemFileAssociations\\.markdown\\shell\\FolderExplorer")
    assert file_assoc.state(reg, CMD) == "partial"
    assert file_assoc.state(FakeRegistry(broken=True), CMD) == "unknown"


def test_the_command_runs_the_launcher_from_this_checkout(tmp_path: Path) -> None:
    exe = tmp_path / "pythonw.exe"
    exe.write_bytes(b"")
    cmd = file_assoc.command(exe, Path("X:\\Invented checkout"))
    assert cmd.startswith('"%s" -c "' % exe) and cmd.endswith(' "%1"')
    assert "'X:\\\\Invented checkout'" in cmd and "run_module('app.launcher'" in cmd


def test_only_the_current_user_is_ever_touched() -> None:
    source = (ROOT / "src" / "file_assoc.py").read_text(encoding="utf-8")
    assert "HKEY_LOCAL_MACHINE" not in source and "HKEY_CLASSES_ROOT" not in source
    assert source.count("winreg.HKEY_CURRENT_USER") >= 4


def test_an_instance_that_may_not_touch_the_registry_reports_unknown_and_refuses() -> None:
    assert file_assoc.state() == "unknown"
    with pytest.raises(file_assoc.NotAllowed):
        file_assoc.set_on(True)


def test_the_setting_is_offered_to_this_pc_only(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/config").json()["md_assoc"] == {"state": "unknown"}
        refused = c.post("/api/md-assoc", json={"on": True})
        assert refused.status_code == 409
        assert c.post("/api/md-assoc", json={"on": "yes"}).status_code == 400
    calls: list[Any] = []
    monkeypatch.setattr(file_assoc, "set_on", lambda on: calls.append(on) or "on")
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/md-assoc", json={"on": True}).json() == {"state": "on"}
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    auth = {"Authorization": "Bearer invented-token"}
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/config", headers=auth).json()["md_assoc"] == {"state": "pc_only"}
        assert c.post("/api/md-assoc", json={"on": True}, headers=auth).status_code == 403
    assert calls == [True]
