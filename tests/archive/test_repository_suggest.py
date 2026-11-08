"""``EmailRepository.suggest_folders`` sizes its folder pool from the request."""

from __future__ import annotations

import pytest

from src.email.repository import FOLDER_POOL_CAP
from tests.archive.index_support import EmailRecord, EmailRepository, init_db

_FOLDERS = FOLDER_POOL_CAP + 5


@pytest.fixture
def repo(tmp_path):
    conn = init_db(str(tmp_path / "emails.db"))
    repo = EmailRepository(conn)
    for n in range(_FOLDERS):
        folder_path = f"folder-{n:02d}"
        repo.upsert_email(EmailRecord(
            file_path=f"{folder_path}/001 - seed.msg",
            folder_path=folder_path,
            filename="001 - seed.msg",
            subject="quarterly invoice",
            sender="sender@example.invalid",
            recipients="me@example.invalid",
            date_sent="2026-01-01T00:00:00",
            body_preview="quarterly invoice",
            file_mtime=float(n),
            message_id=f"seed-{n}@example.invalid",
        ))
    conn.commit()
    yield repo
    conn.close()


def _suggest(repo: EmailRepository, max_results: int) -> list:
    return repo.suggest_folders(
        subject="quarterly invoice",
        sender="sender@example.invalid",
        recipients="me@example.invalid",
        max_results=max_results,
        min_score=0.0,
    )


def test_pool_grows_past_the_floor_to_the_request(repo):
    assert len(_suggest(repo, _FOLDERS)) == _FOLDERS


def test_small_request_still_returns_only_what_was_asked(repo):
    assert len(_suggest(repo, 3)) == 3


def test_the_exact_subject_lookup_reads_the_case_blind_subject_index(repo):
    plan = " ".join(str(r[-1]) for r in repo._conn.execute(
        "EXPLAIN QUERY PLAN SELECT folder_path, COUNT(*) AS n FROM emails WHERE subject = ? COLLATE NOCASE GROUP BY folder_path",
        ("Quarterly Invoice",)))
    assert "idx_emails_subject_nocase" in plan
    assert repo.exact_subject_counts("Quarterly Invoice") == {f"folder-{n:02d}": 1 for n in range(_FOLDERS)}
