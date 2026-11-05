"""An invented folder tree for tests: made-up names, deterministic sizes, no real data.

Files are written sparse-free with real bytes so the on-disk size is real too;
the whole tree is a few megabytes.
"""

from __future__ import annotations

from pathlib import Path

# (relative path, size in bytes); every name is invented.
FILES: tuple[tuple[str, int], ...] = (
    ("Photos/2024/harbour-trip/IMG_0001.jpg", 820_000),
    ("Photos/2024/harbour-trip/IMG_0002.jpg", 760_000),
    ("Photos/2024/harbour-trip/IMG_0003.heic", 410_000),
    ("Photos/2025/garden/IMG_0101.jpg", 905_000),
    ("Photos/2025/garden/clip-01.mov", 1_900_000),
    ("Videos/drone-footage/river-01.mp4", 2_400_000),
    ("Videos/drone-footage/river-02.mp4", 2_100_000),
    ("Documents/Invoices/invoice-0001.pdf", 120_000),
    ("Documents/Invoices/invoice-0002.pdf", 118_000),
    ("Documents/Reports/summary-q1.docx", 64_000),
    ("Documents/Reports/budget-model.xlsx", 48_000),
    ("Music/Album 01/track-01.mp3", 700_000),
    ("Music/Album 01/track-02.mp3", 690_000),
    ("Projects/garden-planner/src/main_01.py", 4_000),
    ("Projects/garden-planner/data/plants.json", 22_000),
    ("Archive/old-laptop.zip", 1_500_000),
    ("notes.txt", 2_000),
    ("README", 1_000),
)

TOTAL_BYTES = sum(size for _, size in FILES)


def build_tree(root: Path) -> Path:
    """Write the invented tree under ``root`` and return it."""
    for rel, size in FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([len(rel) % 251]) * size)
    (root / "Empty folder").mkdir(exist_ok=True)
    return root


# Invented hidden items add_hidden writes: a hidden folder holding one file,
# and one hidden file at the top. Neither is in FILES.
HIDDEN_FOLDER = "Hidden stash"
HIDDEN_FOLDER_FILE = "cache-01.bin"
HIDDEN_FILE = "thumbs-cache.db"
HIDDEN_BYTES = 3_000 + 1_000


def set_hidden(path: Path) -> None:
    """Give ``path`` the Windows hidden attribute, keeping its others."""
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    attrs = k32.GetFileAttributesW(str(path))
    if attrs == 0xFFFFFFFF or not k32.SetFileAttributesW(str(path), attrs | 0x2):
        raise ctypes.WinError(ctypes.get_last_error())


def add_hidden(root: Path) -> Path:
    """Write the invented hidden folder and file under ``root`` and mark both hidden (Windows only)."""
    folder = root / HIDDEN_FOLDER
    folder.mkdir(exist_ok=True)
    (folder / HIDDEN_FOLDER_FILE).write_bytes(b"\1" * 3_000)
    (root / HIDDEN_FILE).write_bytes(b"\2" * 1_000)
    set_hidden(folder)
    set_hidden(root / HIDDEN_FILE)
    return root
