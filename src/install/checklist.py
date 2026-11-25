"""The installer's closing checklist: what works on this PC and what still needs the owner, each ok, missing or unknown.

Read-only. A check that cannot establish its fact says ``unknown``, never ``ok``. Secrets are never printed: the
token check reads only whether ``FOLDER_EXPLORER_TOKEN`` has a value. Standard library only, like the installer.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.install import startup

OK = "ok"
MISSING = "missing"
UNKNOWN = "unknown"

PASSWORD_ENV = "APP_PASSWORD_FILE"
TOKEN_VAR = "FOLDER_EXPLORER_TOKEN"
DEFAULT_PORT = 8453
_PROBE_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str


def _token_set(env_file: Path) -> bool | None:
    """Whether ``.env`` gives the token a value; None when the file cannot be read."""
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == TOKEN_VAR:
            return bool(value.strip().strip('"').strip("'"))
    return False


def _served_sha(port: int) -> str | None:
    # Loopback and this app's own certificate, which names the tailnet host, not 127.0.0.1.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for scheme in ("https", "http"):
        url = f"{scheme}://127.0.0.1:{port}/api/version"
        try:
            with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S, context=ctx) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            continue
        if isinstance(body, dict) and isinstance(body.get("git_sha"), str):
            return body["git_sha"]
    return None


def remote_checks(root: Path, env: Mapping[str, str], cfg: dict[str, Any]) -> list[Check]:
    """Sign-in and certificate, only when ``remote_access`` is on (off, this PC alone uses the app, unsigned)."""
    if cfg.get("remote_access") is False:
        return []
    password = Path(env.get(PASSWORD_ENV, "").strip() or root / "config" / "app_password.json")
    cert_dir = root / "webapp" / "certificates"
    has_cert = (cert_dir / "cert.pem").is_file() and (cert_dir / "key.pem").is_file()
    token = _token_set(root / ".env")
    return [
        Check("sign-in password", OK if password.is_file() else MISSING,
              "set" if password.is_file() else r"run: .venv\Scripts\python.exe -m src.password set"),
        Check("sign-in token", {True: OK, False: MISSING, None: UNKNOWN}[token],
              {True: "set in .env", False: r"run: .venv\Scripts\python.exe scripts\gen_token.py",
               None: ".env could not be read"}[token]),
        Check("tailnet certificate", OK if has_cert else MISSING,
              "present" if has_cert else r"for phone access: .venv\Scripts\python.exe scripts\gen_tailscale_cert.py"),
    ]


def build(root: Path, env: Mapping[str, str], cfg: dict[str, Any] | None, *, venv_ok: bool,
          deps_state: str, deps_detail: str, probe: bool = True) -> list[Check]:
    """Every row of the checklist, in the order a new PC needs them."""
    checks = [
        Check(".venv", OK if venv_ok else MISSING, "present" if venv_ok else "not created"),
        Check("requirements", deps_state, deps_detail),
        Check("config", OK if cfg is not None else MISSING,
              "readable" if cfg is not None else "fix config.json by hand, then run install again"),
    ]
    checks += remote_checks(root, env, cfg or {})
    folder = startup.startup_dir(env)
    if folder is None:
        checks.append(Check("start at sign-in", UNKNOWN, "APPDATA is not set"))
    else:
        present = startup.find_entries(folder, root)
        checks.append(Check("start at sign-in", OK if present else MISSING,
                            present[0].name if present else "optional: scripts\\install.bat --startup"))
    if probe:
        raw = (cfg or {}).get("port")
        port = raw if isinstance(raw, int) else DEFAULT_PORT
        sha = _served_sha(port)
        # Named by port and build: another checkout on the same port would answer too, and its build tells them apart.
        checks.append(Check("app running", OK if sha else MISSING,
                            f"port {port} answers with build {sha}" if sha
                            else f"nothing answers on port {port}: start tray.bat (or scripts/start.bat for a console)"))
    return checks
