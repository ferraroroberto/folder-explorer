"""The password route's lock-out and guessing alert, on a fake clock and a fake notifier."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp import auth
from app.webapp.server import app
from src import password

LOCAL = ("127.0.0.1", 50000)
PHONE = ("100.64.0.99", 50000)
PASSWORD = "invented-password"
TOKEN = "invented-token"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeNotifier:
    """Records messages; ``sent`` is set when one arrives from the alert thread."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.sent = threading.Event()

    def send_text(self, text: str) -> None:
        self.messages.append(text)
        self.sent.set()


@pytest.fixture()
def clock(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", PASSWORD)
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", TOKEN)
    fake = FakeClock()
    monkeypatch.setattr(auth, "_lockout", auth.make_lockout(fake))
    return fake


@pytest.fixture()
def notifier(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> FakeNotifier:
    fake = FakeNotifier()
    monkeypatch.setattr(auth, "alert_notifier", lambda: fake)
    return fake


def _wrong(c: TestClient) -> int:
    return c.post("/api/login", json={"password": "wrong-guess"}).status_code


def test_wrong_passwords_lock_out_then_the_wait_ends(clock: FakeClock) -> None:
    with TestClient(app, client=PHONE) as c:
        assert [_wrong(c) for _ in range(6)] == [401] * 6
        locked = c.post("/api/login", json={"password": PASSWORD})
        assert locked.status_code == 429
        wait = int(locked.headers["Retry-After"])
        assert wait >= 1
        clock.now += wait
        ok = c.post("/api/login", json={"password": PASSWORD})
        assert ok.status_code == 200 and ok.json() == {"token": TOKEN}
        assert _wrong(c) == 401  # a correct password clears the streak


def test_three_wrong_passwords_send_one_alert_per_streak(notifier: FakeNotifier) -> None:
    with TestClient(app, client=PHONE) as c:
        assert [_wrong(c) for _ in range(2)] == [401, 401]
        assert not notifier.sent.is_set()
        assert _wrong(c) == 401
        assert notifier.sent.wait(5)
        assert [_wrong(c) for _ in range(3)] == [401, 401, 401]
    assert len(notifier.messages) == 1
    text = notifier.messages[0]
    assert "folder-explorer" in text and PHONE[0] in text and "3" in text
    assert PASSWORD not in text and TOKEN not in text and "wrong-guess" not in text


def test_the_pc_itself_is_never_locked_out(notifier: FakeNotifier) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert [_wrong(c) for _ in range(10)] == [401] * 10
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
    assert notifier.messages == []


def test_stored_hash_replaces_the_legacy_password(clock: FakeClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(password.ENV_VAR, str(tmp_path / "app_password.json"))
    password.set_password("4321")
    with TestClient(app, client=PHONE) as c:
        assert c.post("/api/login", json={"password": "4321"}).json() == {"token": TOKEN}
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 401
