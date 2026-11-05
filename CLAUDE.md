# Project Instructions

## This repository

folder-explorer: a public, read-only disk-space explorer. FastAPI + a static PWA on port 8453, a Windows tray, metadata-only scans of every fixed drive plus OneDrive into a per-scan SQLite index in the runtime-data folder. `README.md` carries setup, remote access, scans and the feature list; `docs/scanning.md` the scan method; `docs/layout.md` where code goes. Derived from project-scaffolding; its `CLAUDE.md` conventions apply.

**Hard rules for the scan (do not relax):**

- Metadata only. Never open, read or stat a file one by one; the directory-handle listing in `src/winfs.py` is the only reader.
- The one scoped exception, and only through `src/email/reader.py` (the only module that imports `extract_msg`; a test holds it), only for a `.msg` inside `email.roots`: the email stage reads new or changed mail (a child process, a per-run download budget, a failed file not retried until it changes), "Show all" reads one mail by row id in a child process with a timeout, and `email batch renumber` reads a mail's date when the index has none. Nothing else opens a scanned file.
- The archive verbs (`python -m app.cli email batch`, run by task-os and life-os) are the only writers of user files: they save, rename and delete only inside `email.roots` (`src/email/archive_paths.py` refuses anything else), make every COM call in their own process, never start a hidden Outlook, and keep the JSON contract `tests/archive/test_consumer_contract.py` replays. Never run them against a live mailbox in a test.
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
