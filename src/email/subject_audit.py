"""A dry run that lists archived mails whose file name lost its first letters to the old reply-prefix rule.

The old rule took "Re", "Rv" and "Fw" off any subject that started with them,
colon or not, so "Recibo de pago" was filed as ``cibo de pago``. This lists the
files still carrying such a name. It renames nothing and writes nothing: it
reads each ``.msg`` subject through the one reader (``reader.read_facts``),
inside the email folders only, under the email stage's own download budget, and
takes the same lock so it never runs beside an index run.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from src import config
from src.email import indexer
from src.email.archive import SequencePrefix, sanitize_filename, split_sequence_prefix
from src.email.manifest import Manifest
from src.email.reader import ReadError, read_facts
from src.email.settings import email_part
from src.email.stage import Reader
from src.roots import resolve_roots
from src.scanner import exclude_keys

logger = logging.getLogger(__name__)

# The pre-fix rule, kept only to recognise the names it cut: the colon was optional.
_CUTTING_PREFIX = re.compile(r"^\s*(re|rv|fwd?)\s*:?\s*", re.IGNORECASE)

# How much of a name is compared: long enough to tell a cut name from a whole one,
# short enough to ignore where the archiver shortened a long name to fit MAX_PATH.
_COMPARE_CHARS = 24


@dataclass
class Cut:
    """One archived mail whose name lost letters, and the name it should have had."""

    path: str
    name: str
    should_be: str


@dataclass
class AuditResult:
    files: int = 0
    checked: int = 0
    not_checked_waiting: int = 0
    not_checked_unreadable: int = 0
    cut: list[Cut] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {"files": self.files, "checked": self.checked, "cut": len(self.cut),
                "not_checked_waiting": self.not_checked_waiting,
                "not_checked_unreadable": self.not_checked_unreadable}


def _head(text: str) -> str:
    return sanitize_filename(text).casefold()[:_COMPARE_CHARS]


def cut_name(filename: str, subject: str) -> Optional[str]:
    """The name ``filename`` should have had, when the old rule cut ``subject``'s first letters off it.

    ``subject`` is the mail's real subject as the fixed rule reads it. The old
    rule would have shortened it only if it still starts with a bare "re",
    "rv", "fw" or "fwd"; the name is cut only if it matches that shortened form
    and not the whole subject.
    """
    parsed = split_sequence_prefix(filename)
    shortened = _CUTTING_PREFIX.sub("", subject.strip())
    if parsed is None or shortened == subject.strip() or not shortened:
        return None
    stem = parsed.rest[:-4] if parsed.rest.lower().endswith(".msg") else parsed.rest
    seen = stem.casefold()[:_COMPARE_CHARS]
    if seen == _head(subject) or seen != _head(shortened):
        return None
    return SequencePrefix(parsed.date_prefix, parsed.number, sanitize_filename(subject) + ".msg").with_number(parsed.number)


def audit(manifests: list[Manifest], budget_bytes: int, read: Reader = read_facts) -> AuditResult:
    """Read every listed ``.msg`` the budget allows and list the cut names."""
    out = AuditResult()
    downloaded = 0
    for manifest in manifests:
        for f in manifest.files:
            out.files += 1
            if f.online and downloaded + f.size > budget_bytes:
                out.not_checked_waiting += 1
                continue
            try:
                facts = read(f.path, 0)
            except ReadError as exc:
                out.not_checked_unreadable += 1
                logger.info("ℹ️ subject audit: %s not read: %s", os.path.basename(f.path), exc)
                continue
            if f.online:
                downloaded += f.size
            out.checked += 1
            name = os.path.basename(f.path)
            should_be = cut_name(name, str(facts.get("subject") or ""))
            if should_be is not None:
                out.cut.append(Cut(f.path, name, should_be))
    return out


def run(scope: Optional[str] = None, wait_s: float = 0.0, read: Optional[Reader] = None) -> AuditResult:
    """The dry run over the email folders (or one of them); raises what ``indexer.run`` raises before reading."""
    cfg = config.load()
    roots = resolve_roots(cfg["roots"])
    part = email_part(cfg, roots)
    if part.stage_problem is not None or part.index_path is None:
        raise indexer.NotSetUp(part.stage_problem or "no email index")
    guard = indexer.lock()
    indexer.take_lock(guard, wait_s)
    try:
        lists = indexer.manifests(part, scope, roots, exclude_keys(cfg["exclude"]), fresh=True)
        return audit(lists, part.budget_bytes, read or read_facts)
    finally:
        guard.release()


def describe(result: AuditResult, limit: int) -> list[str]:
    """The lines the CLI prints locally: the counts, then up to ``limit`` names."""
    c = result.counts()
    lines = ["%d archived mail(s) listed, %d read, %d with a cut name, %d waiting for the download budget, %d unreadable"
             % (c["files"], c["checked"], c["cut"], c["not_checked_waiting"], c["not_checked_unreadable"])]
    lines += ["  %s  ->  %s" % (cut.name, cut.should_be) for cut in result.cut[:limit]]
    if len(result.cut) > limit:
        lines.append("  ... and %d more" % (len(result.cut) - limit))
    return lines
