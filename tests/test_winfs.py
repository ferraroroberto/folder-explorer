"""The directory-record decoder and the cloud-state classifier, on synthetic records."""

from __future__ import annotations

import struct

from src import winfs


def _record(name: str, attrs: int, size: int, alloc: int, ea: int = 0, written: int = 0, last: bool = False) -> bytes:
    encoded = name.encode("utf-16-le")
    body_len = 68 + len(encoded)
    padded = (body_len + 7) // 8 * 8
    head = struct.pack("<IIqqqqqqIII", 0 if last else padded, 0, 0, 0, written, 0, size, alloc, attrs, len(encoded), ea)
    return (head + encoded).ljust(padded, b"\0")


def _page(*records: bytes) -> bytes:
    return b"".join(records)


def test_parse_records_reads_both_sizes_and_skips_dot_entries() -> None:
    ft = 116_444_736_000_000_000 + 1_700_000_000 * 10_000_000
    page = _page(
        _record(".", winfs.FILE_ATTRIBUTE_DIRECTORY, 0, 0),
        _record("..", winfs.FILE_ATTRIBUTE_DIRECTORY, 0, 0),
        _record("report.docx", 0x20 | winfs.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS, 580_946, 0, written=ft),
        _record("photos", winfs.FILE_ATTRIBUTE_DIRECTORY, 0, 0, last=True),
    )
    entries = list(winfs.parse_records(page))
    assert [e.name for e in entries] == ["report.docx", "photos"]
    doc = entries[0]
    assert (doc.size, doc.alloc, doc.is_dir, doc.mtime) == (580_946, 0, False, 1_700_000_000)
    assert winfs.is_online_only(doc.attrs)
    assert entries[1].is_dir


def test_reparse_tag_is_read_only_on_reparse_points() -> None:
    junction_tag = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT, a name surrogate
    cloud_tag = 0x9000001A  # IO_REPARSE_TAG_CLOUD, not a name surrogate
    page = _page(
        _record("link", winfs.FILE_ATTRIBUTE_DIRECTORY | winfs.FILE_ATTRIBUTE_REPARSE_POINT, 0, 0, ea=junction_tag),
        _record("synced", winfs.FILE_ATTRIBUTE_DIRECTORY | winfs.FILE_ATTRIBUTE_REPARSE_POINT, 0, 0, ea=cloud_tag),
        _record("plain", winfs.FILE_ATTRIBUTE_DIRECTORY, 0, 0, ea=junction_tag, last=True),
    )
    link, synced, plain = winfs.parse_records(page)
    assert winfs.is_link(link)
    assert not winfs.is_link(synced)
    assert plain.reparse_tag == 0 and not winfs.is_link(plain)


def test_cloud_state_classifies_online_pinned_and_local() -> None:
    assert winfs.cloud_state(winfs.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS) == "online"
    assert winfs.cloud_state(winfs.FILE_ATTRIBUTE_RECALL_ON_OPEN) == "online"
    assert winfs.cloud_state(winfs.FILE_ATTRIBUTE_OFFLINE) == "online"
    assert winfs.cloud_state(winfs.FILE_ATTRIBUTE_PINNED) == "pinned"
    assert winfs.cloud_state(winfs.FILE_ATTRIBUTE_UNPINNED) == "local"
    assert winfs.cloud_state(0x20) == "local"


def test_list_dir_reports_real_sizes(tmp_path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x" * 10_000)
    (tmp_path / "sub").mkdir()
    entries = {e.name: e for e in winfs.list_dir(str(tmp_path))}
    assert set(entries) == {"a.bin", "sub"}
    assert entries["a.bin"].size == 10_000 and entries["a.bin"].alloc >= 10_000
    assert entries["sub"].is_dir and not winfs.is_link(entries["sub"])


def test_extended_path_prefixes_once(tmp_path) -> None:
    p = winfs.extended_path(str(tmp_path))
    assert p.startswith("\\\\?\\") or not p.startswith("\\\\?\\\\\\?\\")
    assert winfs.extended_path(p) == p
