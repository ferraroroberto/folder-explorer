"""``python -m app.webapp``: serve the app with the bind the ``remote_access`` setting picks.

Remote access on renews the tailnet certificate first when it is close to
expiry (``scripts/gen_tailscale_cert.py --check``), then binds every
interface over HTTPS. Off binds loopback over plain HTTP.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

import uvicorn

from src import config
from src.config import PROJECT_ROOT
from src.no_window import NO_WINDOW
from app.webapp.bind import resolve_bind
from app.webapp.event_loop import LOOP_FACTORY
from app.webapp.routers.settings import EFFECTIVE_BIND_ENV

logger = logging.getLogger("app.webapp")

# A wedged tailscaled must not hold the start back for long; a healthy
# renewal finishes in a few seconds.
_CERT_CHECK_TIMEOUT_S = 90


def renew_cert_if_due() -> None:
    """Best effort: never blocks the start, logs a failed or warning renewal."""
    script = PROJECT_ROOT / "scripts" / "gen_tailscale_cert.py"
    try:
        result = subprocess.run([sys.executable, str(script), "--check"], cwd=str(PROJECT_ROOT), capture_output=True,
                                text=True, timeout=_CERT_CHECK_TIMEOUT_S, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("⚠️ certificate renewal check failed to run: %s", exc)
        return
    out = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0 or "[WARN]" in out:
        logger.warning("⚠️ certificate renewal check (exit %d): %s", result.returncode, out)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.webapp")
    parser.add_argument("--port", type=int, default=None, help="override the configured port")
    args = parser.parse_args()

    cfg = config.load()
    if args.port:
        cfg["port"] = args.port
    if cfg["remote_access"]:
        renew_cert_if_due()
    bind = resolve_bind(cfg)
    os.environ[EFFECTIVE_BIND_ENV] = bind.state
    if bind.state == "no-cert":
        logger.warning("⚠️ remote access is on but webapp/certificates/ has no certificate: serving loopback only. "
                       "Run scripts/gen_tailscale_cert.py, then restart.")
    logger.info("ℹ️ folder-explorer on %s (remote access: %s)", bind.local_url, bind.state)
    uvicorn.run(
        "app.webapp.server:app", host=bind.host, port=bind.port, loop=LOOP_FACTORY, log_level="warning",
        ssl_certfile=str(bind.cert) if bind.cert else None, ssl_keyfile=str(bind.key) if bind.key else None,
    )


if __name__ == "__main__":
    main()
