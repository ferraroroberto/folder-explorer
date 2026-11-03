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

DEFAULTS: dict[str, Any] = {
    # The one switch for reaching the app from the phone. Off binds loopback
    # only, over plain HTTP, with no certificate and no sign-in.
    "remote_access": True,
    "port": 8453,
    # null = every local fixed drive plus OneDrive; a list names the roots.
    "roots": None,
    # The in-app scan schedule. Off by default: on this PC the nightly
    # app-launcher job runs the CLI, and the two must never double-run.
    "schedule": "off",
    "schedule_time": "03:00",
}

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
    roots = raw.get("roots")
    if roots is None or (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
        cfg["roots"] = [r.strip() for r in roots if r.strip()] if roots else None
    if raw.get("schedule") in SCHEDULES:
        cfg["schedule"] = raw["schedule"]
    if isinstance(raw.get("schedule_time"), str) and _TIME_RE.match(raw["schedule_time"]):
        cfg["schedule_time"] = raw["schedule_time"]
    return cfg


def load() -> dict[str, Any]:
    """The effective settings: the file's valid keys over the defaults."""
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ config.json unreadable, using defaults: %s", exc)
        return dict(DEFAULTS)
    return _clean(raw if isinstance(raw, dict) else {})


def validate_update(updates: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``updates`` that is a valid change; raises ``ValueError`` on a bad value."""
    out: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in DEFAULTS or key == "port":
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
        out[key] = value
    return out


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
