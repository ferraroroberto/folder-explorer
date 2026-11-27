"""A root that is itself a junction lists its target; a junction found below a root is never followed."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.docs import reader
from src.index import Index, open_index
from src.roots import resolve_roots
from src.scanner import scan_to_index

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")


def _junction(link: Path, target: Path) -> Path:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))
    return link


@pytest.fixture()
def linked(tmp_path: Path) -> Iterator[Path]:
    """A junction onto an invented folder: one note at the top, one below, and a junction inside."""
    target = tmp_path / "target"
    (target / "Alpha").mkdir(parents=True)
    (target / "note.md").write_bytes(b"# top")
    (target / "Alpha" / "plan.md").write_bytes(b"# below")
    inner = _junction(target / "Alpha again", target / "Alpha")
    link = _junction(tmp_path / "link", target)
    yield link
    os.rmdir(link)
    os.rmdir(inner)


def test_the_scan_lists_the_target_and_follows_no_junction_below_it(linked: Path, tmp_path: Path) -> None:
    counters = scan_to_index(str(linked), tmp_path / "j.sqlite3")
    assert (counters.files, counters.folders, counters.skipped_links, counters.errors) == (2, 2, 1, 0)
    ix = Index(open_index(tmp_path / "j.sqlite3"))
    # The index keeps the path as configured, not the target's.
    assert ix.root_path().rstrip("/").endswith("/link")


def test_a_file_at_the_top_of_a_linked_root_opens_from_a_fresh_listing(linked: Path) -> None:
    (root,) = resolve_roots([str(linked)])
    assert root.path == str(linked)
    assert reader.read_document(os.path.join(root.path, "note.md"), False) == b"# top"
    assert reader.find_document(root.path, "note.md") is not None
    # Naming the root does not let a junction below it be opened through.
    assert reader.find_document(os.path.join(root.path, "Alpha again"), "plan.md") is None


def test_a_junction_onto_nothing_fails_the_scan_with_a_reason(tmp_path: Path) -> None:
    gone = tmp_path / "gone"
    gone.mkdir()
    link = _junction(tmp_path / "link", gone)
    gone.rmdir()
    try:
        with pytest.raises(FileNotFoundError, match="cannot list the root"):
            scan_to_index(str(link), tmp_path / "d.sqlite3")
    finally:
        os.rmdir(link)


def test_a_junction_loop_as_a_root_fails_rather_than_listing_empty(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    second.mkdir()
    _junction(first, second)
    second.rmdir()
    _junction(second, first)
    try:
        with pytest.raises(FileNotFoundError, match="cannot list the root"):
            scan_to_index(str(first), tmp_path / "l.sqlite3")
    finally:
        os.rmdir(first)
        os.rmdir(second)
