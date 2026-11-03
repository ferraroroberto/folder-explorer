"""Regenerate the app icons from the fleet brand master, through project-scaffolding's ``brand_gen``.

The master is the Lucide ``folder-tree`` glyph in project-scaffolding's
``brand/`` library; ``brand_gen.render_set`` writes the favicon, the Apple
touch icon, the manifest icons, the maskable icon, the tray icon and the
Stream Deck tile. The outputs are committed, so nothing at runtime imports
this script. It needs a project-scaffolding checkout beside this repo (or
``FLEET_SCAFFOLD_DIR``) and ``resvg-py`` (``requirements-dev.txt``):

    .venv\\Scripts\\python.exe scripts\\gen_icons.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAFFOLD = Path(os.environ.get("FLEET_SCAFFOLD_DIR") or PROJECT_ROOT.parent / "project-scaffolding")


def main() -> int:
    sys.path.insert(0, str(SCAFFOLD / "scripts"))
    from brand_gen import render_set

    render_set(
        master=SCAFFOLD / "brand" / "folder-tree.svg",
        out_dir=PROJECT_ROOT / "app" / "webapp" / "static",
        tray_out_dir=PROJECT_ROOT / "assets" / "tray",
        stream_deck_out_dir=PROJECT_ROOT / "assets" / "stream-deck",
        project_slug="folder-explorer",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
