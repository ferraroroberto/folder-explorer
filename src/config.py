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

from src.atomic_write import atomic_write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ENV = "FOLDER_EXPLORER_CONFIG"

SCHEDULES = ("off", "nightly", "hourly")
SERVERS = ("hypercorn", "uvicorn")
# What a left click on the tray icon does: the Quick search window, or the web app in the browser.
LEFT_CLICKS = ("quick_search", "open_app")

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
    # download_budget_mb caps what one run downloads from OneDrive: null is no limit (one run
    # downloads everything it needs), a whole number is a cap in MB, 0 never downloads.
    # exclude names folders inside the email folders that only the email
    # index leaves out; the global exclude above still applies to it too.
    "email": {"index_path": None, "roots": [], "exclude": [], "download_budget_mb": None},
    # Folder search: how many leading path components a result hides (display
    # only), whether a folder of per-person subfolders shows as one result, and
    # whether a folder in a scanned OneDrive root is listed once, not also under its drive.
    "folder_search": {"skip_levels": 0, "prune_people": True, "onedrive_prevails": True},
    # Explore: whether hidden and system items are listed. Off by default, as
    # in File Explorer; the totals count them either way.
    "explore": {"show_hidden": False},
    # Opening a folder on this PC: whether it shows in the foremost open File Explorer window
    # (faster, but that window loses what it showed) instead of a new one. Off by default.
    "desktop": {"reuse_explorer_window": False},
    # The Outlook macro's Archive button: on opens the small Archive window even when a page is open
    # (and skips the hand-off wait); off hands the mail to an open page and opens the window only without one.
    "macro": {"prefer_window": True},
    # The tray icon: a left click opens the small always-on-top Quick search window (folders and email)
    # or the web app; the tray menu offers both either way.
    "tray": {"left_click": "quick_search"},
    # The archive verbs (python -m app.cli email batch): the file-name form
    # (date_prefix true, false or "auto" to follow the destination folder),
    # the longest path the archiver writes, and the folder suggester's limits.
    "archive": {"date_prefix": False, "max_path_length": 255, "max_suggestions": 3, "min_score": 0.05},
    # The Outlook side of the archive verbs: the folder a filed mail moves to,
    # the categories they stamp, and the address a draft blind-copies (null:
    # the default account's, read from Outlook). Set by editing config.json,
    # except the two Settings switches: add_category (the category the Archive
    # window and sheet stamp; off: neither offers one and filing stamps none)
    # and move_to_archive (off: filing leaves the mail where it is in Outlook).
    "outlook": {"archive_folder": "Archive", "category": "Archived by task-os",
                "dismiss_category": "Dismissed by task-os", "self_address": None,
                "add_category": False, "move_to_archive": False},
}

# The outlook keys the Settings screen switches; the rest of the block is edited in config.json.
OUTLOOK_SWITCHES = ("add_category", "move_to_archive")

# The most leading components folder search may hide.
MAX_SKIP_LEVELS = 8
# The largest per-run download budget Settings accepts, in MB.
MAX_BUDGET_MB = 1024 * 1024
_EMAIL_KEYS = {"index_path", "roots", "exclude", "download_budget_mb"}
# The longest path the archiver may be told to write; Windows' own limit is 260.
MAX_PATH_LENGTH = 260
# Settings only config.json sets: no Settings control writes them.
READ_ONLY = ("port", "server", "archive")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV, "").strip()
    return Path(override) if override else PROJECT_ROOT / "config.json"


def example_path() -> Path:
    return PROJECT_ROOT / "config.example.json"


def create_from_example() -> bool:
    """Write config.json from config.example.json; False, and nothing touched, when the file already exists."""
    text = example_path().read_text(encoding="utf-8")
    path = config_path()
    # runtime-write-exempt: the user's own config.json (or its FOLDER_EXPLORER_CONFIG override), not runtime output
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Mode "x" fails on an existing file, so a config the user has can never be overwritten.
        # runtime-write-exempt: the user's own config.json, created once and never over an existing file
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        return False
    logger.info("ℹ️ config.json created from config.example.json")
    return True


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
    cfg["explore"] = _clean_explore(raw.get("explore"))
    cfg["desktop"] = _clean_desktop(raw.get("desktop"))
    cfg["macro"] = _clean_macro(raw.get("macro"))
    cfg["tray"] = _clean_tray(raw.get("tray"))
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
    for key in OUTLOOK_SWITCHES:
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    return out


def _clean_paths(raw: Any) -> list[str]:
    return [r.strip() for r in raw if isinstance(r, str) and r.strip()] if isinstance(raw, list) else []


