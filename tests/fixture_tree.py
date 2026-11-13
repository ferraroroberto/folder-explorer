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


# A tiny valid 1x1 PNG, so a browser draws the invented diagram.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082"
)

# Invented Markdown notes add_docs writes: a guide that exercises the renderer
# and every link rule, a second page it links to, and one past the size cap.
DOCS_FOLDER = "Notes"
GUIDE_MD = """# Garden guide

Some *emphasis*, a `code chip` and a [second page](sub/watering.md#schedule).

## Beds

| Bed | Plant |
| --- | ----- |
| A1  | Basil |

- [x] Dig the bed
- [ ] Sow the seeds

```python
def water(bed: str) -> None:
    print("watering", bed)
```

![Bed plan](diagram.png)

## Out of reach

An [escape](../../elsewhere.md), an [unscanned page](missing.md), a [web link](https://example.invalid/page)
and ![escaped image](../../outside.png). Raw HTML stays text: <script>alert(1)</script>

A footnote[^1].

[^1]: The invented note.
"""
WATERING_MD = """# Watering

## Schedule

Back to the [guide](../guide.md).
"""
# The page the editor tests change; nothing else reads it, so a save never moves another test's text.
JOURNAL_MD = b"# Journal\n\nFirst entry.\n"
# One byte past the viewer's 2 MB cap.
BIG_MD_BYTES = 2 * 1024 * 1024 + 1
# The invented HTML pages sit in a folder of their own, so the Markdown notes' folder keeps its rows,
# and the page's image and link into the notes cross folders.
PAGES_FOLDER = "Pages"
# An invented HTML page: a stylesheet and a script beside it, an image and a link in the notes, every link
# rule, and a probe that records in its own body whether it could reach the app's page, cookies, storage or API.
PAGE_HTML = """<!doctype html>
<html><head><title>Invented page</title>
<base href="https://example.invalid/">
<link rel="stylesheet" href="page.css">
<script src="page.js"></script>
</head><body>
<h1 id="top">Invented page</h1>
<p class="styled">Styled by the stylesheet beside it.</p>
<img alt="Bed plan" src="../Notes/diagram.png">
<img alt="Escaped" src="../../outside.png">
<p><a href="../Notes/guide.md">The guide</a>, <a href="missing.html">a missing page</a>,
<a href="../../elsewhere.html">an escape</a>, <a href="https://example.invalid/x">the web</a>, <a href="#top">the top</a>.</p>
<script>
(async () => {
  const b = document.body;
  try { void parent.document.title; b.dataset.parent = 'reached'; } catch (e) { b.dataset.parent = 'blocked'; }
  let cookie = '';
  try { cookie = document.cookie; } catch (e) { cookie = ''; }
  b.dataset.cookie = cookie.includes('probe=') ? 'reached' : 'blocked';
  let stored = null;
  try { stored = localStorage.getItem('folder-explorer.probe'); } catch (e) { stored = null; }
  b.dataset.storage = stored ? 'reached' : 'blocked';
  try { await fetch('/api/doc/recent?probe=' + Date.now(), { mode: 'no-cors' }); b.dataset.quiet = 'sent'; } catch (e) { b.dataset.quiet = 'failed'; }
  try {
    const r = await fetch('/api/doc/render', { method: 'POST', body: new Blob(['{}']) });
    b.dataset.api = 'answered ' + r.status;
  } catch (e) { b.dataset.api = 'blocked'; }
})();
</script>
</body></html>
"""
PAGE_CSS = ".styled { color: rgb(0, 128, 0); }\n"
PAGE_JS = "document.documentElement.dataset.pageJs = 'ran';\n"
# The HTML page the editor e2e changes; nothing else reads it.
SKETCH_HTML = b"<!doctype html>\n<h1>Sketch</h1>\n<p>First draft.</p>\n"


def add_docs(root: Path) -> Path:
    """Write the invented notes under ``root``: a guide, a page it links to, a journal to edit, an image, an oversize file,
    and, in their own folder, an HTML page with its stylesheet and script plus an HTML sketch to edit."""
    folder = root / DOCS_FOLDER
    (folder / "sub").mkdir(parents=True, exist_ok=True)
    (folder / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (folder / "sub" / "watering.md").write_text(WATERING_MD, encoding="utf-8")
    (folder / "journal.md").write_bytes(JOURNAL_MD)
    (folder / "diagram.png").write_bytes(TINY_PNG)
    (folder / "big.md").write_bytes(b"#" * BIG_MD_BYTES)
    pages = root / PAGES_FOLDER
    pages.mkdir(exist_ok=True)
    (pages / "page.html").write_bytes(PAGE_HTML.encode("utf-8"))
    (pages / "page.css").write_bytes(PAGE_CSS.encode("utf-8"))
    (pages / "page.js").write_bytes(PAGE_JS.encode("utf-8"))
    (pages / "sketch.htm").write_bytes(SKETCH_HTML)
    return root
