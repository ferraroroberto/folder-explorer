"""Email search: the operator parser, the SQL it builds, ranking, tiers, facets and highlighting, on invented mail."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

import pytest

from src.email.query import compile_query, date_bounds, newer_than, size_bytes, tokenize
from src.email.search import MARK_END, MARK_START, EmailIndex, open_ro, split_address
from src.no_window import NO_WINDOW
from tests.email_fixture import build_email_index

TODAY = date(2026, 11, 5)


@pytest.fixture()
def index(tmp_path: Path) -> EmailIndex:
    return EmailIndex(open_ro(build_email_index(tmp_path / "emails.db", tmp_path / "Mail")))


@pytest.fixture()
def full_index(tmp_path: Path) -> EmailIndex:
    return EmailIndex(open_ro(build_email_index(tmp_path / "emails.db", tmp_path / "Mail", extra_fields=True)))


def subjects(result: dict) -> list[str]:
    return [h["subject"].replace(MARK_START, "").replace(MARK_END, "") for h in result["hits"]]


# The same cases app/webapp/static/query.js is written to: (box text, [(neg, key, value)]).
TOKEN_CASES = [
    ("budget", [(False, "", "budget")]),
    ('"draft plan" -old', [(False, "", "draft plan"), (True, "", "old")]),
    ("from:ana subject:\"q4 budget\"", [(False, "from", "ana"), (False, "subject", "q4 budget")]),
    ("-in:Archive is:flagged", [(True, "in", "Archive"), (False, "is", "flagged")]),
    ("http://example.org", [(False, "", "http://example.org")]),
    ("alpha OR beta", [(False, "", "alpha"), (False, "", "beta")]),
]


@pytest.mark.parametrize("text,expected", TOKEN_CASES)
def test_tokenizer_splits_words_phrases_and_operators(text: str, expected: list) -> None:
    assert [(t.neg, t.key, t.value) for t in tokenize(text)] == expected


def test_the_page_splits_the_box_the_same_way() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: query.js cannot be checked")
    module = (Path(__file__).resolve().parent.parent / "app" / "webapp" / "static" / "query.js").as_uri()
    script = ("const {tokenize} = await import(%s); console.log(JSON.stringify(%s.map((text) => tokenize(text)"
              ".map((t) => [t.neg, t.key || '', t.key ? t.val : t.word]))))") % (
                  json.dumps(module), json.dumps([text for text, _ in TOKEN_CASES]))
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [[list(t) for t in expected] for _, expected in TOKEN_CASES]


def test_only_free_words_are_left_to_relax() -> None:
    q = compile_query('budget "draft plan" kick OR off from:ana subject:q4 -old', {"sender"}, TODAY)
    assert q.loose == [["budget"], ["kick", "off"]]
    assert q.strict == ['"draft plan"', 'sender : "ana"*', 'subject : "q4"*']


def test_or_joins_two_words_into_one_alternative() -> None:
    toks = tokenize("alpha OR beta gamma")
    assert [t.or_prev for t in toks] == [False, True, False]
    q = compile_query("alpha OR beta gamma", set(), TODAY)
    assert q.match == '("alpha"* OR "beta"*) AND "gamma"*'


def test_value_parsers() -> None:
    assert size_bytes("5M") == 5 * 1024**2 and size_bytes("500k") == 500 * 1024 and size_bytes("big") is None
    assert date_bounds("2025") == (date(2025, 1, 1), date(2025, 12, 31))
    assert date_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert date_bounds("2025-13") is None
    assert newer_than("30d", TODAY) == date(2026, 10, 6)
    assert newer_than("6m", TODAY) == date(2026, 5, 5)
    assert split_address("Ana Lopez <ana@example.com>") == ("Ana Lopez", "ana@example.com")
    assert split_address("billing@kestrel.example") == ("billing@kestrel.example", "billing@kestrel.example")


def test_words_rank_subject_matches_first_and_mark_them(index: EmailIndex) -> None:
    result = index.search("budget", today=TODAY)
    assert result["total"] == 3
    # Two subjects say "budget"; the third mail only mentions it in the preview.
    assert subjects(result)[2] == "Kickoff agenda"
    assert MARK_START + "budget" + MARK_END in result["hits"][0]["subject"].lower()
    assert result["words"] == ["budget"]


def test_newest_sort_ignores_relevance(index: EmailIndex) -> None:
    result = index.search("budget", sort="newest", today=TODAY)
    assert subjects(result) == ["Kickoff agenda", "Q4 budget draft", "Re: budget notes"]


def test_operators_filter(index: EmailIndex) -> None:
    assert subjects(index.search("from:ana", today=TODAY)) and all(
        h["from_addr"] == "ana@example.com" for h in index.search("from:ana", today=TODAY)["hits"])
    assert subjects(index.search("subject:invoice", today=TODAY)) == ["Invoice 0042"]
    assert set(subjects(index.search("in:alpha", today=TODAY))) == {"Kickoff agenda", "Site visit photos"}
    assert subjects(index.search("is:flagged", today=TODAY)) == ["Q4 budget draft"]
    assert subjects(index.search("is:done", today=TODAY)) == ["Site visit photos"]
    assert subjects(index.search("after:2026 before:2026-02", today=TODAY)) == ["Kickoff agenda"]
    assert subjects(index.search("newer_than:1y", today=TODAY)) == ["Site visit photos", "Kickoff agenda"]
    assert subjects(index.search("id:<msg-5@example.invalid>", today=TODAY)) == ["Invoice 0042"]
    assert "Invoice 0042" not in subjects(index.search("-from:billing", today=TODAY))
    assert subjects(index.search('budget -"budget notes"', today=TODAY)) == ["Q4 budget draft", "Kickoff agenda"]


def test_fields_the_index_lacks_are_reported_not_faked(index: EmailIndex) -> None:
    result = index.search("budget has:attachment cc:mia", today=TODAY)
    assert result["total"] == 3
    assert any("has:" in n for n in result["notes"]) and any("Cc" in n for n in result["notes"])
    # to: still works through the older combined recipients column.
    assert "Invoice 0042" in subjects(index.search("to:ana", today=TODAY))


def test_the_new_fields_filter_when_present(full_index: EmailIndex) -> None:
    assert full_index.search("has:attachment", today=TODAY)["total"] == 4
    assert subjects(full_index.search("filename:invoice", today=TODAY)) == ["Invoice 0042"]
    assert subjects(full_index.search("larger:5M", today=TODAY)) == ["Site visit photos"]
    assert subjects(full_index.search("cc:mia", today=TODAY)) == ["Q4 budget draft"]
    assert set(subjects(full_index.search("with:mia", today=TODAY))) == {"Q4 budget draft", "Site visit photos"}
    assert full_index.search("", today=TODAY)["hits"][0]["attachments"] == 2


def test_facets_count_the_whole_result_set(index: EmailIndex) -> None:
    facets = index.search("budget", limit=1, today=TODAY)["facets"]
    assert facets["senders"][0] == {"name": "Ana Lopez", "value": "ana@example.com", "count": 2}
    assert {f["value"].replace("\\", "/").rsplit("/", 2)[-2] for f in facets["folders"]} == {"Clients", "Projects"}
    assert facets["years"] == [{"value": "2026", "count": 1}, {"value": "2025", "count": 2}]
    assert facets["flagged"] == 1


def test_one_mail_marks_its_preview(index: EmailIndex) -> None:
    first = index.search("timeline", today=TODAY)["hits"][0]
    mail = index.email(first["id"], "timeline", today=TODAY)
    assert MARK_START + "timeline" + MARK_END in mail["preview"]
    assert mail["from_name"] == "Ana Lopez" and mail["cc"] is None and mail["attachments"] is None
    with pytest.raises(KeyError):
        index.email(999)


def test_an_empty_box_lists_the_newest_mail(index: EmailIndex) -> None:
    result = index.search("", today=TODAY)
    assert result["total"] == 5 and subjects(result)[0] == "Site visit photos"


def listed(result: dict) -> list[tuple[str, str]]:
    return list(zip(subjects(result), [h["tier"] for h in result["hits"]]))


def test_one_wrong_word_still_finds_the_others_as_partial(index: EmailIndex) -> None:
    assert listed(index.search("kickoff agenda zeppelin", today=TODAY)) == [("Kickoff agenda", "partial")]
    result = index.search("budget zeppelin", today=TODAY)
    assert {h["tier"] for h in result["hits"]} == {"partial"} and result["total"] == 3
    assert result["hits"][0]["score"] == 0.5 and not result["truncated"]
    assert MARK_START in result["hits"][0]["subject"] + result["hits"][0]["snippet"]


def test_exact_hits_come_first_and_a_full_page_of_them_adds_nothing(index: EmailIndex) -> None:
    result = index.search("budget kickoff", today=TODAY)
    assert [h["tier"] for h in result["hits"]] == ["exact", "partial", "partial"]
    assert result["hits"][0]["score"] == 1.0 and subjects(result)[0] == "Kickoff agenda"
    assert listed(index.search("budget kickoff", limit=1, today=TODAY)) == [("Kickoff agenda", "exact")]
    first = index.search("budget kickoff", limit=2, today=TODAY)
    second = index.search("budget kickoff", limit=2, offset=2, today=TODAY)
    assert first["total"] == second["total"] == 3
    assert subjects(first) + subjects(second) == subjects(result) and second["facets"] is None


def test_a_typo_finds_the_mail_as_similar_and_marks_it(index: EmailIndex) -> None:
    result = index.search("kikcoff", today=TODAY)
    assert listed(result) == [("Kickoff agenda", "similar")]
    assert 0 < result["hits"][0]["score"] < 1 and MARK_START in result["hits"][0]["subject"]
    mail = index.email(result["hits"][0]["id"], "kikcoff", today=TODAY)
    assert MARK_START + "Kickoff" + MARK_END in mail["subject"] and "kickoff" in mail["words"]


def test_a_near_word_raises_a_partial_hit(index: EmailIndex) -> None:
    result = index.search("budget kikcoff", today=TODAY)
    assert listed(result)[0] == ("Kickoff agenda", "partial")
    assert result["hits"][0]["score"] > result["hits"][1]["score"] == 0.5


def test_the_newest_sort_keeps_the_tiers_apart(index: EmailIndex) -> None:
    result = index.search("budget kickoff", sort="newest", today=TODAY)
    assert listed(result) == [("Kickoff agenda", "exact"), ("Q4 budget draft", "partial"), ("Re: budget notes", "partial")]


@pytest.mark.parametrize("text,allowed", [
    ("from:ana kikcoff", {"Q4 budget draft", "Re: budget notes"}),
    ("-agenda kikcoff", {"Q4 budget draft", "Re: budget notes", "Site visit photos", "Invoice 0042"}),
    ("before:2025 kikcoff", {"Q4 budget draft", "Re: budget notes", "Invoice 0042"}),
    ("is:flagged kikcoff", {"Q4 budget draft"}),
])
def test_operators_and_exclusions_are_never_relaxed(index: EmailIndex, text: str, allowed: set[str]) -> None:
    # The one near spelling sits in "Kickoff agenda", which each filter leaves out: only filtered mails may stand in.
    result = index.search(text, today=TODAY)
    assert set(subjects(result)) <= allowed
    assert all(h["tier"] == "closest" for h in result["hits"])


def test_a_quoted_phrase_stays_exact_in_every_tier(index: EmailIndex) -> None:
    assert index.search('"notes budget"', today=TODAY)["total"] == 0
    assert index.search('"notes budget" kikcoff', today=TODAY)["total"] == 0
    assert set(subjects(index.search('"budget notes" agnd', today=TODAY))) <= {"Re: budget notes"}


def test_closest_fills_an_empty_result_only(index: EmailIndex) -> None:
    result = index.search("invce", today=TODAY)
    assert result["hits"] and {h["tier"] for h in result["hits"]} == {"closest"}
    assert subjects(result)[0] == "Invoice 0042" and MARK_START in result["hits"][0]["subject"]
    assert "closest" not in {h["tier"] for h in index.search("budget zeppelin", today=TODAY)["hits"]}


def test_an_or_pair_counts_as_one_word(index: EmailIndex) -> None:
    result = index.search("kickoff OR invoice zeppelin", today=TODAY)
    assert set(listed(result)) == {("Kickoff agenda", "partial"), ("Invoice 0042", "partial")}


def test_facets_count_the_listed_tiers(index: EmailIndex) -> None:
    facets = index.search("budget zeppelin", today=TODAY)["facets"]
    assert facets["years"] == [{"value": "2026", "count": 1}, {"value": "2025", "count": 2}]


def test_the_vocabulary_follows_another_connections_writes(tmp_path: Path) -> None:
    path = build_email_index(tmp_path / "emails.db", tmp_path / "Mail")
    index = EmailIndex(open_ro(path))
    assert "zeppelin" not in index.vocabulary().words
    writer = sqlite3.connect(str(path))
    writer.execute("INSERT INTO emails (file_path, folder_path, filename, subject, file_mtime) "
                   "VALUES ('x', 'y', 'z', 'Zeppelin tour', 0)")
    writer.commit()
    writer.close()
    assert listed(index.search("zepelin", today=TODAY)) == [("Zeppelin tour", "similar")]
