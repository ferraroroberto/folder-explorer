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


def test_an_exact_subject_older_than_the_recent_samples_keeps_its_count_and_sample(cfg, archive_root):
    """Five newer subjects push the thread out of the folder's recent samples; it still leads, scored as the thread."""
    folders = {"Harbour": ["Berth renewal"] + ["Harbour note %d" % n for n in range(6)], "Marina": ["Berth prices"]}
    _seed_index(cfg, archive_root, folders)
    _date_by_order(cfg)

    first = _ranked(cfg, "Berth renewal")[0]

    assert _leaf(first) == "Harbour" and first.exact_subject
    assert first.match_count >= 1 and first.sample_subjects[0] == "Berth renewal"


def test_an_exact_subject_the_fts_pool_leaves_out_still_leads_with_its_count_and_sample(cfg, archive_root, monkeypatch):
    _seed_index(cfg, archive_root, {
        "Lighthouse": ["Lamp"] + ["Lighthouse log %d" % n for n in range(6)],
        **{"Shop %d" % n: ["Lamp oil order %d" % n] for n in range(3)},
    })
    _date_by_order(cfg)
    _fts_without(monkeypatch, "Lighthouse")

    first = _ranked(cfg, "Lamp")[0]

    assert _leaf(first) == "Lighthouse" and first.exact_subject
    assert first.match_count == 1 and first.sample_subjects[0] == "Lamp"


def test_the_exact_thread_scores_as_a_full_thread_match(cfg, archive_root):
    """An exact folder's thread share is the whole 30%, whatever its recent samples hold."""
    _seed_index(cfg, archive_root, {"Quay": ["Mooring fee"] + ["Quay bulletin %d" % n for n in range(6)]})
    _date_by_order(cfg)

    first = _ranked(cfg, "Mooring fee")[0]

    assert first.score >= 0.30 + 0.45 * 0.5


def test_a_matching_message_id_is_the_same_email_at_full_score(cfg, archive_root, monkeypatch):
    """The copy already in the index is offered first at 100%, even when FTS drops its folder."""
    _seed_index(cfg, archive_root, {
        "Shop 0": ["Lamp oil order"], "Shop 1": ["Lamp oil invoice"], "Filed": ["Unrelated words entirely"],
    })
    _fts_without(monkeypatch, "Filed")
    seeded_id = "seed-3@example.invalid"  # the third seeded mail, the one in "Filed"

    ranked = SuggestionEngine(cfg, max_suggestions=2).suggest(
        EmailData(subject="Lamp oil", sender="x@example.invalid", message_id="<" + seeded_id + ">"))

    assert _leaf(ranked[0]) == "Filed" and ranked[0].same_email and ranked[0].score == 1.0
    assert all(not s.same_email for s in ranked[1:])


def test_the_same_subject_with_another_message_id_is_not_the_same_email(cfg, archive_root):
    _seed_index(cfg, archive_root, {"Northwind": ["Site visit"], "Photos": ["Site visit photos"]})

    ranked = SuggestionEngine(cfg).suggest(
        EmailData(subject="Site visit", sender="sender@example.invalid", message_id="other@example.invalid"))

    assert _leaf(ranked[0]) == "Northwind" and ranked[0].exact_subject
    assert not any(s.same_email for s in ranked) and ranked[0].score < 1.0


def _date_by_order(cfg: dict) -> None:
    """Date each seeded mail by its seed order, so later seeds are the folder's newest subjects."""
    from src.email.repository import open_index

    conn = open_index(cfg)
    conn.execute("UPDATE emails SET date_sent = printf('2026-01-01T00:%02d:00', id)")
    conn.commit()
    conn.close()


def _fts_without(monkeypatch, leaf: str) -> None:
    """Make the FTS pool drop one folder, as truncation does when stronger matches crowd it out."""
    from src.email.repository import EmailRepository

    real = EmailRepository.suggest_folders

    def dropped(self, *args, **kwargs):
        return [s for s in real(self, *args, **kwargs) if _leaf(s) != leaf]

    monkeypatch.setattr(EmailRepository, "suggest_folders", dropped)
