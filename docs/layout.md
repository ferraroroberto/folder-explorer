# Layout

Shape `webapp-tray` (see `.fleet.toml`).

```
folder-explorer/
├── app/                        # entry points and the web layer
│   ├── cli/__main__.py         # `python -m app.cli scan|roots|email|folders`: scans and the email index with no app running
│   ├── cli/email_batch.py      # `email batch <verb>`: the archive verbs' flags, JSON document and exit codes
│   ├── cli/folders.py          # `folders search --json`: folder search for another app, one JSON document
│   ├── outlook.py              # `python -m app.outlook`: the Outlook bridge (the macro's entry, the Archive
│   │                           #   sheet's prepare and file, Find in mailbox), each verb in its own process
│   ├── tray/                   # `pythonw -m app.tray`: the tray that owns the web app
│   │   ├── tray.py             # menu, adopt-or-spawn, watchdog, breadcrumbs
│   │   ├── single_instance.py  # vendored: named-mutex single instance + start lock
│   │   └── watchdog.py         # vendored: retry-with-backoff, health watchdog, breadcrumb log
│   └── webapp/
│       ├── __main__.py         # `python -m app.webapp`: bind from remote_access, cert renewal, serve.run
│       ├── server.py           # FastAPI app: routers, sign-in gate, gzip, cache-busted static mount
│       ├── bind.py             # remote_access + certificate -> host, scheme, state
│       ├── auth.py             # loopback-exempt bearer gate and /api/login, with the login lock-out
│       ├── manager.py          # the tray's webapp process: spawn, probe, stop
│       ├── build.py            # the process's build identity (git SHA, start time, asset hash)
│       ├── state.py            # the open-index pool, its housekeeping and the email-folder search
│       ├── serve.py            # vendored HTTP/2 launch module (hypercorn, uvicorn fallback)
│       ├── routers/            # browse (tree, folder, top, percentile, types, summary), search, emails, scans, settings,
│       │                       #   archive (the Archive sheet and the macro's hand-off)
│       └── static/             # the PWA: index.html, one ES module per view, app.css, tokens.css, icons
│           └── _vendored/      # fleet UI components, byte-for-byte from project-scaffolding
├── src/                        # logic, never imports app/
│   ├── winfs.py                # metadata-only directory listing and the cloud-state classifier
│   ├── folder_search.py        # folder search over every root's index: every word in the path, folders of people
│   ├── folder_match.py         # how a search word matches: accent folding, one-edit spellings, near-match score
│   ├── this_pc.py              # is a client address one of this machine's own addresses (the PC through its tailnet name)
│   ├── archive_window/         # the macro's fallback Archive window (Tk) when no page is open on this PC: model (what each
│   │                           #   control shows and does, no Tk), theme (the app's tokens.css), view (the widgets)
│   ├── macro_home.py           # the macro sheet's setup: the primary checkout and the user's FOLDER_EXPLORER_HOME (read-only)
│   ├── desktop.py              # PC-only: open a mail in Outlook, a folder in File Explorer
│   ├── email/                  # the email part: settings, query parser, search, sanitize, body,
│   │   │                       #   schema (emails.sqlite3), manifest, stage, indexer (lock + history),
│   │   │                       #   and the archive: naming, archive (the .msg writer), archive_paths
│   │   │                       #   (the email-folder guard), archive_settings, mailboxes, repository,
│   │   │                       #   suggester, renumber, archive_sheet (the Archive sheet's suggest and file)
│   │   ├── subject_audit.py    # `email subjects`: the read-only dry run for names cut by the old reply-prefix rule (`fix-cut-names` renames from it)
│   │   ├── batch/              # the archive verbs: verbs (plan, apply, mailboxes, renumber), fix_cut_names, revert,
│   │   │                       #   dismiss, draft, send, find, sync, move, common (the envelope)
│   │   └── reader.py           # the only module that opens a file (a .msg, for the stage, Show all, renumber)
│   ├── outlook/                # every COM call: client, drafts, sending (the one Send), syncing, stores, process,
│   │                           #   bridge (attach, the mail at hand, a mail by its ids, Display)
│   ├── scanner.py              # the walk and the index writer
│   ├── index.py                # every query over an index, and the open-index pool
│   ├── store.py                # scan history and current index per root (state.sqlite3)
│   ├── scan_runner.py          # one-at-a-time scan runs, the background worker and the schedule
│   ├── scan_lock.py            # the cross-process scan lock
│   ├── roots.py                # default roots (fixed drives + OneDrive), deduplicated
│   ├── config.py               # config.json with defaults and validation
│   ├── atomic_write.py         # the one atomic text writer (config.json, .env): unique temp, cleanup on failure
│   ├── secrets_env.py          # the sign-in secrets in .env
│   ├── secret_compare.py       # the one constant-time secret comparison
│   ├── login_alert.py          # the guessing alert's text and Telegram notifier
│   ├── static_versioning.py    # the fleet hash stamped on every asset URL
│   └── (vendored)              # build_info, compression, login_lockout, no_window, notify/, pooled_http, runtime_data, usage_log
├── tests/                      # unit tests on invented fixture trees; tests/e2e is the browser suite,
│                               #   tests/archive the archive verbs over a fake Outlook (and the consumer contract)
├── scripts/                    # the gate, the ten ratchets (vendored), gen_icons, gen_tailscale_cert, set_password
├── assets/                     # tray icon and Stream Deck tile from scripts/gen_icons.py, and ArchiveMacro.bas
│                               #   (the Outlook macro template, no paths)
└── docs/                       # this file, scanning.md, and design/ (the search study and the app mock-up, invented data)
```

## Where new code goes

- Anything that reads the disk, the index or the scan history goes in `src/`, testable without FastAPI. `src/` never imports `app/`.
- A route is a thin wrapper in `app/webapp/routers/` over a `src/` call.
- A front-end view is its own module in `app/webapp/static/`, imported by `main.js` with a relative path (the cache-busting stamp only rewrites relative imports).
- A shared UI component comes from project-scaffolding's `_vendored/`, copied verbatim and declared in `.fleet.toml` `[vendored]`; never edit a vendored file here.
- A test goes in `tests/` on an invented tree (`tests/fixture_tree.py`), never real paths or names.

The structure ratchet holds files to 1,000 lines, functions to 100, directories to 40 modules and the root folders to the shape.
