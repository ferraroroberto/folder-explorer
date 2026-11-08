"""The writer helper refuses a path under a code folder and works anywhere else."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import atomic_write, runtime_files


@pytest.mark.parametrize("folder", runtime_files.CODE_FOLDERS)
def test_every_writer_refuses_a_path_under_a_code_folder(folder: str) -> None:
    inside = runtime_files.PROJECT_ROOT / folder / "never_written" / "out.db"
    with pytest.raises(runtime_files.CodeFolderError):
        runtime_files.make_dirs(inside.parent)
    with pytest.raises(runtime_files.CodeFolderError):
        runtime_files.open_file(inside, "a+b")
    with pytest.raises(runtime_files.CodeFolderError):
        runtime_files.connect(inside)
    with pytest.raises(runtime_files.CodeFolderError):
        runtime_files.connect_read_only(inside)
    assert not inside.parent.exists()


def test_a_dotdot_path_into_a_code_folder_is_refused() -> None:
    sneaky = runtime_files.PROJECT_ROOT / "docs" / ".." / "src" / "never_written"
    with pytest.raises(runtime_files.CodeFolderError):
        runtime_files.make_dirs(sneaky)


def test_a_path_outside_the_code_folders_is_written(tmp_path: Path) -> None:
    runtime_files.make_dirs(tmp_path / "a" / "b")
    with runtime_files.open_file(tmp_path / "a" / "b" / "x.lock", "a+b") as handle:
        handle.write(b"x")
    conn = runtime_files.connect(tmp_path / "a" / "index.db")
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    ro = runtime_files.connect_read_only(tmp_path / "a" / "index.db", immutable=True)
    assert ro.execute("SELECT v FROM t").fetchone()[0] == 1
    with pytest.raises(Exception, match="readonly"):
        ro.execute("INSERT INTO t VALUES (2)")
    ro.close()


def test_swap_in_renames_a_built_file_onto_its_destination(tmp_path: Path) -> None:
    built, dest = tmp_path / "x.part", tmp_path / "x"
    built.write_text("new", encoding="utf-8")
    dest.write_text("old", encoding="utf-8")
    atomic_write.swap_in(built, dest)
    assert dest.read_text(encoding="utf-8") == "new" and not built.exists()
