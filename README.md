# folder-explorer

Where the space on your drives goes, and where a folder or an archived mail is. A Windows web app that scans folders by their listings only, then shows a folder tree with sizes and shares, the largest files, the top percentile of files and a breakdown by file type, and searches folder paths and an email archive's index. OneDrive files that live only in the cloud are counted both ways: **Size** (what the cloud stores) and **On disk** (what this PC holds), with a toggle for which one drives the totals. The disk walk never opens a file, so it never downloads one.

FastAPI + a static PWA, a Windows tray, and the fleet design system. The disk views read nothing but folder listings, with two exceptions. Mail is the first, and only for `.msg` files inside the configured email folders (`email.roots`): [What opens a file](docs/scanning.md#what-opens-a-file) lists every reader, and the Archive sheet, the Outlook macro and the archive verbs file mail into those folders only on request ([docs/archive.md](docs/archive.md)). The Markdown viewer is the second: it reads a `.md` file, and the images beside it, when you open it, and its editor saves that one file when you press Save, after checking it hasn't changed on disk since you opened it (same page). Nothing else outside the email folders is ever opened or written, and nothing outside them is moved or deleted. On this PC only, Explore can also ask Windows to open a folder in File Explorer or a file in its own app (see Features). The app reads nothing to do it: it hands Windows the path, and the app Windows starts opens the file.

It replaces two older tools: email-archiver (the Outlook archive, its email index and its batch CLI) and the folder searcher tray from the `automation` repo. Both are retired; see Moving over.

## Install

Python 3.11+ on Windows.

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-tray.txt
& .\.venv\Scripts\python.exe -m pip install -r requirements-email.txt   # optional: Show all for mails, the archive suggester
& .\.venv\Scripts\python.exe -m pip install -r requirements-outlook.txt # optional: the Archive sheet, the macro, the archive verbs
copy config.example.json config.json   # optional: every key has a default
```

## Run

- **Tray (always on):** `tray.bat`. Starts the tray, which starts the web app and restarts it if it dies. `tray.bat --restart` restarts both. Put a shortcut to `tray.bat` in `shell:startup` to start it at sign-in.
- **Console only:** `start.bat` (or `& .\.venv\Scripts\python.exe -m app.webapp`); Ctrl-C stops it.
- Open `http://127.0.0.1:8453/` on this PC. With remote access on, the tailnet address is `https://<this-pc>.<tailnet>.ts.net:8453/`.

## Remote access (phone)

One setting, `remote_access` in `config.json`, also shown as a switch in Settings:

| `remote_access` | Binds | Cert | Sign-in |
| --- | --- | --- | --- |
| `true` (default) | every interface, HTTPS | the tailnet certificate in `webapp/certificates/` | password, then a bearer token; this PC never needs it |
| `false` | `127.0.0.1` only, plain HTTP | none | none |

The change applies on the next start (tray menu **Restart web app**, or `tray.bat --restart`). With `true` but no certificate yet, the app stays on loopback and Settings says so.

One-time setup for the phone:

1. `& .\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py` issues the tailnet certificate (Let's Encrypt via `tailscale cert`; HTTPS certificates must be on in the Tailscale admin console). Every start renews it when it is within 30 days of expiry.
2. `& .\.venv\Scripts\python.exe scripts\set_password.py` sets the password (asked twice, stored in the gitignored `.env`, never printed) and mints the token.
3. On the phone, open the tailnet address, sign in, then **Share → Add to Home Screen**.

**Wrong passwords.** Each device gets 5 free wrong passwords; every further one makes it wait 2 s, doubling to 5 minutes (the sheet shows the wait from the `429` + `Retry-After` reply), and a correct password or an hour of quiet starts it over. This PC is never locked out. The state is in memory, so a restart clears it. Three wrong passwords in a row from one device send one Telegram message to the `coding alerts` chat (app name, device address and count, never a password), at most one per five minutes. It needs `FOLDER_EXPLORER_TELEGRAM_BOT_TOKEN` and `FOLDER_EXPLORER_TELEGRAM_ALERT_CHAT_ID` in `.env`; without them the attempt is only logged.

## Scans

The default roots are every local fixed drive plus each OneDrive folder, deduplicated by path. Set `roots` in `config.json` (a list of folders) or in Settings → Folders to scan. Three ways to scan, all under one lock so two scans never overlap:

- **Rescan** in the Scans tab (one root or all), or **Rescan now** in the tray menu.
- **In-app schedule:** Settings → Scan schedule (`off`, `nightly` at `schedule_time`, `hourly`). Off by default; it runs only while the app runs, so it suits a PC without app-launcher.
- **CLI:** `& .\.venv\Scripts\python.exe -m app.cli scan` (all roots) or `... scan --root c`; `python -m app.cli roots` lists the roots and their last scan. Exit 0 ok, 1 a root failed, 2 another scan is running, 3 unknown root.

On this PC the nightly scan is an app-launcher Job that runs the CLI, so the in-app schedule stays off. `exclude` (Settings → Skip these folders) lists folders every scan leaves out; the disk index, folder search and the email index all skip them.

**Settings owns the folder lists; Scans only runs.** Folders to scan, Skip these folders, Email folders and Leave out of the email index are four lists with the same rows: add a full path, remove one from its row menu after a confirm, each saved at once. A new entry must be an existing folder, not already in the list or nested in one of its entries, and inside the folder it belongs to (a skip folder or an email folder inside a folder to scan, an email exclude inside an email folder); a folder that still holds an entry of the list below it is removed only after that entry. Entries already saved are not re-checked, so an older `config.json` loads unchanged and a stale path can still be removed. The Scans tab shows status and runs scans and the email index, with no settings of its own.

**The email index.** With `email.roots` set (the email folders, each inside a scan root), the app keeps its own email index, `emails.sqlite3` in the runtime-data folder, and every scan ends with the email stage that updates it. The Scans tab shows each run and runs it now. `python -m app.cli email index [--root <folder>]` runs the stage alone; exit 0 indexed, 1 failed, 2 busy, 3 not inside an email folder, 4 not set up here. The download budget, the other `email` commands (`import`, `status`, `subjects`, `--fresh`, `scan --no-email`) and the index layout: [docs/scanning.md](docs/scanning.md#running-the-email-index).

How a scan reads sizes without downloading anything, and the index it writes: [docs/scanning.md](docs/scanning.md). Whether a scan or an email run is running, finished, failed or died is one derived state, shown the same on every screen and route: [docs/run-state.md](docs/run-state.md). A mail's "Show all" load in the Search preview has its own declared state: [docs/body-state.md](docs/body-state.md).

Scan output (the index, the scan history, logs, the usage log) lives in the runtime-data folder, `C:\sqlite\folder-explorer\` by default (`FLEET_DATA_ROOT` or `FOLDER_EXPLORER_DATA_DIR` move it), never in the repo.

## Search

The first tab searches **Folders**, **Markdown** files or **Emails**; the mode and the box live in the URL (`#search?mode=emails&q=...`, or `mode=docs` or `mode=folders`), so a search can be bookmarked.

- **Folders** searches every root's current index: a folder matches when its full path holds every word (split on spaces or `;`), so `alpha budget` finds `...\Alpha\2025\Budget`. Accents and case never matter, and a word of six letters or more that is one typo off still finds the folder, ranked below the exact hits; each result shows its match as a percentage (100% when every word is exact). An index from before accent folding matches words as typed until the next scan. A folder whose subfolders are named like email addresses shows once, with a count, and the display can hide the first N path components. A folder inside a scanned OneDrive root is also on the drive that holds it, so **Show OneDrive folders once** (on by default) lists it only as the OneDrive row; the drive's copies are left out of the query itself, so the count matches the list, and a library with no finished scan keeps the drive's copy. All three are under Display, and saved in `folder_search` (`skip_levels`, `prune_people`, `onedrive_prevails`). An index from before folder search asks for a rescan. Another app gets the same results with no app running: `python -m app.cli folders search --query "<words>" [--limit N] --json` prints one JSON document (`schema_version` 1; each result's full `path`, its shortened `display` and its `score` from 0 to 1). Exit 0 searched, 1 the query has no words, 4 no root has a finished scan; keys and codes in `app/cli/folders.py`.
- **Markdown** lists every `.md` and `.markdown` file in the last scan of every root, newest first, with its folder, size and modified date; an empty box lists them all, and each word narrows it to names that hold every word (case and accents never matter). It reads the index only: no file is opened to build the list, and a cloud-only file is listed with an **Online-only** mark. A row opens the same viewer as Explore (a sheet, or **Open in new window** for `/doc`), so a cloud-only file shows "Only in OneDrive" with **Download and open**; the preview card beside the list (on a wide window) also has **Edit here**, **Show in Explore**, **Copy path** and, on this PC, **Open in default app**. The list loads 100 at a time with **Show more**. Hidden files follow Explore's setting.
- **Emails** searches the email index read-only (this app's own, or another app's named by `email.index_path`: an SQLite file with the `emails` and `emails_fts` tables). Words and `"phrases"` search subject, sender, recipients and the indexed preview; `-word` excludes and `OR` takes either. Operators: `from:` `to:` `cc:` `with:` `subject:` `filename:` `has:attachment` `larger:5M` `after:` `before:` `newer_than:30d` `in:` `is:flagged` `is:done` `id:`. Every chip, filter and facet writes its operator into the box. An operator the index has no field for yet is listed as left out, never faked.
- **Preview:** the indexed fields with the matches marked, and **Show all**, which reads the full mail. **Open in Outlook** (the archived `.msg`), **Find in mailbox** (the live copy, looked up by Message-ID in the Inbox, the archive folder and Sent Items, and opened in its own window) and **Open in File Explorer** work on this PC only, as does **Open folder** on a mail (its folder in File Explorer, through the same route as Explore's open actions); every device gets Show folder (Explore at that folder) and Copy path.
- **Search API for other apps:** `GET /api/v1/search/status`, `/api/v1/search/folders` (with `within=<folder>` to keep the search to some folders) and `/api/v1/search/emails` answer in a versioned JSON contract with a distinct `state` (`ok`, `not-configured`, `indexing`, `error`). A caller on this PC needs no sign-in. Shapes, limits and states: [docs/service-api.md](docs/service-api.md).
- **Flagged-mail API for other apps:** `GET /api/v1/mail/flagged` lists the mails flagged in Outlook, oldest first, with offset paging and the same `state` envelope, so "can't tell" never reads as "nothing flagged". Fields and paging: [docs/service-api.md](docs/service-api.md#get-apiv1mailflagged).
- **Archive API for other apps:** `POST /api/v1/archive/plan`, `/apply`, `/revert` and `/index` run the CLI's archive verbs (the ones task-os drives) over HTTP, each in a child process, for a caller on this PC. An answer says `done`, `not-found`, `refused` or `error`, and keeps a call that was acknowledged apart from the mails confirmed filed. Bodies, outcomes and codes: [docs/service-api.md](docs/service-api.md#archive).
- **Quick search from the tray:** a left click on the tray icon opens a small always-on-top **Quick search** window with the same **Folders** and **Emails** search, through the web app's own API (the same routes as this tab, so the same results). Typing searches as you go (Enter searches now), `↑`/`↓` move, Enter or a double-click opens the folder in File Explorer or the mail's `.msg` in Outlook, **Copy path** (`Ctrl+Shift+C`) copies the whole path, `Tab` switches tab and `Esc` hides it; a second click brings the same window back with its last tab and query. With the web app down it says so, never "no matches". **Tray click opens Quick search** in Settings (`tray.left_click`: `quick_search`, the default, or `open_app`) picks what a left click does; the tray menu has **Quick search** and **Open** either way.
- **Saved searches** sit under the box (stored in `state.sqlite3`; relative dates stay relative); the last 10 searches show when the box is empty, per device.
- **Keyboard:** `/` the box, `↓` or `j`/`k` the results, `Enter` preview, `a` Show all, `o` Outlook (this PC), `e` open the folder in File Explorer (this PC; on a Markdown file, open it in its default app), `f` show the folder in Explore, `c` copy the path, `Esc` back. `e`, `f` and `c` mean the same on an email, a folder and a Markdown result, and `Enter` on a Markdown result opens the viewer.

**Reading a mail.** Show all reads a `.msg` only when it sits inside the configured email folders (`email.roots`, each inside a scan root), looks the path up by the mail's row id (never a path from the client), and reads it in a child process with a 30 s timeout. Reading an online-only OneDrive mail downloads it, and the button says so. An HTML body is cleaned and shown in a sandboxed frame (no scripts, no same-origin, no referrer) in the app's current theme: in dark, the mail's own colours are inverted in luminance with the hue kept and images turned back, and a sun/moon button on the frame flips that one message to the other look. The frame loads nothing remote until you turn on **Load remote images** for that mail (off by default, per message, forgotten when you leave): then only `https:` images load, never scripts or anything else. Bodies are never stored. It needs `requirements-email.txt`; without it, Search works and Show all says why it is off.

## Archive

**Archive sheet and Outlook macro (this PC).** **Archive the open mail**, the first card on Search in email mode, opens the Archive sheet for the mail open or selected in Outlook (classic): ranked folder suggestions with why, a Find a folder search, the category, date-prefix and copy choices, and the file name. The Outlook macro puts the same flow on Outlook's ribbon, in a window. Both read the mail from Outlook, never a `.msg` from disk, write only the filed `.msg` and its attachments inside `email.roots`, and run every Outlook step in their own `python -m app.outlook` process, never starting Outlook. Needs `requirements-outlook.txt` and Outlook (classic). Behaviour, setting up the macro, timings and failure messages: [docs/archive.md](docs/archive.md#archive-sheet-and-the-outlook-macro-this-pc).

**Archive verbs (Outlook).** `python -m app.cli email batch <verb>` is the headless Outlook archive that task-os and life-os drive: `plan`, `apply`, `revert`, `dismiss`, `renumber`, `fix-cut-names`, `draft`, `read`, `send`, `mailboxes`, `find` and `sync`. Every verb prints one JSON document on stdout (`schema_version` 1) and writes only inside `email.roots`; flags, error codes and exit codes are in `app/cli/email_batch.py`, and `tests/archive/test_consumer_contract.py` replays every command shape the two apps send. What each verb does and the rules they keep: [docs/archive.md](docs/archive.md#archive-verbs-outlook).

## Moving over

This app replaces email-archiver and the folder searcher tray, which are retired.

- **The email index:** `email import --from <emails.db>` copies email-archiver's index in once (see Scans); from then on this app is the index's only writer.
- **The folder searcher's window:** the tray's Quick search window (see Search) covers it: instant search over the saved index, words split on spaces or `;` and AND-ed over the full path, email-branch pruning, path-depth trimming, Enter, double-click to open in File Explorer, always on top, one window. Its Emails tab covers email-archiver's search and opening a found `.msg`.
- **Callers:** task-os and life-os run the archive verbs and `email index --root` here, and life-os looks folders up with `folders search --json`. life-os finds this checkout beside its own (`LIFE_OS_FOLDER_EXPLORER_REPO` overrides that). For one release it still falls back to email-archiver (`LIFE_OS_EMAIL_ARCHIVER_REPO`) and the folder searcher's index file when this app is not found, and email-archiver's own scan hands over to `email index --fresh` here once its `database.path` points at this app's index.
- **Nightly:** this app's scan replaces both email-archiver's scan and the folder searcher's scan in the nightly chain.
- **Archiving a mail:** the Outlook macro and the Archive sheet replace email-archiver's archive dialog and its Stream Deck buttons.

## Features

- **Search:** folders by every word of their path, every Markdown file by name, emails by words and operators, with facets, saved searches and a preview (see Search).
- **Explore:** the root's totals, a folder tree that expands level by level (heaviest first, with share of parent, size and file count), and the selected folder's contents: its own files and folders, or every nested file. Hidden and system items (and everything inside a hidden folder) stay out of the list until the **Hidden items** switch is on, saved as `explore.show_hidden`. When shown, they carry a crossed-eye mark. Sizes and totals always include them, and the contents say how many items the list leaves out. A scan from before hidden items were recorded asks for a rescan and lists everything.
- **Markdown viewer:** a `.md` row in Explore carries an `MD` badge. A tap on a phone, or a double-click on the PC, opens it rendered in a full-height sheet (CommonMark with GitHub tables, task lists, footnotes and highlighted code, both themes), with its heading outline, its images and its relative links to other Markdown files, which open in the same sheet. Its ⋮ menu leads with **View**, **Edit here** and **Open in new window**: `/doc?root=…&scan=…&id=…`, a page of its own with the outline beside the text, which can be bookmarked and explains when a newer scan renumbered the file. Any signed-in device can read. A cloud-only file shows "Only in OneDrive" with **Download and open**, which brings that one file down to this PC. Limits and rules: [What opens a file](docs/scanning.md#what-opens-a-file).
- **Drop a Markdown file:** dragging a `.md` or `.markdown` file from File Explorer onto the window, in Explore or in `/doc`, shows a drop target and opens it. If the last scan has exactly one file with that name, size and modified time, that indexed copy opens as usual, Edit included. Otherwise the text is rendered read-only (up to 2 MB) with a note that editing, relative links and images need the file in a scanned folder. A drop of anything else is ignored with a short message. The page never sends a path: only the name, size and modified time, and the text of a file the scan doesn't have.
- **Open Markdown from Windows (this PC):** Settings' **Associate Markdown files with this app** adds folder-explorer to **Open with** for `.md` and `.markdown` and an **Open in Folder Explorer** item to File Explorer's menu, for your user account only (`HKCU\Software\Classes`, no administrator), and removes exactly those keys when switched off; the row shows the state read back from the registry. To make it the default, pick it once in **Open with → Choose another app** (Windows may ask anyway). The file opens in a `/doc` window (an Edge app window, or the default browser without Edge), also when it is newer than the last scan, as long as its folder was scanned; with the app not running, or a file outside the scanned folders, a message says so. The `/doc` window's sidebar lists the documents opened last, on any device, kept across restarts. How it finds the file: [What opens a file](docs/scanning.md#what-opens-a-file).
- **Markdown editor:** **View | Edit** in the sheet or the `/doc` window. On the PC the source (with Markdown colouring and a format bar) sits beside a live preview that follows the cursor; on a phone **Write | Preview** toggles them and a row of Markdown keys sits above the keyboard. Save is explicit (Save, or Ctrl+S), never automatic; Esc goes back to View. A file changed on disk since you opened it stops at "Changed on disk since you opened it" with Compare, Copy my text, Load theirs and **Overwrite theirs…** (which asks first and keeps the replaced copy in the runtime-data folder's `doc-backups/`). An unsaved draft stays on the device until it is saved or discarded, and leaving with one asks first. Files up to 1 MB and in UTF-8 edit; others open read-only.
- **Opening from Explore (this PC):** double-click a folder to open it in File Explorer, or a file (other than Markdown, which opens in the viewer) to open it in its default app; a Markdown file's menu keeps **Open in default app**. Each row's ⋮ menu also has **Edit** (the shell's edit action), **Show in File Explorer** (its folder, with the file selected) and **Copy path**. The server looks the item up by its id in the current scan, never by a path from the page, and refuses every caller but this PC: loopback, or the PC's own tailnet or LAN address (a socket the OS lets bind that address), so opening the app by its tailnet name on the PC works the same, with no sign-in. On a phone the menu offers Copy path only, under a line saying opening works on the PC.
- **Opening a folder fast (this PC):** a folder opens through the shell's explore action, which the Explorer already running answers without a new `explorer.exe` process, and the app then brings that window to the front (restoring it when minimised) even though the browser has the focus. Settings' **Reuse an open Explorer window** (off by default, saved as `desktop.reuse_explorer_window`) shows the folder in the Explorer window used last instead: faster, but that window loses what it showed. A folder the app can't confirm within 250 ms (a slow or offline share) opens anyway rather than waiting. Each open logs its timings to `webapp.log` in the runtime-data folder, as milliseconds from the request: `open: request ready at … ms, existence check … ms (there)`, then `File Explorer asked for the folder in a new window at … ms`, then `window shown at … ms, in front at … ms`. A large gap before "asked" is the app or the check; between "asked" and "shown" it is Windows and Explorer; a `kept it behind` or `no File Explorer window showed the folder` warning names the failure.
- **Largest:** the 10 largest files under the selected folder, and how much of it the largest 1, 5 or 10 percent of files hold, grouped by subfolder.
- **Types:** bytes, count and share per extension; the box above the list narrows it by extension, and tapping a type filters every tab to it.
- **Scans:** each root's last scan, live progress, and Rescan.
- **Archive sheet and the Outlook macro:** file the open mail from Search or from Outlook's ribbon, with the folder suggestions and why (see Archive).
- **Archive verbs:** the headless Outlook archive another app drives, with a folder suggester that puts a folder already holding the thread first and scores each folder by its best match (see Archive). email-archiver's legacy suggester sums its matches per folder instead, so the two can disagree; a test with invented folders pins this app's order and %.
- **Size / On disk** toggle for every total; online-only files carry a cloud mark.
- Phone and desktop layouts, light and dark, text size, and the fleet's bottom-tab navigation; the build line in the footer.

## Configuration

`config.json` (gitignored; `config.example.json` lists every key): `remote_access`, `port` (8453), `server` (`hypercorn` serves HTTP/2 over the certificate; `uvicorn` is the HTTP/1.1 fallback), `roots` (`null` = every drive + OneDrive), `schedule`, `schedule_time`, `exclude` (folders every scan skips), `email` (`roots`: the email folders; `exclude`: folders inside them the email index skips; `index_path`: `null` for this app's own index, or another app's index to read only; `download_budget_mb`: `null` for no limit, else a cap in MB), `folder_search` (`skip_levels`, `prune_people`, `onedrive_prevails`), `explore` (`show_hidden`, the Hidden items switch), `desktop` (`reuse_explorer_window`, the Settings switch), `archive` (`date_prefix`: `false`, `true` or `"auto"` to follow the destination folder; `max_path_length`; `max_suggestions`, `min_score`) and `outlook` (`archive_folder`, `category`, `dismiss_category`, `self_address`, which only `config.json` sets, and `add_category` and `move_to_archive`, the Settings switches). The folder lists and the download budget are also set in Settings. With no `config.json`, Settings shows a "Set up folder-explorer" card whose button creates it from `config.example.json` (`POST /api/config/create`; a file that exists is never overwritten). Settings reads every block with a default, so a server older than the page still opens it, and a failed load says so in plain words, never as a script error. Secrets live in `.env` (`.env.example`).

## Verification gate

```powershell
powershell -File scripts\verify-before-ship.ps1
```

Byte-compile, the unit tests, the browser suite (Chromium desktop and WebKit iPhone against a disposable instance over an invented tree), and the ten fleet ratchets. Needs `requirements-dev.txt` and Node (`npx`) for the static ratchet.

The last stdout line of a run is `GATE-RESULT: PASS` or `GATE-RESULT: FAIL`, on a pass, a failed stage, a missing `.venv` and an unhandled error; exit codes are 0 and 1. Do not pipe the gate when its exit code matters (`| tail` reports `tail`'s status); a caller that must pipe asserts on that line, and a run without it is not a pass.

## Layout

Where code goes: [docs/layout.md](docs/layout.md). Agent instructions: [CLAUDE.md](CLAUDE.md).

## License

MIT, see [LICENSE](LICENSE).
