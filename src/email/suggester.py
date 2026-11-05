"""The archive suggester: rank the folders an incoming mail most likely belongs in.

Each candidate folder gets one blended score:

    0.45 x its best FTS match (bm25 over subject, sender, recipients, preview;
           a folder scores by its single best mail, so volume does not rank)
  + 0.30 x thread continuity (the subject against the folder's newest subjects)
  + 0.25 x the subject against the folder's own name (helps a new, near-empty folder)

Then exact-subject priority: a folder already holding a mail with this very
(cleaned) subject ranks ahead of every folder that does not, because the same
thread is the strongest signal there is. The behaviour tests on invented
folders (``tests/test_email_suggester.py``) hold the weights to that order.
An empty index gives no suggestions, never a guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.email.archive_settings import get_suggestion_limits
from src.email.naming import clean_subject
from src.email.repository import EmailRepository, FolderSuggestion, open_index

logger = logging.getLogger(__name__)

W_FTS = 0.45
W_THREAD = 0.30
W_NAME = 0.25


@dataclass
class EmailData:
    """The incoming mail's fields the suggester ranks on."""

    subject: str = ""
    sender: str = ""
    recipients: str = ""
    date_sent: Optional[datetime] = None


@dataclass
class RankedSuggestion:
    folder_path: str
    display_name: str        # the last two path components
    score: float             # the blended score, 0 to 1
    match_count: int         # matching mails in this folder
    sample_subjects: list[str]
    exact_subject: bool = False


def folder_display_name(path: str) -> str:
    """The last two components of a path, joined with ``/`` on every platform."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:]) if parts else path


def _similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return fuzz.token_set_ratio(a, b) / 100.0


def _name_score(subject: str, folder_path: str) -> float:
    return _similarity(subject, Path(folder_path).name) if subject else 0.0


def _thread_score(subject: str, samples: list[str]) -> float:
    """The best match of the subject against the folder's stored subjects; Re:/Fwd: do not matter."""
    scores = [_similarity(subject, s) for s in samples if s]
    return max(scores) if subject and scores else 0.0


class SuggestionEngine:
    """Ranked folder suggestions; opens its own connection per call, so any thread may use it."""

    def __init__(self, cfg: dict[str, Any], max_suggestions: Optional[int] = None) -> None:
        self._cfg = cfg
        top, self._min_score = get_suggestion_limits(cfg)
        self._max = top if max_suggestions is None else max_suggestions

    def suggest(self, email: EmailData) -> list[RankedSuggestion]:
        """Up to ``max_suggestions`` folders, exact-subject folders first, then by blended score."""
        subject = clean_subject(email.subject)
        conn = open_index(self._cfg)
        repo = EmailRepository(conn)
        try:
            # Extra candidates for re-ranking; min_score applies after blending.
            raw = repo.suggest_folders(subject, email.sender, email.recipients, self._max * 3, 0.0)
            exact = repo.folders_with_subject(subject)
            missing = exact - {s.folder_path for s in raw}
            raw += [FolderSuggestion(f, 0.0, 0, repo.sample_subjects(f)) for f in sorted(missing)]
        finally:
            conn.close()
        if not raw:
            logger.info("ℹ️ no folder matches for subject %r", subject)
            return []
        ranked = [self._blend(subject, s, s.folder_path in exact) for s in raw]
        ranked = [r for r in ranked if r.exact_subject or r.score >= self._min_score]
        ranked.sort(key=lambda r: (r.exact_subject, r.score), reverse=True)
        unique: dict[str, RankedSuggestion] = {}
        for r in ranked:
            unique.setdefault(r.folder_path, r)
        top = list(unique.values())[:self._max]
        logger.info("ℹ️ suggestions for %r: %s", subject, [(r.display_name, round(r.score, 3)) for r in top])
        return top

    @staticmethod
    def _blend(subject: str, s: FolderSuggestion, exact: bool) -> RankedSuggestion:
        score = (W_FTS * s.score + W_THREAD * _thread_score(subject, s.sample_subjects)
                 + W_NAME * _name_score(subject, s.folder_path))
        return RankedSuggestion(s.folder_path, folder_display_name(s.folder_path), score, s.match_count,
                                s.sample_subjects, exact)
