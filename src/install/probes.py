"""``python -m src.install.probes <name>``: the checklist probes ``setup.bat`` runs for what is specific to this app.

Each exits 0 when the fact holds, 1 when it does not and 2 when it could not be established, which the installer
reports as ``ok``, ``missing`` and ``unknown`` (project-scaffolding docs/installer.md). Read-only; the token is never
printed, only whether ``FOLDER_EXPLORER_TOKEN`` has a value.
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

from src import config, password
from src.secrets_env import TOKEN_VAR, read_env_file

_PROBE_TIMEOUT_S = 2.0
OK, MISSING, UNKNOWN = 0, 1, 2


def token_set(env_file: Path) -> bool | None:
    """Whether ``.env`` gives the token a value; None when the file cannot be read."""
    try:
        env_file.read_bytes()  # read_env_file reads an unreadable file as an empty one
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return bool(read_env_file(env_file).get(TOKEN_VAR))


def served_sha(port: int) -> str | None:
    """The ``git_sha`` the app on loopback ``port`` reports, over https or http; None when nothing answers."""
    # This app's own certificate names the tailnet host, not 127.0.0.1.
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


def _running(_root: Path) -> int:
    return OK if served_sha(config.load()["port"]) else MISSING


def _token(root: Path) -> int:
    return {True: OK, False: MISSING, None: UNKNOWN}[token_set(root / ".env")]


def _password(_root: Path) -> int:
    return OK if password.stored_hash() else MISSING


PROBES = {"running": _running, "token": _token, "password": _password}


def main(argv: list[str] | None = None, root: Path = config.PROJECT_ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in PROBES:
        print(f"usage: python -m src.install.probes {{{'|'.join(PROBES)}}}", file=sys.stderr)
        return UNKNOWN
    return PROBES[args[0]](root)


if __name__ == "__main__":
    sys.exit(main())
