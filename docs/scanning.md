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
| `folders` | id, parent, name, depth, `lo`/`hi`, subtree totals (size, alloc, files, folders, online-only files and bytes, listing errors), and `hidden` |
| `files` | folder id, name, extension, size, alloc, attributes, modified time, and `hidden` |
| `folder_ext` | per folder and extension, the folder's *own* files: size, alloc, count, online-only count and bytes; a subtree's type totals sum a range of it |
| `folder_paths` | each folder's full path, case-folded, accents dropped, with `/` separators: what folder search matches every word against |
| `folder_words` | each word of the folder names (folded the same way) with the ids of the folders whose own name holds it, packed: where folder search finds a near word's folders |
| `folder_people` | folders holding subfolders named like email addresses, with how many: folder search's "folder of people" |

Folder search scans `folder_paths` with `instr`, one call per word, where a trigram FTS index would triple the index size for little gain. When the exact hits leave room in the results, a word of six letters or more also looks for its near words: its one-edit spellings are looked up in `folder_words` by key (a spelling or a word starting with it), and only the subtrees under those folders are read, never every path again. On 2.6 million folders `folder_words` adds about 3% to the index. An index written before `folder_words` stores paths case-folded only; it is searched for each word as typed and folded, with no near words, until the next scan replaces it. One written before `folder_paths` answers folder search with "rescan".

`hidden` is 0 for a visible item, 1 for one with the hidden or system attribute (both come with the listing, so nothing extra is read), and 2 for anything inside a hidden folder. Explore leaves out every nonzero row unless its Hidden items switch is on. The totals count every row either way. An index from before the column lists everything, and Explore says a rescan would tell hidden items apart.

The index is built as `<name>.part` and renamed into place only when the walk finishes. `state.sqlite3` records each scan (root, trigger, live counters while it runs, status, index file); a root's current index is its newest scan that finished `ok`. A failed or interrupted scan leaves the previous index in place, and the page flags the root. Replaced index files are deleted once no reader has them open.

## The email stage

Each scan ends with the email stage, `python -m app.cli email index` in its own process. Its manifest of `.msg` files comes from the scan's own index (the `files` rows with extension `msg` under each email folder), so nothing is listed twice; a scoped run (`--root <folder>`) lists only that folder with the same handle listing. The stage diffs the manifest against `emails.sqlite3` by path, size and modified time and reads only what is new, changed, or indexed before To, Cc, attachments and size were stored. The stage first decides every file's verdict without opening anything, so it knows how many files need a read (`to_index`) before the first read; while it reads it hands its counts so far (`done`, `downloaded`, `downloaded_bytes`, `failed`, `waiting`) to `src/store.py`, which keeps them on the running `email_runs` row at most once a second for the Scans tab. An online-only file is read (and so downloaded) without limit by default (`email.download_budget_mb` `null`); with a number set it is read only while the run's download budget lasts. A file that fails is recorded in `email_failures` with its size and modified time and skipped until either changes. Rows for files the manifest no longer lists are removed only when the folder's listing had no errors. Excluded folders are skipped by the walk, so they never reach the manifest; `email.exclude` folders are dropped from a manifest read from the scan index too, since the disk walk does not skip them. The stored `body_preview` is the readable text only: `src/email/preview.py` drops the `<url>` link targets, `[cid:…]`/`[image]` placeholders and URL-only lines Outlook writes into a plain-text body, before the preview is cut, on both the `.msg` read and the live mailbox read. Each run first cleans any stored preview that still carries that markup from its own text (no file is opened, so no download), and the search index follows.

`emails.sqlite3` keeps the layout the consumers read: the `emails` columns, `emails_fts(subject, sender, recipients, body_preview)` in that order, `recipients` still holding the To line, plus `to_addrs`, `cc_addrs`, `attachment_names`, `has_attachments` and `size`. It is a WAL database; the stage is its only writer and every reader opens it read-only.

The disk walk holds `scan.lock`, the email stage `email.lock`, both OS byte-range locks the system drops when the holder exits. A run waiting on the email lock past its `--wait` exits 2 ("busy"), never a plain failure. Each run writes a row to `email_runs` in `state.sqlite3` with its counts.

## One scan at a time

`scan.lock` in the runtime-data folder is an OS byte-range lock taken without waiting, by the app's Rescan, the in-app schedule, the tray and the CLI alike. The operating system releases it when the holder exits, so a crashed scan never leaves it stuck; the next scan marks the crashed one `interrupted`.
