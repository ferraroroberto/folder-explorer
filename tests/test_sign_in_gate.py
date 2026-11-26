"""The sign-in gate's request handling for callers on this PC, which need no token."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.webapp.server import app

LOCAL = ("127.0.0.1", 50000)
_ELSEWHERE = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Origin": "https://example.invalid"}


def test_sign_in_gate_request_handling() -> None:
    """Only a GET may arrive from a page on another site; the app's own page and non-browser clients are unaffected."""
    with TestClient(app, client=LOCAL) as c:
        removed = c.delete("/api/searches/987654", headers=_ELSEWHERE)
        posted = c.post("/api/searches", headers={**_ELSEWHERE, "Content-Type": "text/plain"}, content=b'{"a":"=b"}')
        visited = c.get("/api/version", headers=_ELSEWHERE)
        own = c.delete("/api/searches/987654", headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"})
        script = c.delete("/api/searches/987654")
    assert removed.status_code == 403 and removed.json()["reason"] == "other_site"
    assert posted.status_code == 403
    assert visited.status_code == 200
    assert own.status_code == 404 and script.status_code == 404
