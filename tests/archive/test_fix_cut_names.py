"""``fix-cut-names``: the rename of archived mails whose name lost its first letters, its undo and its guards.

Synthetic files and invented subjects only. The audit's own lock and folder listing are replaced by a
fixed manifest; the matching, the rename, the index and the undo log are the product's.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.cli import email_batch
from src.email import subject_audit
from src.email.batch import fix_cut_names as verb
from src.email.manifest import Manifest, MsgFile
from src.email.reader import ReadError
from tests.archive.batch_support import archive_root, cfg  # noqa: F401 - fixtures are used by name
from tests.archive.index_support import EmailRecord, EmailRepository, open_index

SUBJECTS = {
    "2026-11-02 - 017 - cibo de pago de Tesla.msg": "Recibo de pago de Tesla",
    "004 - visión anual.msg": "Revisión anual",
    "005 - Plan of action.msg": "Plan of action",
    "006 - unreadable.msg": "",
}


def _folder(root: Path, names: dict[str, str]) -> Path:
    folder = root / "Project Alpha"
    folder.mkdir()
    for name in names:
        (folder / name).write_bytes(b"x")
    return folder


def _use_manifest(monkeypatch: pytest.MonkeyPatch, folder: Path, names: dict[str, str]) -> None:
    def read(path: str, _preview: int) -> dict[str, Any]:
        name = Path(path).name
        if name not in names or name == "006 - unreadable.msg":
            raise ReadError("parse", "not a readable mail")
        return {"subject": names[name]}

    @contextlib.contextmanager
    def audited(scope=None, wait_s=0.0, reader=None, cfg=None):  # noqa: ANN001, ARG001
        files = [MsgFile(str(folder / n), 10, 0, False) for n in sorted(p.name for p in folder.iterdir())]
        yield subject_audit.audit([Manifest(str(folder), files)], 1 << 20, read)

    monkeypatch.setattr(subject_audit, "audited", audited)


def _seed(cfg: dict, folder: Path, names: dict[str, str]) -> None:
    conn = open_index(cfg)
    repo = EmailRepository(conn)
    for n, subject in names.items():
        repo.upsert_email(EmailRecord(file_path=str(folder / n), folder_path=str(folder), filename=n,
                                      subject=subject, message_id=n + "@example.invalid"))
    conn.commit()
    conn.close()


def _rows(cfg: dict) -> dict[str, str]:
    conn = open_index(cfg)
    try:
        return {r["file_path"]: r["subject"] for r in conn.execute("SELECT file_path, subject FROM emails")}
    finally:
        conn.close()


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


@pytest.fixture
def setup(cfg, archive_root, monkeypatch):  # noqa: F811
    folder = _folder(archive_root, SUBJECTS)
    _use_manifest(monkeypatch, folder, SUBJECTS)
    _seed(cfg, folder, SUBJECTS)
    return folder


def test_a_dry_run_lists_the_renames_and_changes_nothing(cfg, setup, tmp_path):  # noqa: F811
    before = _names(setup)

    doc = verb.fix_cut_names(cfg)

    assert doc["dry_run"] is True and doc["undo_log"] is None
    assert [i["outcome"] for i in doc["renames"]] == ["planned", "planned"]
    assert doc["counts"]["cut"] == 2 and doc["counts"]["renamed"] == 0
    assert _names(setup) == before
    assert not list((tmp_path / "data").glob("cut-names-undo-*"))


def test_apply_renames_keeps_date_and_sequence_and_follows_the_index(cfg, setup):  # noqa: F811
    doc = verb.fix_cut_names(cfg, apply=True)

    assert doc["counts"]["renamed"] == 2 and doc["counts"]["clashes"] == 0
    assert doc["counts"]["index_rows_updated"] == 2
    assert _names(setup) == ["004 - Revisión anual.msg", "005 - Plan of action.msg",
                             "006 - unreadable.msg", "2026-11-02 - 017 - Recibo de pago de Tesla.msg"]
    rows = _rows(cfg)
    assert str(setup / "2026-11-02 - 017 - Recibo de pago de Tesla.msg") in rows
    assert str(setup / "004 - Revisión anual.msg") in rows
    assert str(setup / "004 - visión anual.msg") not in rows
    assert rows[str(setup / "004 - Revisión anual.msg")] == "Revisión anual"


def test_the_undo_log_is_written_with_every_rename_and_revert_restores_them(cfg, setup):  # noqa: F811
    original = _names(setup)
    original_rows = set(_rows(cfg))
    doc = verb.fix_cut_names(cfg, apply=True)

    log = json.loads(Path(doc["undo_log"]).read_text(encoding="utf-8"))
    assert len(log["renames"]) == 2
    assert {Path(r["old"]).name for r in log["renames"]} == {"004 - visión anual.msg",
                                                             "2026-11-02 - 017 - cibo de pago de Tesla.msg"}

    undone = verb.revert_cut_names(cfg, doc["undo_log"])

    assert undone["counts"]["renamed"] == 2 and undone["revert"] is True
    assert _names(setup) == original
    assert set(_rows(cfg)) == original_rows


def test_the_undo_log_exists_before_the_first_rename(cfg, setup, monkeypatch):  # noqa: F811
    seen: list[bool] = []
    real = verb._move

    def spy(repo, roots, old, new):  # noqa: ANN001
        seen.append(bool(list(Path(verb.store.data_dir()).glob("cut-names-undo-*"))))
        return real(repo, roots, old, new)

    monkeypatch.setattr(verb, "_move", spy)

    verb.fix_cut_names(cfg, apply=True)

    assert seen == [True, True]


def test_a_taken_name_is_a_clash_and_nothing_is_overwritten(cfg, setup):  # noqa: F811
    taken = setup / "004 - Revisión anual.msg"
    taken.write_bytes(b"other mail")

    doc = verb.fix_cut_names(cfg, apply=True)

    assert doc["counts"]["renamed"] == 1 and doc["counts"]["clashes"] == 1
    assert taken.read_bytes() == b"other mail"
    assert (setup / "004 - visión anual.msg").exists()


def test_a_file_that_is_gone_is_skipped_not_failed(cfg, setup):  # noqa: F811
    (setup / "004 - visión anual.msg").unlink()

    doc = verb.fix_cut_names(cfg, apply=True)

    assert doc["counts"]["renamed"] == 1


def test_a_path_outside_the_email_folders_is_never_renamed(cfg, tmp_path, monkeypatch):  # noqa: F811
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    names = {"004 - visión anual.msg": "Revisión anual"}
    (outside / "004 - visión anual.msg").write_bytes(b"x")
    _use_manifest(monkeypatch, outside, names)

    doc = verb.fix_cut_names(cfg, apply=True)

    assert doc["counts"]["renamed"] == 0 and doc["counts"]["skipped"] == 1
    assert (outside / "004 - visión anual.msg").exists()


def test_revert_will_not_overwrite_and_skips_what_was_never_renamed(cfg, setup):  # noqa: F811
    doc = verb.fix_cut_names(cfg, apply=True)
    (setup / "004 - visión anual.msg").write_bytes(b"a newer file")
    (setup / "2026-11-02 - 017 - Recibo de pago de Tesla.msg").unlink()

    undone = verb.revert_cut_names(cfg, doc["undo_log"])

    assert undone["counts"]["clashes"] == 1 and undone["counts"]["skipped"] == 1
    assert (setup / "004 - visión anual.msg").read_bytes() == b"a newer file"
    assert (setup / "004 - Revisión anual.msg").exists()


def test_revert_refuses_a_log_that_names_a_path_outside_the_email_folders(cfg, setup, tmp_path):  # noqa: F811
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "new.msg").write_bytes(b"x")
    log = tmp_path / "log.json"
    log.write_text(json.dumps({"version": 1, "renames": [{"old": str(stray / "old.msg"),
                                                          "new": str(stray / "new.msg")}]}), encoding="utf-8")

    undone = verb.revert_cut_names(cfg, str(log))

    assert undone["counts"]["skipped"] == 1 and (stray / "new.msg").exists() and not (stray / "old.msg").exists()


@pytest.mark.parametrize("body", ["not json", json.dumps({"version": 9, "renames": []}), json.dumps([1])])
def test_an_unusable_undo_log_is_refused_before_anything_moves(cfg, tmp_path, body):  # noqa: F811
    log = tmp_path / "log.json"
    log.write_text(body, encoding="utf-8")

    with pytest.raises(verb.UndoLogError):
        verb.revert_cut_names(cfg, str(log))


def test_the_cli_prints_one_document_and_refuses_a_missing_log(cfg, setup, monkeypatch, capsys, tmp_path):  # noqa: F811
    monkeypatch.setattr(email_batch, "load_config", lambda: cfg)

    assert email_batch.main(["fix-cut-names"]) == email_batch.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["verb"] == "fix-cut-names" and doc["dry_run"] is True and doc["schema_version"] == 1

    assert email_batch.main(["fix-cut-names", "--revert", str(tmp_path / "none.json")]) == email_batch.EXIT_CANNOT_START
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_input"
