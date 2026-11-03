"""``pythonw -m app.tray``: the tray that owns the webapp (see ``tray.bat``)."""

from __future__ import annotations

import sys

from app.tray.tray import main

if __name__ == "__main__":
    sys.exit(main())
