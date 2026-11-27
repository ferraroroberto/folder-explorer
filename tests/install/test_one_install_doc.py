"""The install, update, migrate and uninstall steps live in ``docs/install.md`` only: a second doc that opens a
section for one of them is a second copy of the procedure, and two copies drift.

Headings are the check, so a procedure restated in running prose under another heading is not caught.
README.md is left out: it is the index, and its "Install" section points at the doc.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
HOME = DOCS / "install.md"

# Matched at the start of a heading only, so a surface named "update banner" is not a procedure.
_PROCEDURE = re.compile(r"(fresh (pc|install)|set(ting)? up\b|install\b|updat(e|ing)\b|migrat|uninstall)", re.I)
_HEADING = re.compile(r"#{1,6}\s+(.*)")


def _procedure_headings(path: Path) -> list[str]:
    found, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        heading = None if fenced else _HEADING.match(line)
        if heading and _PROCEDURE.match(heading.group(1)):
            found.append(heading.group(1))
    return found


def test_only_install_md_has_the_install_procedures() -> None:
    # The home must still match, or a renamed heading would leave the rule checking nothing.
    assert len(_procedure_headings(HOME)) >= 4
    copies = {str(p.relative_to(DOCS)): h for p in sorted(DOCS.rglob("*.md")) if p != HOME
              for h in [_procedure_headings(p)] if h}
    assert not copies, f"move these steps into docs/install.md and link there: {copies}"
