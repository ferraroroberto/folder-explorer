# Layout

Shape `webapp-tray` (see `.fleet.toml`).

```
folder-explorer/
├── app/                        # entry points and the web layer
│   ├── cli/__main__.py         # `python -m app.cli scan|roots|email|folders`: scans and the email index with no app running
│   ├── cli/email_batch.py      # `email batch <verb>`: the archive verbs' flags, JSON document and exit codes
│   ├── cli/archive_gate.py     # `email batch` with the Outlook archive block off: one JSON refusal, exit 2, before any Outlook code loads
│   ├── cli/folders.py          # `folders search --json`: folder search for another app, one JSON document
│   ├── launcher.py             # `pythonw -m app.launcher <file>`: the Markdown "Open with" handler (names the file by its
│   │                           #   scanned folder, asks the app for its id, opens the /doc window)
│   ├── outlook.py              # `python -m app.outlook`: the Outlook bridge (the macro's entry, the Archive
│   │                           #   sheet's prepare and file, Find in mailbox), each verb in its own process
│   ├── tray/                   # `pythonw -m app.tray`: the tray that owns the web app
│   │   ├── tray.py             # menu (left click: Quick search or Open), adopt-or-spawn, watchdog, breadcrumbs
│   │   ├── respawn.py          # adopt-or-spawn and the watchdog's on_wedge, every spawn through the one RespawnGuard
│   │   ├── single_instance.py  # vendored: named-mutex single instance + start lock
│   │   ├── watchdog.py         # vendored: retry-with-backoff, health watchdog, breadcrumb log
│   │   └── snap_menu.py        # the tray's Screenshot submenu over a SnapService, and its redraw when config.json changes
│   └── webapp/
│       ├── __main__.py         # `python -m app.webapp`: bind from remote_access, cert renewal, serve.run
│       ├── server.py           # FastAPI app: routers, sign-in gate, gzip, cache-busted static mount
│       ├── bind.py             # remote_access + certificate -> host, scheme, state
│       ├── feature_gate.py     # `require(block)`: the router dependency that answers 404 while a feature block is switched off
│       ├── auth.py             # loopback-exempt bearer gate and /api/login, with the login lock-out
│       ├── manager.py          # the tray's webapp process: spawn, probe, stop
│       ├── build.py            # the process's build identity (git SHA, start time, asset hash)
│       ├── state.py            # the open-index pool, its housekeeping, and the folder search the route, the sheet and the window share
│       ├── serve.py            # vendored HTTP/2 launch module (hypercorn, uvicorn fallback)
│       ├── routers/            # browse (tree, folder, top, percentile, types, summary), docs (the document viewer), search, emails, scans, settings,
│       │                       #   archive (the Archive sheet and the macro's hand-off), search_v1 (the
│       │                       #   versioned search and flagged-mail API other apps call, docs/service-api.md), archive_v1 (the archive
│       │                       #   verbs over HTTP, each run as the CLI's child), explorer_v1 (the front File Explorer folder, for a caller on this PC), shortlist (the cleanup shortlist: files marked in Largest, in state.sqlite3)
│       └── static/             # the PWA: index.html, one ES module per view, app.css, tokens.css, icons;
│                           #   state.js also holds the page's view of the feature blocks (`isOn`, and `data-features-off` on `<html>`, which app.css reads to hide a block's `[data-feature]` parts);
                          #   parts/icon-sprite.html is the inline icon sprite and parts/settings-dialogs.html the Settings page and its setting sheet, each spliced into index.html where its include line stands;
│                           #   body-state.js is the Search preview's "Show all" load state (docs/body-state.md);
│                           #   doc.html + doc-page.js the /doc window, doc-view.js the viewer body it shares with doc-sheet.js (an HTML page in a sandboxed frame)
│                           #   doc-edit.js the editor both share (doc-conflict.js its conflict screen, doc-confirm.js its confirm
│                           #   sheets, md-source.js its DOM-free text helpers); doc-answer.js names a failed API answer for both;
│                           #   doc-drop.js takes a Markdown or HTML file dropped on the window (drop-files.js its DOM-free request builders,
│                           #   doc-handoff.js hands a rendered one, and its file handle, to the /doc window it opens in)
│           ├── dochost/        # doc-host.js: the View/Edit lifecycle the sheet and the /doc window share (mode, editor, a dropped file's handle, the race guard); a test holds it to one copy
│           ├── localdoc/       # local-doc.js: a dropped file no scan covers, read and saved through the browser's file handle (the only page-side writer of a
│           │                   #   user file); local-text.js its DOM-free text shape: byte-order mark, line ends, version
│           ├── filters/        # filter-trigger.js: the Filters & sort button's count badge and accessible name, shared by the Explore and Search sheets
│           └── _vendored/      # fleet UI components, byte-for-byte from project-scaffolding
├── src/                        # logic, never imports app/
│   ├── winfs.py                # metadata-only directory listing and the cloud-state classifier
│   ├── folder_search.py        # folder search over every root's index: the tiers from the words' subtrees, folders of people
│   ├── search_tiers.py         # the exact / partial / similar / closest tiers folder and email search rank by, and word similarity
│   ├── folder_match.py         # how a search word matches: accent folding, one-edit spellings, near-match score
│   ├── search_api.py           # the v1 search contract: its states, hit shapes and the page link
│   ├── archive_api.py          # the v1 archive contract: outcomes, acknowledged vs confirmed, the envelope
│   ├── this_pc.py              # is a client address one of this machine's own addresses (the PC through its tailnet name)
│   ├── stage_timer.py          # per-stage millisecond breadcrumbs (the macro's entry, the ranking) and the process's age
│   ├── archive_window/         # the macro's Archive window (Tk), shown by default or when no page is open on this PC: model (what each
│   │                           #   control shows and does, no Tk), finder (Find a folder: Quick search's folder search, browsing,
│   │                           #   which folders can take the mail; no Tk), theme (the app's tokens.css), work_area (the monitor's work area), view (the widgets; keeps the window and its buttons inside the work area)
│   ├── search_window/          # the tray's Quick search window (Tk): model (rows and states, no Tk), client (the web app's
│   │                           #   local API, its only source), view (the widgets, the archive window's theme)
│   ├── macro_home.py           # the macro sheet's setup: the primary checkout and the user's FOLDER_EXPLORER_HOME (read-only)
│   ├── docs/                   # the document viewer: reader (the only reader of a document's bytes, with the size,
│   │                           #   link and cloud-only checks), locate (a relative link fenced to its root, then its
│   │                           #   id in the index), render (markdown-it + Pygments to HTML and an outline; no disk), html_frame (an HTML page's
│   │                           #   sandboxed srcdoc: the files beside it inlined, links by id; no disk)
│   │                           #   and writer (the editor's only writer: the opened file only, after its version check);
│   │                           #   launch (the launcher's path to root, scan, folder id and name, read from the index);
│   │                           #   listing (Search's Documents mode: every indexed Markdown and HTML file across the roots, and the match of a dropped file by name, size and time, from the index; no disk),
│   │                           #   dropmatch (the file a dropped one is when the scan lacks it exactly: edited or new, found by its SHA-256 through the reader, one time and folder budget)
│   ├── snap/                   # the tray's region screenshot (docs/snap.md): core (geometry, hotkey and filename rules, no Tk), settings (the snap block of
│   │                           #   config.json), hotkey (RegisterHotKey thread, DPI context), selection (the click-click and press-drag-release state machine, no Tk), overlay (the Tk overlay, loupe and save), clipboard, folder (show the save folder in File Explorer: the tray menu and Settings), service (the Tk thread the tray starts)
│   ├── desktop.py              # PC-only: open a mail in Outlook, a folder in File Explorer, a /doc app window
│   ├── file_assoc.py           # PC-only: the per-user Markdown "Open with" registration (HKCU only) and its state
│   ├── window_front.py         # PC-only: wait for the opened Explorer window, bring it to the front, log the timings
│   ├── explorer_window.py      # PC-only: the folder of the foremost open File Explorer window's shown tab (the Archive sheet and window offer it)
│   ├── email/                  # the email part: settings, query parser, search, relaxed (the tiers below an exact
│   │   │                       #   match, docs/email-search.md), sanitize, body, schema (emails.sqlite3), manifest, stage, indexer (lock + history),
│   │   │                       #   and the archive: naming, archive (the .msg writer), archive_paths
│   │   │                       #   (the email-folder guard), archive_settings, mailboxes, repository,
│   │   │                       #   suggester, renumber, archive_sheet (the Archive sheet's suggest and file)
│   │   ├── subject_audit.py    # `email subjects`: the read-only dry run for names cut by the old reply-prefix rule (`fix-cut-names` renames from it)
│   │   ├── batch/              # the archive verbs: verbs (plan, apply, mailboxes, renumber), fix_cut_names, revert,
│   │   │                       #   dismiss, draft, send, find, sync, move, common (the envelope)
│   │   └── reader.py           # the only module that opens a file (a .msg; its callers: scanning.md, "What opens a file")
│   ├── outlook/                # every COM call: client, drafts, sending (the one Send), syncing, stores, process,
│   │                           #   bridge (attach, the mail at hand, a mail by its ids, Display)
│   ├── scanner.py              # the walk and the index writer
│   ├── index.py                # every query over an index, and the open-index pool
│   ├── store.py                # scan history and current index per root (state.sqlite3)
│   ├── scan_runner.py          # one-at-a-time scan runs, the background worker and the schedule
│   ├── scan_lock.py            # the cross-process scan lock and its free / held / unreadable probe
│   ├── run_state.py            # the run state: one derive function over a run row and its lock (docs/run-state.md)
│   ├── roots.py                # default roots (fixed drives + OneDrive), deduplicated
│   ├── config.py               # config.json with defaults and validation
│   ├── installer.py            # vendored: what `setup.bat` runs (docs/install.md); the app's own inputs are the `[install]` table of .fleet.toml
│   ├── install/                # the entries that table declares, standard library only: startup (the opt-in Startup shortcut to tray.bat),
│   │                           #   probes (sign-in token and password, app answering: the checklist's app-specific rows)
│   ├── ps_script.py            # starts a repo PowerShell script under any execution policy (AllSigned included) without weakening it
│   ├── features.py             # the feature blocks (email, archive, documents, screenshot): their keys, which are on, and whether each can run on this machine
│   ├── atomic_write.py         # the one atomic writer: text (config.json, .env) and bytes (a saved Markdown file); unique temp, cleanup on failure; swap_in renames a built file
│   ├── runtime_files.py        # mkdir, lock-file open and SQLite connects for a path a caller names; raises on a path under a code folder
│   ├── secrets_env.py          # the sign-in secrets in .env
│   ├── secret_compare.py       # the one constant-time secret comparison
│   ├── static_versioning.py    # the fleet hash stamped on every asset URL; splices a page's `<!--include x.html-->` fragments
│   └── (vendored)              # build_info, compression, login_alert, login_lockout, no_window, notify/, password, pooled_http, runtime_data, shell_etag, subject_text, usage_log
├── tests/                      # unit tests on invented fixture trees; tests/e2e is the browser suite (one browser context per projection shared by every case, reset between cases, so the run
│                               #   leaves few loopback sockets in TIME_WAIT; `_port_pool.py` heads a failure on `ERR_ADDRESS_IN_USE` with PORT POOL EXHAUSTED),
│                               #   tests/archive the archive verbs over a fake Outlook (and the consumer contract),
│                               #   tests/tray the tray's respawn path over a fake clock and process table,
│                               #   tests/install the installer on temp checkouts, the standalone restart, and the clean-pull and AllSigned checks,
│                               #   tests/scan what a scan logs: the progress heartbeat over a fake clock, each root's result as it ends,
│                               #   tests/harness the e2e harness's own checks (the port-pool failure naming),
│                               #   tests/search folder search: matching, accents and near words, the tiers and their timing on a large synthetic index,
│                               #   tests/e2e/_surface.py (vendored) is the `surface(page, id)` hook: a no-op in the gate, and under the design review's capture a test that calls it records that open surface in light and dark,
│                               #   tests/js the front-end state tables under Node (run by tests/test_body_state_js.py)
├── scripts/                    # the gate, the eleven vendored ratchets (the UX surface one with its map generator), this
│                               #   repo's own readme_index_ratchet.py (README feature-bullet word count), gen_icons,
│                               #   gen_tailscale_cert, gen_token, standalone_restart (tray.bat --restart on a PC without the shared
│                               #   tray helper, and the owned ports tray.bat reads from the config), disposable_webapp (the throwaway
│                               #   scanned instance on a free port that the e2e suite and the design review's synthetic run both boot) and
│                               #   design_review_synthetic (prints that instance's URL for `/design-review --synthetic` and `measure --e2e`)
├── assets/                     # tray icon and Stream Deck tile from scripts/gen_icons.py, and ArchiveMacro.bas
│                               #   (the Outlook macro template, no paths)
└── docs/                       # this file, install.md (fresh PC, update, migrate, uninstall), setup-and-migration.md (set up on another machine, what to copy, the feature blocks), scanning.md, service-api.md (the v1 search and archive API), folder-search.md, email-search.md, desktop-open.md, ux-surfaces.yaml (the UX surface map, the one source) with the ux-surface-map.md page generated from it, and design/ (the search study and the app mock-up, invented data)
```

## Where new code goes

- Anything that reads the disk, the index or the scan history goes in `src/`, testable without FastAPI. `src/` never imports `app/`.
- A route is a thin wrapper in `app/webapp/routers/` over a `src/` call.
- A front-end view is its own module in `app/webapp/static/`, imported by `main.js` with a relative path (the cache-busting stamp only rewrites relative imports).
- A shared UI component comes from project-scaffolding's `_vendored/`, copied verbatim and declared in `.fleet.toml` `[vendored]`; never edit a vendored file here.
- A test goes in `tests/` on an invented tree (`tests/fixture_tree.py`), never real paths or names.

The structure ratchet holds files to 1,000 lines, functions to 100, directories to 40 modules and the root folders to the shape.
