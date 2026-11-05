"""App settings in ``config.json`` at the repo root (gitignored), with defaults for every key.

A missing file, or a missing key, takes the default below, so a fresh clone
runs with no config at all. ``config.example.json`` documents every key.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ENV = "FOLDER_EXPLORER_CONFIG"

SCHEDULES = ("off", "nightly", "hourly")
SERVERS = ("hypercorn", "uvicorn")

DEFAULTS: dict[str, Any] = {
    # The one switch for reaching the app from the phone. Off binds loopback
    # only, over plain HTTP, with no certificate and no sign-in.
    "remote_access": True,
    "port": 8453,
    # hypercorn serves HTTP/2 over the certificate; uvicorn is the HTTP/1.1 fallback.
    "server": "hypercorn",
    # null = every local fixed drive plus OneDrive; a list names the roots.
    "roots": None,
    # The in-app scan schedule. Off by default: on this PC the nightly
    # app-launcher job runs the CLI, and the two must never double-run.
    "schedule": "off",
    "schedule_time": "03:00",
    # Email search reads an email index (an SQLite file with the emails and
    # emails_fts tables) read-only. index_path null turns email search off;
    # roots are the email folders "Show all" may read a .msg from, each
    # inside a scan root.
    "email": {"index_path": None, "roots": []},
    # Folder search: how many leading path components a result hides (display
    # only) and whether a folder of per-person subfolders shows as one result.
    "folder_search": {"skip_levels": 0, "prune_people": True},
}

# The most leading components folder search may hide.
MAX_SKIP_LEVELS = 8

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV, "").strip()
    return Path(override) if override else PROJECT_ROOT / "config.json"


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if isinstance(raw.get("remote_access"), bool):
        cfg["remote_access"] = raw["remote_access"]
    port = raw.get("port")
    if isinstance(port, int) and 1 <= port <= 65535:
        cfg["port"] = port
    if raw.get("server") in SERVERS:
        cfg["server"] = raw["server"]
    roots = raw.get("roots")
    if roots is None or (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
        cfg["roots"] = [r.strip() for r in roots if r.strip()] if roots else None
    if raw.get("schedule") in SCHEDULES:
        cfg["schedule"] = raw["schedule"]
    if isinstance(raw.get("schedule_time"), str) and _TIME_RE.match(raw["schedule_time"]):
        cfg["schedule_time"] = raw["schedule_time"]
    cfg["email"] = _clean_email(raw.get("email"))
    cfg["folder_search"] = _clean_folder_search(raw.get("folder_search"))
    return cfg


def _clean_email(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"index_path": None, "roots": []}
    if not isinstance(raw, dict):
        return out
    path = raw.get("index_path")
    if isinstance(path, str) and path.strip():
        out["index_path"] = path.strip()
    roots = raw.get("roots")
    if isinstance(roots, list):
        out["roots"] = [r.strip() for r in roots if isinstance(r, str) and r.strip()]
    return out


def _clean_folder_search(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["folder_search"])
    if not isinstance(raw, dict):
        return out
    skip = raw.get("skip_levels")
    if isinstance(skip, int) and not isinstance(skip, bool) and 0 <= skip <= MAX_SKIP_LEVELS:
        out["skip_levels"] = skip
    if isinstance(raw.get("prune_people"), bool):
        out["prune_people"] = raw["prune_people"]
    return out


def load() -> dict[str, Any]:
    """The effective settings: the file's valid keys over the defaults."""
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _clean({})
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ config.json unreadable, using defaults: %s", exc)
        return _clean({})
    return _clean(raw if isinstance(raw, dict) else {})


def validate_update(updates: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``updates`` that is a valid change; raises ``ValueError`` on a bad value."""
    out: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in DEFAULTS or key in ("port", "server"):
            raise ValueError("unknown or read-only setting: " + key)
        if key == "remote_access" and not isinstance(value, bool):
            raise ValueError("remote_access must be true or false")
        if key == "schedule" and value not in SCHEDULES:
            raise ValueError("schedule must be one of " + ", ".join(SCHEDULES))
        if key == "schedule_time" and not (isinstance(value, str) and _TIME_RE.match(value)):
            raise ValueError("schedule_time must be HH:MM")
        if key == "roots":
            if value is not None and not (isinstance(value, list) and all(isinstance(r, str) and r.strip() for r in value)):
                raise ValueError("roots must be null or a list of folder paths")
            value = [r.strip() for r in value] if value else None
        if key == "email":
            value = _validate_email(value)
        if key == "folder_search":
            value = _validate_folder_search(value)
        out[key] = value
    return out


def _validate_email(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"index_path", "roots"}:
        raise ValueError("email takes index_path and roots")
    path = value.get("index_path")
    if path is not None and not isinstance(path, str):
        raise ValueError("email.index_path must be a file path or null")
    roots = value.get("roots", [])
    if not (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
        raise ValueError("email.roots must be a list of folder paths")
    return _clean_email(value)


def _validate_folder_search(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"skip_levels", "prune_people"}:
        raise ValueError("folder_search takes skip_levels and prune_people")
    skip = value.get("skip_levels", 0)
    if not (isinstance(skip, int) and not isinstance(skip, bool) and 0 <= skip <= MAX_SKIP_LEVELS):
        raise ValueError("folder_search.skip_levels must be 0 to %d" % MAX_SKIP_LEVELS)
    if not isinstance(value.get("prune_people", True), bool):
        raise ValueError("folder_search.prune_people must be true or false")
    return _clean_folder_search(value)


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge valid ``updates`` into the file and return the new effective settings."""
    clean = validate_update(updates)
    path = config_path()
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current.update(clean)
    tmp = path.with_name(path.name + ".tmp")
    # runtime-write-exempt: config.json at the repo root is the documented settings file (gitignored)
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    logger.info("ℹ️ settings saved: %s", ", ".join(sorted(clean)))
    return load()
