# Project Instructions

## This repository

folder-explorer: a public disk-space explorer, read-only apart from mail filing and the document editor. FastAPI + a static PWA on port 8453, a Windows tray, metadata-only scans of every fixed drive plus OneDrive into a per-scan SQLite index in the runtime-data folder. `README.md` carries setup, remote access, scans and the feature list; `docs/scanning.md` the scan method; `docs/layout.md` where code goes. Derived from project-scaffolding; its `CLAUDE.md` conventions apply.

**Hard rules for the scan (do not relax):**

- Metadata only. Never open, read or stat a file one by one; the directory-handle listing in `src/winfs.py` is the only reader, apart from the two scoped exceptions below (mail and the document viewer).
- The one scoped exception, and only through `src/email/reader.py` (the only module that imports `extract_msg`; a test holds it), only for a `.msg` inside `email.roots`: the email stage reads new or changed mail (a child process, a per-run download budget, a failed file not retried until it changes), "Show all" reads one mail by row id in a child process with a timeout, `email batch renumber` reads a mail's date, or its attachment names when mails share a number, only when the index has none, and `email subjects` (a dry run that renames nothing) and `email batch fix-cut-names` (the rename from it, with an undo log) read each mail's subject under the stage's download budget and lock. Mail is opened by nothing else; the document viewer below is the only other reader.
- The archive verbs (`python -m app.cli email batch`, run by task-os and life-os) and the Outlook bridge's `file` (`python -m app.outlook`, from the Archive sheet and the macro) are the only writers of mail files: they save, rename and delete only inside `email.roots` (`src/email/archive_paths.py` refuses anything else), make every COM call in their own process, never start a hidden Outlook (the bridge never starts one at all), and the verbs keep the JSON contract `tests/archive/test_consumer_contract.py` replays. The v1 archive API (`/api/v1/archive/*`, `app/webapp/routers/archive_v1.py`) is those same verbs run as the CLI's own child process for a caller on this PC, never an in-server Outlook call; its tests fake the child. Never run either against a live mailbox in a test; the e2e instance runs with `FOLDER_EXPLORER_OUTLOOK=off`.
- The document viewer reads only through `src/docs/reader.py` (a test holds it): a `.md`/`.markdown`/`.html`/`.htm` up to 2 MB, the images beside it up to 10 MB, and for an HTML page the `.css`/`.js` beside it up to 2 MB each (never downloaded), looked up by root, scan id and item id, checked against a fresh listing of its folder before any byte is read. A cloud-only file is refused ("Only in OneDrive") and opened without recall; **Download and open** is the one hydrating path, one file per tap. Relative links and images resolve on the server and never leave their root. The render reads nothing from disk. An HTML page draws only in a frame sandboxed without `allow-same-origin`, and the sign-in gate refuses every API request from an opaque origin (`Origin: null`, or a cross-site no-cors fetch), loopback included (a test holds both). A document dropped on the window is matched to its indexed copy by name, size and modified time (`POST /api/doc/dropped/match`, index only); for a caller on this PC with the page's SHA-256 of the bytes it is otherwise found by reading, through the reader only, the indexed files of that name and a fresh listing of scanned folders (`src/docs/dropmatch.py`, one budget of 4 s and 3,000 folders, a cloud-only candidate never read, exactly one equal file opens, a test holds it); else it is rendered read-only from the text the page read (`POST /api/doc/dropped/render`, no disk); the page never sends a path. Tests use fixture files in a temp root and fake the listing and open seams, never a real placeholder.
- The document editor writes only through `src/docs/writer.py` (a test holds it), the only writer of a user file outside `email.roots`: the one Markdown or HTML file it opened, looked up by id, after the version check that refuses any change on disk since it opened; only the confirmed **Overwrite theirs…** replaces a newer copy, and backs it up first. Scope, caps and the round trip: docs/scanning.md "What opens a file". Tests save fixture files in a temp root; the e2e changes only the fixture's `journal.md` and `sketch.htm`.
- The tray's region screenshot (`src/snap/`, docs/snap.md) reads screen pixels, never a file, and its one write is a new JPEG named by `unique_path` in the configured `snap.folder` (`src/snap/overlay.py`, the only module that writes it, held by a test): nothing is overwritten, moved or deleted, and `make_dirs` refuses a folder under the code. Only `app/tray/tray.py` builds it, so the unit tests and the e2e instance never start a hotkey; its overlay is checked by hand on a real desktop.
- Explore's open actions (`POST /api/open`) hand a path to Windows and read nothing: File Explorer for a folder, or the shell's `open` or `edit` verb, or File Explorer with the file selected. They run only for a caller on this PC (checked on the server), look the item up by root, scan id and item id (never a client path), and launch only through `src/desktop.py`, the one module that may (a test holds it). Tests launch nothing: the unit tests replace its launch seams, and the e2e instance runs with `FOLDER_EXPLORER_DESKTOP=off` and fakes the route.
- The Markdown "Open with" launcher (`app/launcher.py`) never sends a path: it names the file by root, scan id, scanned folder id and name (`src/docs/launch.py`, the index read in its own process), and `POST /api/doc/find` answers this PC only, finding a file newer than the scan in one listing of that folder. Its window opens through `src/desktop.py`. The association behind it is written only by `src/file_assoc.py`, under `HKCU\Software\Classes` only (never HKLM, never admin), removed exactly by the off switch, and never touched by a test (a fake registry) or the e2e instance (`FOLDER_EXPLORER_DESKTOP=off`).
- Never hydrate a OneDrive placeholder: no call that reads file data, no `GetCompressedFileSizeW` per file, no thumbnail or content sniffing.
- Junctions, symlinks and mount points are counted, never followed.
- Scan output lives in the runtime-data folder (`src/runtime_data.py`), never in the repo.
- Public repo: tests and docs use invented names only. No real paths, file names or screenshots in the repo, issues or PRs.

