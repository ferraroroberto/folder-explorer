"""The installer's machine-local files: create one from its tracked template when missing, never overwrite one.

Standard library only: ``python -m app.install`` runs these with the system Python, before ``.venv`` exists.

* ``config.json`` from ``config.example.json``: created when missing; when present, only the keys the template has and
  the file lacks are added (nested blocks included), after a timestamped backup. No existing value is ever changed. A
  file that does not parse, or whose top level is not an object, is left untouched and reported.
* ``.env`` from ``.env.example``: created when missing, never merged. A merge would need a backup, and a backup of
  ``.env`` is one more copy of the secrets; every key in it is optional.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.atomic_write import atomic_write_text

CONFIG_ENV = "FOLDER_EXPLORER_CONFIG"
CONFIG_TEMPLATE = "config.example.json"
ENV_NAME = ".env"
ENV_TEMPLATE = ".env.example"

# What each outcome means in the installer's report; "unchanged" and "kept" are the idempotent ones.
CREATED = "created"
ADDED = "added keys"
UNCHANGED = "unchanged"
KEPT = "kept"
UNREADABLE = "unreadable"
NO_TEMPLATE = "no template"


@dataclass
class FileOutcome:
    """What the installer did, or would do under ``-WhatIf``, to one machine-local file."""

    name: str
    status: str
    added: list[str] = field(default_factory=list)
    backup: Path | None = None
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.status in (CREATED, ADDED)


def config_target(root: Path, env: Mapping[str, str]) -> Path:
    """The config file the app reads: ``FOLDER_EXPLORER_CONFIG`` when set (as ``src/config.py`` resolves it)."""
    override = env.get(CONFIG_ENV, "").strip()
    return Path(override) if override else root / "config.json"


def missing_keys(existing: dict[str, Any], template: dict[str, Any], prefix: str = "") -> list[str]:
    """Dotted names of the template keys ``existing`` lacks. A key whose value is not an object is never entered."""
    out: list[str] = []
    for key, value in template.items():
        name = f"{prefix}{key}"
        if key not in existing:
            out.append(name)
        elif isinstance(value, dict) and isinstance(existing[key], dict):
            out.extend(missing_keys(existing[key], value, f"{name}."))
    return out


def merged(existing: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """``existing`` with every missing template key added; every existing value kept as it is, in its order."""
    out = dict(existing)
    for key, value in template.items():
        if key not in out:
            out[key] = value
        elif isinstance(value, dict) and isinstance(out[key], dict):
            out[key] = merged(out[key], value)
    return out


def _backup_path(target: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    candidate = target.with_name(f"{target.name}.{stamp}.bak")
    n = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.{stamp}-{n}.bak")
        n += 1
    return candidate


def _create_exclusive(target: Path, text: str) -> bool:
    """Write ``text`` to a new file; False, and nothing touched, when one already exists."""
    # runtime-write-exempt: the user's own config.json or .env, created once beside the code (or where its override says)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Mode "x" fails on an existing file, so the user's own copy can never be overwritten.
        # runtime-write-exempt: the user's own config.json or .env, never over an existing file
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        return False
    return True


def ensure_config(root: Path, env: Mapping[str, str], *, what_if: bool, now: datetime | None = None) -> FileOutcome:
    """Create the config from its template, or add only the keys it lacks (after a backup)."""
    template_path = root / CONFIG_TEMPLATE
    target = config_target(root, env)
    name = target.name if target.parent == root else str(target)
    if not template_path.is_file():
        return FileOutcome(name, NO_TEMPLATE, detail=f"{CONFIG_TEMPLATE} is missing from the checkout")
    template_text = template_path.read_text(encoding="utf-8")
    template = json.loads(template_text)
    # A file that appears between the check and the create is read as an existing one below.
    if not target.exists() and (what_if or _create_exclusive(target, template_text)):
        return FileOutcome(name, CREATED, detail=f"from {CONFIG_TEMPLATE}")
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return FileOutcome(name, UNREADABLE, detail=f"left as it is: {exc}")
    if not isinstance(existing, dict):
        return FileOutcome(name, UNREADABLE, detail="left as it is: the top level is not a JSON object")
    added = missing_keys(existing, template)
    if not added:
        return FileOutcome(name, UNCHANGED)
    backup = _backup_path(target, now or datetime.now())
    if not what_if:
        # runtime-write-exempt: the backup of the user's own config, beside it, before any change
        shutil.copy2(target, backup)
        atomic_write_text(target, json.dumps(merged(existing, template), indent=2) + "\n")
    return FileOutcome(name, ADDED, added=added, backup=backup)


def ensure_env(root: Path, *, what_if: bool) -> FileOutcome:
    """Create ``.env`` from ``.env.example`` when missing; an existing one is kept, never read or merged."""
    template_path = root / ENV_TEMPLATE
    target = root / ENV_NAME
    if target.exists():
        return FileOutcome(ENV_NAME, KEPT)
    if not template_path.is_file():
        return FileOutcome(ENV_NAME, NO_TEMPLATE, detail=f"{ENV_TEMPLATE} is missing from the checkout")
    if not what_if and not _create_exclusive(target, template_path.read_text(encoding="utf-8")):
        return FileOutcome(ENV_NAME, KEPT)
    return FileOutcome(ENV_NAME, CREATED, detail=f"from {ENV_TEMPLATE}")
