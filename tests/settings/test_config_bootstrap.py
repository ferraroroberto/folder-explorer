"""First run: config.json created from config.example.json, never over a file that exists, through the API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import config

LOCAL = ("127.0.0.1", 50000)


def test_the_example_is_exactly_the_defaults() -> None:
    assert config._clean(json.loads(config.example_path().read_text(encoding="utf-8"))) == config.DEFAULTS


def test_create_writes_the_example_once_and_loads_as_defaults(isolated: Path) -> None:
    assert config.create_from_example() is True
    assert (isolated / "config.json").read_text(encoding="utf-8") == config.example_path().read_text(encoding="utf-8")
    assert config.load() == config.DEFAULTS


def test_create_never_overwrites_a_file_that_exists(isolated: Path) -> None:
    mine = '{"port": 9000, "roots": ["D:/Invented"]}\n'
    (isolated / "config.json").write_text(mine, encoding="utf-8")
    assert config.create_from_example() is False
    assert (isolated / "config.json").read_text(encoding="utf-8") == mine


def test_create_also_leaves_an_unreadable_file_alone(isolated: Path) -> None:
    (isolated / "config.json").write_text("{not json", encoding="utf-8")
    assert config.create_from_example() is False
    assert (isolated / "config.json").read_text(encoding="utf-8") == "{not json"


def test_create_makes_the_folder_of_an_overridden_path(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = isolated / "nested" / "config.json"
    monkeypatch.setenv(config.CONFIG_ENV, str(target))
    assert config.create_from_example() is True and target.is_file()


def test_the_api_reports_a_missing_file_then_creates_it_in_one_post(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        first = c.get("/api/config").json()
        assert first["config_file"] == {"exists": False} and first["config"] == config.DEFAULTS
        made = c.post("/api/config/create")
        assert made.status_code == 200
        assert made.json()["created"] is True and made.json()["config_file"] == {"exists": True}
        assert (isolated / "config.json").is_file()
        assert c.get("/api/config").json()["config_file"] == {"exists": True}


def test_a_second_create_changes_nothing(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/config", json={"schedule": "hourly"}).status_code == 200
        before = (isolated / "config.json").read_text(encoding="utf-8")
        again = c.post("/api/config/create").json()
        assert again["created"] is False and again["config"]["schedule"] == "hourly"
        assert (isolated / "config.json").read_text(encoding="utf-8") == before


def test_a_missing_example_is_a_plain_500_sentence(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "example_path", lambda: isolated / "absent.json")
    with TestClient(app, client=LOCAL) as c:
        res = c.post("/api/config/create")
    assert res.status_code == 500
    assert "could not be created" in res.json()["detail"] and not (isolated / "config.json").exists()
