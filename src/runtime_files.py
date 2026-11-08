"""The writer helper for paths a caller names: it refuses any path under a code folder.

The runtime-write ratchet cannot resolve a path that arrives as a parameter, so each
such write used to carry its own marker. They go through here instead: every function
resolves the path and raises ``CodeFolderError`` when it lands under ``src/``, ``app/``,
``scripts/``, ``hooks/`` or ``skills/`` of this checkout, so the guarantee is enforced
at runtime and the markers below say so once. Gitignored files in the checkout's root
(``config.json``, ``.env``) and everything outside the tree stay allowed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import IO, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# The same folders as `[runtime_write_ratchet].code_folders` in .fleet.toml.
CODE_FOLDERS = ("src", "app", "scripts", "hooks", "skills")


class CodeFolderError(ValueError):
    """The path resolves under a code folder, where no runtime output belongs."""


def checked(path: Path | str) -> Path:
    """``path`` resolved; raises ``CodeFolderError`` when it is under a code folder of this checkout."""
    resolved = Path(path).resolve()
    for folder in CODE_FOLDERS:
        if resolved.is_relative_to(PROJECT_ROOT / folder):
            raise CodeFolderError(f"{resolved.name}: inside the code folder {folder}/, not a place for runtime output")
    return resolved


def make_dirs(path: Path | str) -> None:
    """Create ``path`` and its parents."""
    # runtime-write-exempt: checked() refuses a path under a code folder, so this is never code
    checked(path).mkdir(parents=True, exist_ok=True)


def open_file(path: Path | str, mode: str) -> IO[bytes]:
    """Open ``path`` in a binary ``mode`` (``"a+b"`` for a lock file)."""
    # runtime-write-exempt: checked() refuses a path under a code folder, so this is never code
    return open(checked(path), mode)


def connect(path: Path | str, **kwargs: Any) -> sqlite3.Connection:
    """A read-write SQLite connection to ``path``; ``kwargs`` go to ``sqlite3.connect``."""
    # runtime-write-exempt: checked() refuses a path under a code folder, so this is never code
    return sqlite3.connect(str(checked(path)), **kwargs)


def connect_read_only(path: Path | str, *, immutable: bool = False, **kwargs: Any) -> sqlite3.Connection:
    """A read-only SQLite connection (``mode=ro``, plus ``immutable=1`` for a file that never changes)."""
    uri = checked(path).as_uri() + ("?mode=ro&immutable=1" if immutable else "?mode=ro")
    # runtime-write-exempt: opened with mode=ro, so it writes nothing wherever the file is
    return sqlite3.connect(uri, uri=True, **kwargs)