## Run / restart

- **Restart recipe:** `tray.bat --restart` (the shared `tray_lifecycle.ps1`: kills this repo's tray subtree, reclaims port 8453 by command line, starts the tray, and checks the served `git_sha` against `HEAD`). Run it fire-and-forget through a real Windows shell, then poll `GET http://127.0.0.1:8453/api/version` (or `https://` with remote access on) until `git_sha` matches `git rev-parse --short HEAD`, with a hard timeout. A `/healthz` 200 alone proves nothing about the build. Agents may run this recipe without asking; it touches only this app.
- **Console:** `start.bat` / `python -m app.webapp` (no tray).
- **Scans:** `python -m app.cli scan` (disk walk, then the email stage); the nightly app-launcher Job runs exactly that, so the in-app schedule stays `off` on this PC. `python -m app.cli email index [--root FOLDER]` runs the email stage alone under its own lock.
- The live signal is the Explore tree rendering the root's folders with sizes, not a health check.

**Not covered by the restart:** a scan the tray or the app-launcher Job started keeps running in its own process and finishes on the code it started with. The served build identity is in `/api/version` and the page footer.

## Verification gate

```
powershell -File scripts/verify-before-ship.ps1
```

Byte-compile, the unit tests, the e2e suite (`tests/e2e`, Chromium desktop + WebKit iPhone, disposable instance on a free port; set `FOLDER_EXPLORER_E2E_AUTOBOOT=1` to run it alone), the runtime-write probe, and the ten ratchets (structure, instruction, doc refs, dead code, static, docs, comment, conventions, runtime write, usage track), each vendored byte-for-byte with an empty baseline. Exit 3 from a ratchet is "not confirmed", a failure. Never raise a baseline to get green.

## CI expectations

No `.github/workflows/`: the local gate is the contract. Report CI as **unknown**, never green.

## UX surface

- design spec applies: yes
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:
  - /          (Search, Explore, Largest, Types and Scans tabs, the bottom nav, the Settings sheet)

Each tab leads with its own vendored `home-head` (tab title + context line + theme toggle + Settings gear); the shared toolbar moves into the active tab after its header and is absent on Search and Scans. Search and Explore are declared wide views (`.fleet.toml` `[design] wide_views`). Tokens in `app/webapp/static/tokens.css` follow `~/.claude/design.md` + `design.dark.md`; `/design-sync` reports drift. Every control carries a constant `data-track`, every `<dialog>` a `data-track-view`.

## Vendored components

`.fleet.toml` `[vendored]` lists every file copied from project-scaffolding with its scaffold commit. Re-vendor with `/propagate-vendored`; never edit a vendored copy here. `scripts/gen_tailscale_cert.py` is copy-to-adapt, not vendored. App icons come from the scaffold's `brand/folder-tree.svg` via `scripts/gen_icons.py` (needs a project-scaffolding checkout beside this repo); the outputs are committed.
