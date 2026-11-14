"""The ``snap`` block of config.json: defaults, per-key checks, the round trip through the file, and who may write it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import config
from src.snap import settings
from src.snap.core import QUALITY_CHOICES, ConfigError

LOCAL = ("127.0.0.1", 50000)


def _file(isolated: Path) -> dict:
    return json.loads((isolated / "config.json").read_text(encoding="utf-8"))


def test_no_file_means_the_defaults() -> None:
    cfg = settings.resolve(config.load()["snap"])
    assert (cfg.hotkey, cfg.jpeg_quality, cfg.copy_to_clipboard, cfg.file_prefix, cfg.show_loupe, cfg.hold_to_capture) == ("ctrl+shift+a", 90, True, "snap", True, False)
    assert cfg.folder == Path.home() / "Pictures" / "snaps"


def test_values_are_read_and_a_folder_expands(isolated: Path) -> None:
    (isolated / "config.json").write_text(json.dumps({"snap": {
        "folder": "~/shots", "hotkey": "Ctrl+Alt+S", "jpeg_quality": 75, "copy_to_clipboard": False, "file_prefix": " shot ", "show_loupe": False}}),
        encoding="utf-8")
    cfg = settings.resolve(config.load()["snap"])
    assert cfg.folder == Path("~/shots").expanduser()
    assert (cfg.hotkey, cfg.jpeg_quality, cfg.copy_to_clipboard, cfg.file_prefix, cfg.show_loupe) == ("ctrl+alt+s", 75, False, "shot", False)


@pytest.mark.parametrize("update, needle", [
    ({"jpeg_quality": 0}, "jpeg_quality"),
    ({"jpeg_quality": 101}, "jpeg_quality"),
    ({"jpeg_quality": True}, "jpeg_quality"),
    ({"jpeg_quality": "90"}, "jpeg_quality"),
    ({"copy_to_clipboard": "yes"}, "copy_to_clipboard"),
    ({"show_loupe": 1}, "show_loupe"),
    ({"hold_to_capture": "yes"}, "hold_to_capture"),
    ({"folder": ""}, "folder"),
    ({"hotkey": 5}, "hotkey"),
    ({"hotkey": "a"}, "modifier"),
    ({"file_prefix": 5}, "file_prefix"),
    ({"file_prefix": "a/b"}, "cannot contain"),
    ({"file_prefix": "   "}, "empty"),
    ({"jpg_quality": 80}, "jpg_quality"),
])
def test_a_bad_setting_is_refused_by_name(update: dict, needle: str) -> None:
    with pytest.raises(ConfigError, match=needle):
        settings.validate(update)


def test_a_bad_value_in_the_file_takes_its_default_and_leaves_the_rest(isolated: Path) -> None:
    (isolated / "config.json").write_text(json.dumps({"snap": {"jpeg_quality": 500, "file_prefix": "invoice", "color": "red"}}), encoding="utf-8")
    block = config.load()["snap"]
    assert block["jpeg_quality"] == 90 and block["file_prefix"] == "invoice" and set(block) == set(settings.DEFAULTS)


def test_every_tray_quality_choice_is_a_valid_quality() -> None:
    for quality in QUALITY_CHOICES:
        assert settings.validate({"jpeg_quality": quality}) == {"jpeg_quality": quality}


def test_save_writes_one_key_and_keeps_the_rest_as_written(isolated: Path) -> None:
    (isolated / "config.json").write_text(json.dumps({"port": 9000, "snap": {"folder": "%USERPROFILE%/shots", "jpeg_quality": 60}}), encoding="utf-8")
    saved = config.save_snap({"file_prefix": "invoice"})
    assert saved["snap"]["file_prefix"] == "invoice" and saved["snap"]["jpeg_quality"] == 60
    assert _file(isolated) == {"port": 9000, "snap": {"folder": "%USERPROFILE%/shots", "jpeg_quality": 60, "file_prefix": "invoice"}}
    assert config.load()["snap"] == saved["snap"]


def test_each_setting_round_trips_through_the_file(isolated: Path) -> None:
    out = str(isolated / "out")
    for key, value in (("copy_to_clipboard", False), ("jpeg_quality", 70), ("file_prefix", "report"), ("show_loupe", False), ("hold_to_capture", True), ("folder", out)):
        assert config.save_snap({key: value})["snap"][key] == value
    final = settings.resolve(config.load()["snap"])
    assert (final.copy_to_clipboard, final.jpeg_quality, final.file_prefix, final.show_loupe, final.hold_to_capture, final.folder) == (
        False, 70, "report", False, True, Path(out))


def test_a_bad_save_leaves_the_file_untouched(isolated: Path) -> None:
    (isolated / "config.json").write_text(json.dumps({"snap": {"jpeg_quality": 60}}), encoding="utf-8")
    before = (isolated / "config.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        config.save_snap({"file_prefix": "bad:name"})
    assert (isolated / "config.json").read_text(encoding="utf-8") == before


def test_save_creates_a_missing_file(isolated: Path) -> None:
    config.save_snap({"jpeg_quality": 95})
    assert _file(isolated) == {"snap": {"jpeg_quality": 95}}


def test_the_settings_api_writes_only_hold_to_capture_in_the_snap_block(isolated: Path) -> None:
    (isolated / "config.json").write_text(json.dumps({"snap": {"folder": "~/shots", "jpeg_quality": 60}}), encoding="utf-8")
    with TestClient(app, client=LOCAL) as c:
        for bad in ({"folder": "X:/Invented"}, {"hold_to_capture": "yes"}, {"hold_to_capture": True, "folder": "X:/Invented"}, {}):
            refused = c.post("/api/config", json={"snap": bad})
            assert refused.status_code == 400 and "hold_to_capture" in refused.json()["detail"]
        assert c.get("/api/config").json()["config"]["snap"]["hold_to_capture"] is False
        saved = c.post("/api/config", json={"snap": {"hold_to_capture": True}})
        assert saved.status_code == 200 and saved.json()["config"]["snap"]["hold_to_capture"] is True
    assert _file(isolated) == {"snap": {"folder": "~/shots", "jpeg_quality": 60, "hold_to_capture": True}}