def _budget_ok(value: Any) -> bool:
    """None (no limit) or a whole number of MB from 0 to ``MAX_BUDGET_MB``."""
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_BUDGET_MB)


def _clean_email(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["email"])
    if not isinstance(raw, dict):
        return out
    path = raw.get("index_path")
    if isinstance(path, str) and path.strip():
        out["index_path"] = path.strip()
    out["roots"] = _clean_paths(raw.get("roots"))
    out["exclude"] = _clean_paths(raw.get("exclude"))
    budget = raw.get("download_budget_mb")
    if _budget_ok(budget):
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
    if isinstance(raw.get("onedrive_prevails"), bool):
        out["onedrive_prevails"] = raw["onedrive_prevails"]
    return out


def _clean_explore(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["explore"])
    if isinstance(raw, dict) and isinstance(raw.get("show_hidden"), bool):
        out["show_hidden"] = raw["show_hidden"]
    return out


def _clean_desktop(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["desktop"])
    if isinstance(raw, dict) and isinstance(raw.get("reuse_explorer_window"), bool):
        out["reuse_explorer_window"] = raw["reuse_explorer_window"]
    return out


def _clean_macro(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["macro"])
    if isinstance(raw, dict) and isinstance(raw.get("prefer_window"), bool):
        out["prefer_window"] = raw["prefer_window"]
    return out


def _clean_tray(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = dict(DEFAULTS["tray"])
    if isinstance(raw, dict) and raw.get("left_click") in LEFT_CLICKS:
        out["left_click"] = raw["left_click"]
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
        if key == "explore":
            if not (isinstance(value, dict) and set(value) == {"show_hidden"} and isinstance(value["show_hidden"], bool)):
                raise ValueError("explore takes show_hidden, true or false")
        if key == "desktop":
            if not (isinstance(value, dict) and set(value) == {"reuse_explorer_window"}
                    and isinstance(value["reuse_explorer_window"], bool)):
                raise ValueError("desktop takes reuse_explorer_window, true or false")
        if key == "macro":
            if not (isinstance(value, dict) and set(value) == {"prefer_window"} and isinstance(value["prefer_window"], bool)):
                raise ValueError("macro takes prefer_window, true or false")
        if key == "tray":
            if not (isinstance(value, dict) and set(value) == {"left_click"} and value["left_click"] in LEFT_CLICKS):
                raise ValueError("tray takes left_click, one of " + ", ".join(LEFT_CLICKS))
        if key == "outlook":
            if not (isinstance(value, dict) and value and set(value) <= set(OUTLOOK_SWITCHES)
                    and all(isinstance(v, bool) for v in value.values())):
                raise ValueError("outlook takes " + " and ".join(OUTLOOK_SWITCHES) + ", true or false")
        out[key] = value
    return out


def _validate_email(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _EMAIL_KEYS:
        raise ValueError("email takes index_path, roots, exclude and download_budget_mb")
    path = value.get("index_path")
    if path is not None and not isinstance(path, str):
        raise ValueError("email.index_path must be a file path or null")
    roots = value.get("roots", [])
    if not (isinstance(roots, list) and all(isinstance(r, str) for r in roots)):
        raise ValueError("email.roots must be a list of folder paths")
    excluded = value.get("exclude", [])
    if not (isinstance(excluded, list) and all(isinstance(r, str) for r in excluded)):
        raise ValueError("email.exclude must be a list of folder paths")
    budget = value.get("download_budget_mb", DEFAULTS["email"]["download_budget_mb"])
    if not _budget_ok(budget):
        raise ValueError("email.download_budget_mb must be null (no limit) or a whole number of MB, 0 or more")
    return _clean_email(value)


def _validate_folder_search(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(DEFAULTS["folder_search"]):
        raise ValueError("folder_search takes skip_levels, prune_people and onedrive_prevails")
    skip = value.get("skip_levels", 0)
    if not (isinstance(skip, int) and not isinstance(skip, bool) and 0 <= skip <= MAX_SKIP_LEVELS):
        raise ValueError("folder_search.skip_levels must be 0 to %d" % MAX_SKIP_LEVELS)
    for key in ("prune_people", "onedrive_prevails"):
        if not isinstance(value.get(key, True), bool):
            raise ValueError("folder_search.%s must be true or false" % key)
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
    for key, value in clean.items():
        # Settings sets one outlook key; the folder and category names the file holds stay as they are.
        if key == "outlook":
            held = current.get("outlook")
            value = {**(held if isinstance(held, dict) else {}), **value}
        current[key] = value
    atomic_write_text(path, json.dumps(current, indent=2) + "\n")
    logger.info("ℹ️ settings saved: %s", ", ".join(sorted(clean)))
    return load()
