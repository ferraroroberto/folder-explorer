"""The sign-in secrets: the password and the bearer token it is exchanged for.

Both live in ``.env`` at the repo root (gitignored), written by
``scripts/set_password.py``. A process environment variable of the same name
wins, which is how tests inject their own. Nothing here ever logs a value.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.config import PROJECT_ROOT

PASSWORD_VAR = "FOLDER_EXPLORER_PASSWORD"
TOKEN_VAR = "FOLDER_EXPLORER_TOKEN"


def env_path() -> Path:
    return PROJECT_ROOT / ".env"


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines of a dotenv file; blank lines and ``#`` comments skipped."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def get_secret(name: str) -> str:
    """The value of ``name`` from the environment, else ``.env``, else ``""``."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return read_env_file(env_path()).get(name, "")


def write_secrets(path: Path, values: dict[str, str]) -> None:
    """Set ``values`` in the dotenv file at ``path``, keeping every other line."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    pending = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in pending:
            out.append(key + "=" + pending.pop(key))
        else:
            out.append(line)
    out.extend(k + "=" + v for k, v in pending.items())
    tmp = path.with_name(path.name + ".tmp")
    # runtime-write-exempt: .env at the repo root is the documented secrets file (gitignored)
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)
