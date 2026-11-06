"""The Outlook macro's one machine setting: where the primary checkout is, and whether ``FOLDER_EXPLORER_HOME`` points at it.

Display only. Nothing here runs ``setx`` or writes the environment; the user
environment (HKCU) is opened read-only. The macro runs inside Outlook, which
reads the variable at its own start, so the readout looks at the registry
rather than at this process's environment.
"""

from __future__ import annotations

import logging
import ntpath
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

VARIABLE = "FOLDER_EXPLORER_HOME"

STATE_OK = "ok"
STATE_DIFFERENT = "different"
STATE_UNSET = "unset"
STATE_UNKNOWN = "unknown"


def primary_checkout(project_root: Path) -> Path:
    """The primary checkout, even when ``project_root`` is a linked worktree.

    A worktree's ``.git`` is a file holding ``gitdir: <primary>/.git/worktrees/<name>``;
    the primary is three levels above that. A plain checkout keeps ``.git`` as a folder.
    """
    dot_git = project_root / ".git"
    if not dot_git.is_file():
        return project_root
    try:
        first = dot_git.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        logger.warning("⚠️ could not read %s; using the running folder as the checkout", dot_git)
        return project_root
    if not first.startswith("gitdir:"):
        return project_root
    gitdir = Path(first[len("gitdir:"):].strip())
    if gitdir.parent.name != "worktrees":
        return project_root
    return gitdir.parent.parent.parent


def read_user_variable() -> Optional[str]:
    """The user-scope value of the variable, ``""`` when it is not set; ``None`` when it can't be read."""
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VARIABLE)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("⚠️ could not read the user environment: %s", exc)
        return None
    return str(value)


def _same_folder(a: str, b: str) -> bool:
    def norm(p: str) -> str:
        return ntpath.normcase(ntpath.normpath(p.strip().strip('"')))

    return norm(a) == norm(b)


def state_for(current: Optional[str], home: str) -> str:
    if current is None:
        return STATE_UNKNOWN
    if not current.strip():
        return STATE_UNSET
    return STATE_OK if _same_folder(current, home) else STATE_DIFFERENT


def setup(project_root: Path) -> dict[str, Any]:
    """The copyable command and the variable's state, for the macro sheet."""
    home = str(primary_checkout(project_root))
    current = read_user_variable()
    return {
        "command": f'setx {VARIABLE} "{home}"',
        "home": home,
        "current": current or None,
        "state": state_for(current, home),
    }
