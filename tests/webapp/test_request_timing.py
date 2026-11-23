"""The request-timing middleware is mounted: every /api answer carries Server-Timing and the stats endpoint answers."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.webapp.server import app

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)


def test_an_api_answer_says_where_its_time_went(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/version")
    assert res.status_code == 200
    assert "app;dur=" in res.headers["server-timing"]


def test_the_stats_endpoint_reports_what_the_middleware_saw(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        c.get("/api/version")
        res = c.get("/api/request-stats", params={"window_s": 60})
    assert res.status_code == 200
    body = res.json()
    assert body["window_s"] == 60
    assert body["inflight"] >= 1 and body["max_inflight"] >= 1


def test_the_stats_endpoint_is_behind_the_sign_in_gate(isolated: Path) -> None:
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/request-stats").status_code == 401
