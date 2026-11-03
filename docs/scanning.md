# Scanning: metadata only, never a download

How a scan reads every file's size without opening a file, why that keeps OneDrive placeholders online-only, and the index it writes.

## The listing

`src/winfs.py` opens each folder once, with `CreateFileW(FILE_LIST_DIRECTORY, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)`, and pages `GetFileInformationByHandleEx(FileFullDirectoryInfo)` in 64 KiB pages. Every record carries:

| Field | Becomes |
| --- | --- |
| `EndOfFile` | **Size**: the logical length, what the cloud stores and what a download would fetch |
| `AllocationSize` | **On disk**: the bytes allocated on this volume (0 for an online-only file) |
| `FileAttributes` | the cloud state and whether the entry is a folder or a reparse point |
| `EaSize` | the reparse tag, on a reparse point |
| `LastWriteTime` | the modified date |

No file is opened, read or stat-ed one by one: one handle per folder gives every figure. That is also about a hundred times faster than a `GetCompressedFileSizeW` call per file. Off Windows the same record comes from `os.scandir` + `lstat`, with `st_blocks` as the allocation.

## Cloud files

A file is **online-only** when any of `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`, `FILE_ATTRIBUTE_RECALL_ON_OPEN` or `FILE_ATTRIBUTE_OFFLINE` is set; the OneDrive client marks its placeholders with the first. `FILE_ATTRIBUTE_PINNED` means "always keep on this device". Opening a placeholder's *data* is what makes the sync client download it, and a scan never does. Listing a folder whose listing is not local yet may make the client fetch that listing, which is metadata, never file content.

Checked on a real online-only document before and after full walks of its tree: attributes `ARCHIVE | RECALL_ON_DATA_ACCESS` unchanged, `GetCompressedFileSizeW` 0 throughout.

## Links

A folder whose reparse tag is a name surrogate (a junction, a symlink, a mount point) is counted in `skipped_links` and never followed, so no bytes are counted twice and no walk loops. OneDrive's own cloud reparse tags are not name surrogates, so synced folders are walked normally.

## The index

One SQLite file per finished scan, `index/<root>-<scan id>.sqlite3` in the runtime-data folder. The walk is depth first, so folder ids are pre-order and each folder's subtree is the id range `[lo, hi]`; every "under this folder" question is a range scan.

| Table | Rows |
| --- | --- |
| `folders` | id, parent, name, depth, `lo`/`hi`, and subtree totals: size, alloc, files, folders, online-only files and bytes, listing errors |
| `files` | folder id, name, extension, size, alloc, attributes, modified time |
| `folder_ext` | per folder and extension, the folder's *own* files: size, alloc, count, online-only count and bytes; a subtree's type totals sum a range of it |

The index is built as `<name>.part` and renamed into place only when the walk finishes. `state.sqlite3` records each scan (root, trigger, live counters while it runs, status, index file); a root's current index is its newest scan that finished `ok`. A failed or interrupted scan leaves the previous index in place, and the page flags the root. Replaced index files are deleted once no reader has them open.

## One scan at a time

`scan.lock` in the runtime-data folder is an OS byte-range lock taken without waiting, by the app's Rescan, the in-app schedule, the tray and the CLI alike. The operating system releases it when the holder exits, so a crashed scan never leaves it stuck; the next scan marks the crashed one `interrupted`.
