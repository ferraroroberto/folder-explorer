"""Accent- and typo-tolerant folder search over a scanned invented tree, each result with its score."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from src.folder_match import edits, fold, fuzzy, near_score
from src.folder_search import search
from src.index import Index, open_index
from src.roots import Root
from src.scanner import scan_to_index

# Invented folders: accented and plain names, a long word with near spellings, and one with a suffix.
FOLDERS = (
    "Informes/Comparación 2024",
    "Informes/Solucion final",
    "Proyectos/Soluciones",
    "Proyectos/Solucions",
    "Proyectos/Presupuestos 2025",
    "Proyectos/Kestrel/Notas",
)


def scanned(tmp_path: Path) -> tuple[Root, Index, Path]:
    base = tmp_path / "Invented drive"
    for rel in FOLDERS:
        (base / rel).mkdir(parents=True)
    index_path = tmp_path / "root.sqlite3"
    scan_to_index(str(base), index_path, root_name="Invented drive")
    return Root("inv", "Invented drive", str(base), "local"), Index(open_index(index_path)), index_path


def hits(root: Root, ix: Index, query: str) -> list[tuple[str, float]]:
    return [(r["name"], r["score"]) for r in search([(root, ix)], query, prune=True, skip=0)["results"]]


def test_fold_drops_case_and_accents() -> None:
    assert fold("Comparación ÅÉÎ") == "comparacion aei"
    assert fold("plain") == "plain"


def test_one_edit_spellings_and_who_gets_them() -> None:
    near = edits("budget")
    assert {"budgt", "bduget", "budgat", "budgest"} <= near and "budget" not in near
    assert fuzzy("budget") and not fuzzy("notes") and not fuzzy("q1-2025")
    assert near_score("budget") == 0.833


def test_a_query_without_accents_finds_the_accented_folder_and_the_reverse(tmp_path: Path) -> None:
    root, ix, _ = scanned(tmp_path)
    assert hits(root, ix, "comparacion") == [("Comparación 2024", 1.0)]
    assert hits(root, ix, "COMPARACIÓN 2024") == [("Comparación 2024", 1.0)]
    assert ("Solucion final", 1.0) in hits(root, ix, "solución")


def test_a_one_letter_typo_still_finds_the_folder_below_exact_hits(tmp_path: Path) -> None:
    root, ix, _ = scanned(tmp_path)
    found = hits(root, ix, "solucions")
    # The exact hit leads at 100%; the near ones follow, each below it, in score order.
    assert found[0] == ("Solucions", 1.0)
    assert {name for name, _ in found[1:]} == {"Soluciones", "Solucion final"}
    assert all(score < 1.0 for _, score in found[1:])
    assert [s for _, s in found] == sorted((s for _, s in found), reverse=True)
    # A typo plus a suffix: one edit from the start of "presupuestos".
    assert hits(root, ix, "presupesto") == [("Presupuestos 2025", 0.9)]
    # Every word must still match, the near one included.
    assert [n for n, _ in hits(root, ix, "presupesto 2025")] == ["Presupuestos 2025"]
    assert hits(root, ix, "presupesto kestrel") == []


def test_short_words_match_exactly_only(tmp_path: Path) -> None:
    root, ix, _ = scanned(tmp_path)
    assert hits(root, ix, "nota") == [("Notas", 1.0)]
    assert hits(root, ix, "notaz") == []


def test_an_index_from_before_accent_folding_still_answers(tmp_path: Path) -> None:
    root, ix, index_path = scanned(tmp_path)
    ix.conn.close()
    old = tmp_path / "old.sqlite3"
    shutil.copy(index_path, old)
    conn = sqlite3.connect(str(old))
    # What an older scan wrote: paths case-folded only, no word table.
    conn.execute("UPDATE folder_paths SET lpath = replace(lpath, 'comparacion', 'comparación')")
    conn.execute("DROP TABLE folder_words")
    conn.commit()
    conn.close()
    old_ix = Index(open_index(old))
    assert hits(root, old_ix, "comparación") == [("Comparación 2024", 1.0)]
    assert hits(root, old_ix, "solucions") == [("Solucions", 1.0)]
