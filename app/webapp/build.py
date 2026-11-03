"""The one build identity of this webapp process: git SHA, start time, asset hash."""

from __future__ import annotations

from pathlib import Path

from src.config import PROJECT_ROOT
from src.static_versioning import BuildInfo

STATIC_DIR = Path(__file__).resolve().parent / "static"

BUILD_INFO = BuildInfo(STATIC_DIR, PROJECT_ROOT)
