"""The document sheet and the /doc window share one view/edit controller, and neither carries its own copy.

The lifecycle (mode, editor, a dropped file's handle, the race guard) lives in
``app/webapp/static/dochost/doc-host.js``. A host that opens the editor or reads a local
file itself would grow a parallel copy that every editing change has to match
by hand, which is how the two drifted before.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "webapp" / "static"
HOST = STATIC / "dochost" / "doc-host.js"
HOSTS = [STATIC / "doc-sheet.js", STATIC / "doc-page.js"]
# What only the shared controller may use: opening the editor, reading a local file, and the mode switch.
LIFECYCLE = re.compile(r"\b(openEditor|saveButtonHooks|renderLocal|withLocal)\b|function\s+(markMode|startEditing|stopEditing|viewLocal|leaveEditor)\b")


def test_each_host_uses_the_shared_controller() -> None:
    for page in HOSTS:
        source = page.read_text(encoding="utf-8")
        assert "from './dochost/doc-host.js'" in source, page.name
        assert "createDocHost(" in source, page.name


def test_no_host_carries_its_own_lifecycle() -> None:
    for page in HOSTS:
        assert LIFECYCLE.findall(page.read_text(encoding="utf-8")) == [], page.name


def test_the_controller_holds_the_lifecycle_once() -> None:
    source = HOST.read_text(encoding="utf-8")
    for name in ("markMode", "startEditing", "stopEditing", "viewLocal", "leaveEditor"):
        assert len(re.findall(rf"function\s+{name}\b", source)) == 1, name
    assert source.count("openEditor(") == 1
    assert source.count("renderLocal(") == 1
