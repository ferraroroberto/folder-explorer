"""Adopt-or-spawn for the webapp process the tray owns.

``start()`` adopts a webapp already answering on the port, or spawns
``python -m app.webapp`` from this venv. The bind (loopback HTTP or tailnet
HTTPS) is read from config at every spawn, so a restart picks up a changed
``remote_access`` setting. ``stop()`` only stops a process this manager
spawned. The child writes its log to ``webapp.log`` in the runtime-data
folder: a ``pythonw`` tray has no console to inherit.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import IO, Optional

import requests

from src import config, store
from src.config import PROJECT_ROOT
from src.no_window import NO_WINDOW
from src.pooled_http import build_session, pooled_request
from app.tray.single_instance import cross_process_lock
from app.webapp.bind import resolve_bind

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT_S = 30.0
_PROBE_TIMEOUT_S = 2.0
_POLL_S = 0.4
_STOP_WAIT_S = 10.0
# The log restarts empty past this size at the next spawn.
_LOG_MAX_BYTES = 5_000_000

# The probe goes to 127.0.0.1, which the tailnet certificate does not name,
# so it checks liveness without verifying the certificate.
_SESSION = build_session(pool_size=2)
_SESSION.verify = False
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass


class WebappManager:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._log: Optional[IO[bytes]] = None

    @property
    def port(self) -> int:
        return int(config.load()["port"])

    @property
    def url(self) -> str:
        return resolve_bind(config.load()).local_url + "/"

    def healthy(self) -> bool:
        """``/healthz`` answers 200 over either scheme."""
        for scheme in ("https", "http"):
            try:
                r = pooled_request("GET", "%s://127.0.0.1:%d/healthz" % (scheme, self.port), timeout=_PROBE_TIMEOUT_S,
                                   session=_SESSION)
                if r.status_code == 200:
                    return True
            except requests.RequestException:
                continue
        return False

    def owns_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Adopt a healthy webapp, or spawn one and wait until it answers. Raises on failure."""
        with cross_process_lock(r"Global\folder-explorer-webapp-start-%d" % self.port):
            if self.healthy():
                logger.info("ℹ️ webapp already answering on port %d, adopted", self.port)
                return
            self._spawn()
            deadline = time.monotonic() + _STARTUP_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._proc is not None and self._proc.poll() is not None:
                    raise RuntimeError("webapp exited during start (code %s); see webapp.log" % self._proc.returncode)
                if self.healthy():
                    logger.info("✅ webapp up at %s", self.url)
                    return
                time.sleep(_POLL_S)
            raise RuntimeError("webapp did not answer within %.0f s; see webapp.log" % _STARTUP_TIMEOUT_S)

    def _spawn(self) -> None:
        log_path = store.data_dir() / "webapp.log"
        if log_path.exists() and log_path.stat().st_size > _LOG_MAX_BYTES:
            log_path.unlink()
        self._log = open(log_path, "ab")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        # The webapp child is a console program; under the pythonw tray it
        # would otherwise flash a console window.
        flags = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.webapp"], cwd=str(PROJECT_ROOT), env=env,
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags,
        )
        logger.info("🚀 webapp spawned, pid %d", self._proc.pid)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=_STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._log is not None:
            self._log.close()
            self._log = None

    def restart(self) -> None:
        self.stop()
        self.start()
