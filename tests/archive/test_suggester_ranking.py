"""The archive suggester's ranking, held by behaviour on invented folders.

Two rules decide the order, and these tests are what set the weights:

- exact-subject priority: a folder already holding this very thread ranks
  first, whatever the rest of the archive says;
- volume-neutral ranking: each folder scores by its single best match, so a
  folder with many loosely related mails never outranks the one folder that
  matches well.

email-archiver's legacy suggester sums its match scores per folder instead,
so there a big folder wins by volume. On a hide-one-mail run over a real
archive, the best-mail score found the right folder first about 72% of the
time against 51% for the sum, so this app keeps it, and
``test_the_ranking_and_its_percentages_are_pinned`` fails on any drift.
"""

from __future__ import annotations

from src.email.suggester import EmailData, SuggestionEngine
from tests.archive.batch_support import _seed_index, archive_root, cfg  # noqa: F401 - fixtures are used by name


def _ranked(cfg: dict, subject: str, top: int = 5) -> list:
    return SuggestionEngine(cfg, max_suggestions=top).suggest(EmailData(subject=subject, sender="sender@example.invalid"))


def _leaf(suggestion) -> str:
    return suggestion.folder_path.replace("\\", "/").rsplit("/", 1)[-1]


def test_one_strong_match_beats_a_folder_full_of_weak_ones(cfg, archive_root):
    _seed_index(cfg, archive_root, {
        "Kestrel": ["Kestrel invoice 0042 overdue"],
        "General": ["Invoice run %d for the month" % n for n in range(40)],
    })

    ranked = _ranked(cfg, "Kestrel invoice 0042 reminder")

    assert [_leaf(s) for s in ranked][:2] == ["Kestrel", "General"]


def test_the_folder_holding_the_thread_ranks_first(cfg, archive_root):
    _seed_index(cfg, archive_root, {
        "Status reports": ["Weekly status %d" % n for n in range(20)] + ["Status of the status report"],
        "Northwind": ["Status"],
    })

    ranked = _ranked(cfg, "Status")

    assert _leaf(ranked[0]) == "Northwind"
    assert ranked[0].exact_subject is True
    assert all(not s.exact_subject for s in ranked[1:])


def test_a_reply_prefix_still_finds_its_thread(cfg, archive_root):
    _seed_index(cfg, archive_root, {
        "Alpha": ["Kickoff agenda"],
        "Agendas": ["Kickoff agenda draft %d" % n for n in range(10)],
    })

    ranked = _ranked(cfg, "RE: Kickoff agenda")

    assert _leaf(ranked[0]) == "Alpha" and ranked[0].exact_subject


def test_the_exact_folder_survives_the_score_floor(cfg, archive_root):
    cfg["archive"]["min_score"] = 0.99
    _seed_index(cfg, archive_root, {"Northwind": ["Site visit"], "Photos": ["Site visit photos"] * 3})

    ranked = _ranked(cfg, "Site visit")

    assert [_leaf(s) for s in ranked] == ["Northwind"]


def test_without_an_exact_thread_the_blend_decides(cfg, archive_root):
    _seed_index(cfg, archive_root, {"Budget": ["Q4 budget plan"], "Travel": ["Hotel booking"]})

    ranked = _ranked(cfg, "Q4 budget plan revised")

    assert _leaf(ranked[0]) == "Budget" and not ranked[0].exact_subject
    assert ranked[0].score > (ranked[1].score if len(ranked) > 1 else 0.0)


def test_an_empty_index_suggests_nothing(cfg):
    assert _ranked(cfg, "Anything at all") == []


def test_the_ranking_and_its_percentages_are_pinned(cfg, archive_root):
    """A big folder of loose matches against the folder holding the thread: order and % fixed.

    Summing per folder, as the legacy suggester does, lifts the 30-mail folder's
    share and so its %, which this test refuses.
    """
    _seed_index(cfg, archive_root, {
        "Deliveries": ["Delivery slot %d confirmed" % n for n in range(30)],
        "Car purchase": ["Car delivery appointment tomorrow", "Car registration papers"],
        "Garden": ["Seed catalogue"],
        # Unrelated mail, so the words that do match carry weight in bm25.
        "Newsletters": ["Newsletter issue %d" % n for n in range(60)],
    })

    ranked = _ranked(cfg, "Car delivery appointment tomorrow at five")

    # The 30-mail folder stays a weak second: volume adds nothing (a sum gives it 66%).
    # Every invented mail shares one sender, so the unrelated folders still score a little.
    assert [(_leaf(s), s.exact_subject, round(s.score * 100)) for s in ranked] == [
        ("Car purchase", False, 85), ("Deliveries", False, 23), ("Newsletters", False, 14), ("Garden", False, 11)]
