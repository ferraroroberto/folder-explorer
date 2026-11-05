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
    # Folders the walk skips, whatever root they sit in: the disk index,
    # folder search and the email stage all leave them out.
    "exclude": [],
    # The email part. roots are the email folders, each inside a scan root:
    # the email stage indexes their .msg files and "Show all" reads one.
    # index_path null means this app's own index in the runtime-data folder;
    # a path names another app's index, read-only, and the stage stays off.
    # download_budget_mb caps what one run downloads from OneDrive (0: never).
    "email": {"index_path": None, "roots": [], "download_budget_mb": 2048},
    # Folder search: how many leading path components a result hides (display
    # only) and whether a folder of per-person subfolders shows as one result.
    "folder_search": {"skip_levels": 0, "prune_people": True},
    # The archive verbs (python -m app.cli email batch): the file-name form
    # (date_prefix true, false or "auto" to follow the destination folder),
    # the longest path the archiver writes, and the folder suggester's limits.
    "archive": {"date_prefix": False, "max_path_length": 255, "max_suggestions": 3, "min_score": 0.05},
    # The Outlook side of the archive verbs: the folder a filed mail moves to,
    # the categories they stamp, and the address a draft blind-copies (null:
    # the default account's, read from Outlook). Set by editing config.json.
    "outlook": {"archive_folder": "Archive", "category": "Archived by task-os",
                "dismiss_category": "Dismissed by task-os", "self_address": None},
}

# The most leading components folder search may hide.
MAX_SKIP_LEVELS = 8
# The largest per-run download budget Settings accepts, in MB.
MAX_BUDGET_MB = 1024 * 1024
_EMAIL_KEYS = {"index_path", "roots", "download_budget_mb"}
# The longest path the archiver may be told to write; Windows' own limit is 260.
MAX_PATH_LENGTH = 260
# Settings only config.json sets: no Settings control writes them.
READ_ONLY = ("port", "server", "archive", "outlook")

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
    cfg["exclude"] = _clean_paths(raw.get("exclude"))
    cfg["email"] = _clean_email(raw.get("email"))
    cfg["folder_search"] = _clean_folder_search(raw.get("folder_search"))
    cfg["archive"] = _clean_archive(raw.get("archive"))
    cfg["outlook"] = _clean_outlook(raw.get("outlook"))
    return cfg


def _whole(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _clean_archive(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["archive"])
    if not isinstance(raw, dict):
        return out
    prefix = raw.get("date_prefix")
    if isinstance(prefix, bool) or (isinstance(prefix, str) and prefix.strip().lower() == "auto"):
        out["date_prefix"] = prefix if isinstance(prefix, bool) else "auto"
    if _whole(raw.get("max_path_length"), 100, MAX_PATH_LENGTH):
        out["max_path_length"] = raw["max_path_length"]
    if _whole(raw.get("max_suggestions"), 1, 50):
        out["max_suggestions"] = raw["max_suggestions"]
    floor = raw.get("min_score")
    if isinstance(floor, (int, float)) and not isinstance(floor, bool) and 0 <= floor <= 1:
        out["min_score"] = float(floor)
    return out


def _clean_outlook(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["outlook"])
    if not isinstance(raw, dict):
        return out
    for key in ("archive_folder", "category", "dismiss_category", "self_address"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _clean_paths(raw: Any) -> list[str]:
    return [r.strip() for r in raw if isinstance(r, str) and r.strip()] if isinstance(raw, list) else []


def _clean_email(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["email"])
    if not isinstance(raw, dict):
        return out
    path = raw.get("index_path")
    if isinstance(path, str) and path.strip():
        out["index_path"] = path.strip()
    out["roots"] = _clean_paths(raw.get("roots"))
    budget = raw.get("download_budget_mb")
    if isinstance(budget, int) and not isinstance(budget, bool) and 0 <= budget <= MAX_BUDGET_MB:
        out["download_budget_mb"] = budget
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
        if key not in DEFAULTS or key in READ_ONLY:
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
        if key == "exclude":
            if not (isinstance(value, list) and all(isinstance(r, str) for r in value)):
                raise ValueError("exclude must be a list of folder paths")
            value = _clean_paths(value)
        if key == "email":
            value = _validate_email(value)
        if key == "folder_search":
            value = _validate_folder_search(value)
        out[key] = value
    return out


def _validate_email(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _EMAIL_KEYS:
        raise ValueError("email takes index_path, roots and download_budget_mb")
    path = value.get("index_path")
    if path is not None and not isinstance(path, str):
        raise ValueError("email.index_path must be a file path or null")
    roots = value.get("roots", [])
    if not (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
        raise ValueError("email.roots must be a list of folder paths")
    budget = value.get("download_budget_mb", DEFAULTS["email"]["download_budget_mb"])
    if not (isinstance(budget, int) and not isinstance(budget, bool) and 0 <= budget <= MAX_BUDGET_MB):
        raise ValueError("email.download_budget_mb must be a whole number of MB, 0 or more")
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
