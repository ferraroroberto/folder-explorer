"""The region screenshot's settings: the ``snap`` block of ``config.json``, checked key by key.

``src.config`` cleans the block with :func:`clean` on every load (a bad key takes its default and
says so) and writes it with :func:`validate` (a bad value raises ``ConfigError`` before anything is
written). :func:`resolve` turns a cleaned block into the :class:`SnapConfig` one capture uses.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from src.snap.core import ConfigError, parse_hotkey, validate_prefix

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    # null saves under the user's Pictures folder, in snaps; a path names another folder (~ and %VARS% expand).
    "folder": None,
    "hotkey": "ctrl+shift+a",
    "jpeg_quality": 90,
    "copy_to_clipboard": True,
    "file_prefix": "snap",
    "show_loupe": True,
}


@dataclass(frozen=True)
class SnapConfig:
    """What one capture reads: the settings, with the folder expanded."""

    folder: Path
    hotkey: str
    jpeg_quality: int
    copy_to_clipboard: bool
    file_prefix: str
    show_loupe: bool


def _folder(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ConfigError("'folder' must be a folder path, or null for the Pictures folder")


def _hotkey(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("'hotkey' must be a string such as 'ctrl+shift+a'")
    parse_hotkey(value)
    return value.strip().lower()


def _quality(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ConfigError(f"'jpeg_quality' must be a whole number from 1 to 100, got {value!r}")
    return value


def _prefix(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("'file_prefix' must be a string such as 'snap'")
    return validate_prefix(value)


def _flag(name: str) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{name!r} must be true or false")
        return value

    return check


_CHECKS = {
    "folder": _folder,
    "hotkey": _hotkey,
    "jpeg_quality": _quality,
    "copy_to_clipboard": _flag("copy_to_clipboard"),
    "file_prefix": _prefix,
    "show_loupe": _flag("show_loupe"),
}


def validate(updates: Mapping[str, Any]) -> dict[str, Any]:
    """The checked form of ``updates``; raises ``ConfigError`` for an unknown key or a value that cannot be used."""
    unknown = sorted(set(updates) - set(_CHECKS))
    if unknown:
        raise ConfigError(f"unknown snap setting(s) {unknown}; valid settings are {sorted(_CHECKS)}")
    return {key: _CHECKS[key](value) for key, value in updates.items()}


def clean(raw: Any) -> dict[str, Any]:
    """Every setting, the file's valid ones over the defaults; a value that cannot be used is logged and takes its default."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        try:
            out.update(validate({key: value}))
        except ConfigError as exc:
            logger.warning("⚠️ snap: ignoring a setting in config.json, using the default: %s", exc)
    return out


def resolve(block: Mapping[str, Any]) -> SnapConfig:
    """The :class:`SnapConfig` for a cleaned ``snap`` block."""
    folder = block["folder"]
    path = Path(os.path.expandvars(folder)).expanduser() if folder else Path.home() / "Pictures" / "snaps"
    return SnapConfig(
        folder=path,
        hotkey=block["hotkey"],
        jpeg_quality=block["jpeg_quality"],
        copy_to_clipboard=block["copy_to_clipboard"],
        file_prefix=block["file_prefix"],
        show_loupe=block["show_loupe"],
    )
