"""The HTTP API: browse, scan, settings, the sign-in gate and the bind rule."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.bind import STATE_NO_CERT, STATE_OFF, STATE_ON, resolve_bind
from app.webapp.server import app
from tests.conftest import write_config
from tests.fixture_tree import FILES, TOTAL_BYTES, build_tree

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)


@pytest.fixture()
def scanned(isolated: Path) -> str:
    write_config(isolated, roots=[str(build_tree(isolated / "Invented root"))])
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        return c.get("/api/roots").json()["roots"][0]["key"]


def test_browse_endpoints_answer_from_the_index(scanned: str) -> None:
    with TestClient(app, client=LOCAL) as c:
        tree = c.get("/api/tree", params={"root": scanned}).json()
        assert tree["folder"]["size"] == TOTAL_BYTES and tree["path"][0]["name"] == "Invented root"
        assert c.get("/api/top", params={"root": scanned, "n": 1}).json()["files"][0]["name"] == "river-01.mp4"
        assert c.get("/api/percentile", params={"root": scanned, "p": 10}).json()["count"] == 2
        assert c.get("/api/types", params={"root": scanned}).json()["count"] > 5
        summary = c.get("/api/summary", params={"root": scanned, "type": "-"}).json()
        assert summary["totals"]["files"] == 1
        nested = c.get("/api/folder", params={"root": scanned, "mode": "nested"}).json()
        assert nested["total_items"] == len(FILES)
        assert c.get("/api/tree", params={"root": scanned, "basis": "bogus"}).status_code == 400
        assert c.get("/api/tree", params={"root": scanned, "folder": 9999}).status_code == 404
        assert c.get("/api/tree", params={"root": "unknown"}).status_code == 404


def test_roots_report_the_last_scan(scanned: str) -> None:
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/roots").json()
    root = body["roots"][0]
    assert root["current"]["files"] == len(FILES) and root["running"] is None and root["failed"] is None
    assert body["scanning"] is False


def test_scan_rejects_unknown_roots(scanned: str) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={"roots": ["nope"]}).status_code == 400
        assert c.post("/api/scan", json={"roots": "c"}).status_code == 400


def test_remote_callers_need_a_token(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/roots").status_code == 401
        assert c.get("/api/version").status_code == 200
        assert c.get("/healthz").status_code == 200
        assert c.post("/api/login", json={"password": "x"}).status_code == 503
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    with TestClient(app, client=REMOTE) as c:
        assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
        token = c.post("/api/login", json={"password": "invented-password"}).json()["token"]
        assert token == "invented-token"
        assert c.get("/api/roots", headers={"Authorization": "Bearer " + token}).status_code == 200
        assert c.get("/api/roots", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_repeated_wrong_passwords_are_throttled(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    with TestClient(app, client=("100.64.0.99", 50000)) as c:
        codes = [c.post("/api/login", json={"password": "wrong"}).status_code for _ in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429


def test_loopback_needs_no_token(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/roots").status_code == 200


def test_config_round_trip_and_rejection(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        body = c.post("/api/config", json={"remote_access": False, "schedule": "hourly"}).json()
        assert body["config"]["remote_access"] is False and body["config"]["schedule"] == "hourly"
        assert body["remote"]["would_be"] == STATE_OFF
        assert c.post("/api/config", json={"schedule": "sometimes"}).status_code == 400


def test_shell_and_assets_carry_cache_rules(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        shell = c.get("/")
        assert shell.headers["cache-control"] == "no-cache, must-revalidate"
        assert "/static/main.js?v=" in shell.text
        js = c.get("/static/main.js")
        assert "immutable" in js.headers["cache-control"] and "./api.js?v=" in js.text
        assert c.get("/favicon.ico").status_code == 200


def test_bind_follows_the_remote_access_setting(tmp_path: Path) -> None:
    cfg = {"port": 8453, "remote_access": False}
    assert resolve_bind(cfg, tmp_path).state == STATE_OFF
    assert resolve_bind(cfg, tmp_path).host == "127.0.0.1"
    cfg["remote_access"] = True
    assert resolve_bind(cfg, tmp_path).state == STATE_NO_CERT
    assert resolve_bind(cfg, tmp_path).scheme == "http"
    (tmp_path / "cert.pem").write_text("c")
    (tmp_path / "key.pem").write_text("k")
    on = resolve_bind(cfg, tmp_path)
    assert (on.state, on.host, on.scheme) == (STATE_ON, "0.0.0.0", "https")
