"""Settings shows the scan indexes' size without listing the index folder on every read."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src import store


def test_the_index_size_is_summed_again_only_on_a_change_or_once_it_aged_out(
        isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = store.index_dir()
    (folder / "a-1.sqlite3").write_bytes(b"x" * 10)
    sums: list[Path] = []
    real = store._sum_index_files

    def counted(path: Path) -> int:
        sums.append(path)
        return real(path)

    monkeypatch.setattr(store, "_sum_index_files", counted)
    now = [1000.0]

    def clock() -> float:
        return now[0]

    def stamp(ns: int) -> None:
        os.utime(folder, ns=(ns, ns))

    stamp(1_000_000_000)
    assert store.index_bytes(clock) == 10
    now[0] += 30
    assert store.index_bytes(clock) == 10
    assert len(sums) == 1

    # A new index changes the folder, so the next read sums at once.
    (folder / "a-2.sqlite3").write_bytes(b"y" * 5)
    stamp(2_000_000_000)
    assert store.index_bytes(clock) == 15
    assert len(sums) == 2

    # A file that grew inside an unchanged folder shows once the sum ages out.
    (folder / "a-2.sqlite3").write_bytes(b"y" * 7)
    stamp(2_000_000_000)
    assert store.index_bytes(clock) == 15
    now[0] += store.INDEX_BYTES_MAX_AGE_S
    assert store.index_bytes(clock) == 17
    assert len(sums) == 3
