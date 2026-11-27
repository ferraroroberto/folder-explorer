"""Each shared rule is spelled in one module, so a fix to it can't reach one copy and miss another.

A rule that gets its own owner adds one line to ``RULES``: the pattern its code can't be re-written without, and the
files allowed to hold it. This finds a second spelling of a known rule; jscpd (``scripts/static_ratchet.py``) finds
copied blocks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ("src/**/*.py", "app/**/*.py")
SCRIPTS = ("app/webapp/static/**/*.js",)

# What the rule is -> (where to look, the pattern, the files that may hold it).
RULES: dict[str, tuple[tuple[str, ...], str, set[str]]] = {
    "a program's App Paths lookup": (PYTHON, r"CurrentVersion\\+App Paths", {"src/desktop.py"}),
    "entering the email stage": (PYTHON, r"NotSetUp\(part\.stage_problem", {"src/email/indexer.py"}),
    "the email relevance weights": (PYTHON, r"bm25\(emails_fts, \d+\.\d", {"src/email/query.py"}),
    "the body preview length": (PYTHON, r"(?i)preview\w*(?:: int)? = 500\b", {"src/email/archive_settings.py"}),
    "a child process's environment": (PYTHON, r"PYTHONUNBUFFERED=", {"src/child.py"}),
    "listing the shown Explorer tabs": (PYTHON, r"[^.\w]is_shown_tab\(window\)", {"src/explorer_window.py"}),
    "one path being inside another": (PYTHON, r"startswith\([^\n]*\.rstrip\([^\n]*\+ os\.sep\)", {"src/roots.py"}),
    "the innermost root of a path": (PYTHON, r"key=lambda r: len\(r\.path\)", {"src/roots.py"}),
    "the folder then the file in an index": (PYTHON, r"find_folder\(\w+\[:-1\]\)", {"src/index.py"}),
    "the token's variable name": (PYTHON, r"= \"FOLDER_EXPLORER_TOKEN\"", {"src/secrets_env.py"}),
    "the this-PC-only refusal": (PYTHON, r"\"this works on the PC only\"", {"app/webapp/auth.py"}),
    "a JSON request": (SCRIPTS, r"'Content-Type': 'application/json'", {"app/webapp/static/api.js"}),
    "a code listing's copy control": (SCRIPTS, r"closest\('\.code-listing-copy'\)", set()),
}


def _holders(globs: tuple[str, ...], pattern: str) -> set[str]:
    found = set()
    for glob in globs:
        for path in ROOT.glob(glob):
            name = path.relative_to(ROOT).as_posix()
            if "/_vendored/" not in name and re.search(pattern, path.read_text(encoding="utf-8")):
                found.add(name)
    return found


@pytest.mark.parametrize("rule", RULES)
def test_a_shared_rule_is_spelled_only_by_its_owner(rule: str) -> None:
    globs, pattern, owners = RULES[rule]
    # Equal, not a subset: a pattern that no longer matches its owner would pass while checking nothing.
    assert _holders(globs, pattern) == owners
