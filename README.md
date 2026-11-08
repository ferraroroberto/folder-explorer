# folder-explorer

Where the space on your drives goes, and where a folder or an archived mail is. A Windows web app that scans folders by their listings only, then shows a folder tree with sizes and shares, the largest files, the top percentile of files and a breakdown by file type, and searches folder paths and an email archive's index. OneDrive files that live only in the cloud are counted both ways: **Size** (what the cloud stores) and **On disk** (what this PC holds), with a toggle for which one drives the totals. The disk walk never opens a file, so it never downloads one.

FastAPI + a static PWA, a Windows tray, and the fleet design system. The disk views are read-only. Mail is the one exception, and only for `.msg` files inside the configured email folders (`email.roots`): the email stage reads new or changed mail (see Scans), Show all reads the one mail you ask for (see Search), and two things file Outlook mail into those folders, both only on request: the Archive sheet and the Outlook macro on this PC (see Archive sheet), and the archive verbs another app runs (see Archive verbs). Nothing outside the email folders is ever opened, moved or deleted. On this PC only, Explore can also ask Windows to open a folder in File Explorer or a file in its own app (see Features). The app reads nothing to do it: it hands Windows the path, and the app Windows starts opens the file.

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

**The email index.** With `email.roots` set (the email folders, each inside a scan root) and `email.index_path` left empty, the app keeps its own email index, `emails.sqlite3` in the runtime-data folder, and is its only writer. Every scan ends with the email stage: it compares the scan's list of `.msg` files with the index by path, size and modified time, reads only new or changed mail through `src/email/reader.py`, and removes rows for mail that is gone only after a complete listing of its folder. Besides Show all and the `renumber` verb, this stage is the only thing that opens a file, and it opens only a `.msg` inside `email.roots`. Reading an online-only mail downloads it, so by default one run downloads everything it needs (`email.download_budget_mb` is `null`, "No limit"). A whole number caps one run at that many MB and leaves the rest for the next run ("waiting for budget"); 0 reads local files only. A budget saved in `config.json` is kept as it is, so a config that already holds a number stays capped until Settings → Email folders → OneDrive downloads per run is switched to "No limit (recommended)". A file that fails is not read again until it changes. `email.exclude` (Settings → Leave out of the email index) names folders inside the email folders that only the email index skips; the global skip list applies to it too. The Scans tab shows each run (new, downloaded, failed, and waiting for budget when a cap is set) and runs it now, or for one folder; a run of every folder started there lists the email folders afresh (`--fresh`), so mail filed since the last disk scan is read too. While a run is going its row shows a bar over the files that need a read, with the files done, the files and MB downloaded, the failed reads and the files left unchanged, refreshed every 2 seconds; the stage stores those counts on the running row of the run history, so the page reads them from `GET /api/email/status` (`runs[0].counts`, plus `budget_mb`):

- `& .\.venv\Scripts\python.exe -m app.cli email index` (every email folder) or `... email index --root <folder>` (one folder below one, in seconds, under the email lock only, so it never waits behind a disk scan). Exit 0 indexed, 1 failed, 2 busy (another email run held the lock past `--wait S`), 3 not inside an email folder, 4 not set up here.
- `... email import --from <emails.db>` copies another app's email index in once, keeping its rows; the next run reads each mail once more to fill To, Cc, attachments and size. `... email status` lists the last runs.
- `... email subjects [--root <folder>] [--limit N]` is a dry run that lists archived mails whose file name lost its first letters to an old reply-prefix rule ("Recibo" filed as "cibo"): a count and N names, printed locally. It reads each mail's subject under the email stage's download budget and lock, and renames nothing. `email batch fix-cut-names` renames those files to the full subject: a dry run unless `--apply`, with the date and sequence kept, no file overwritten (a taken name is counted as a clash), the index rows following the rename and an undo log (`cut-names-undo-<time>.json`, written before the first rename) in the runtime-data folder; `--revert <log>` restores from it. The disk walk's tree shows the new names after the next scan.
- `... email index --fresh` lists the email folders now instead of reading the last scan's index: a mail filed since that scan is only seen this way.
- `... scan --no-email` scans the disks only.

Downloaded mail stays on the disk until OneDrive frees it. The app never sets a file back to online-only, because that is a write. How a scan reads sizes without downloading anything, and the index it writes: [docs/scanning.md](docs/scanning.md).

Scan output (the index, the scan history, logs, the usage log) lives in the runtime-data folder, `C:\sqlite\folder-explorer\` by default (`FLEET_DATA_ROOT` or `FOLDER_EXPLORER_DATA_DIR` move it), never in the repo.

## Search

The first tab searches **Folders** or **Emails**; the mode and the box live in the URL (`#search?mode=emails&q=...`), so a search can be bookmarked.

- **Folders** searches every root's current index: a folder matches when its full path holds every word (split on spaces or `;`), so `alpha budget` finds `...\Alpha\2025\Budget`. Accents and case never matter, and a word of six letters or more that is one typo off still finds the folder, ranked below the exact hits; each result shows its match as a percentage (100% when every word is exact). An index from before accent folding matches words as typed until the next scan. A folder whose subfolders are named like email addresses shows once, with a count, and the display can hide the first N path components. A folder inside a scanned OneDrive root is also on the drive that holds it, so **Show OneDrive folders once** (on by default) lists it only as the OneDrive row; the drive's copies are left out of the query itself, so the count matches the list, and a library with no finished scan keeps the drive's copy. All three are under Display, and saved in `folder_search` (`skip_levels`, `prune_people`, `onedrive_prevails`). An index from before folder search asks for a rescan. Another app gets the same results with no app running: `python -m app.cli folders search --query "<words>" [--limit N] --json` prints one JSON document (`schema_version` 1; each result's full `path`, its shortened `display` and its `score` from 0 to 1). Exit 0 searched, 1 the query has no words, 4 no root has a finished scan; keys and codes in `app/cli/folders.py`.
- **Emails** searches the email index read-only (this app's own, or another app's named by `email.index_path`: an SQLite file with the `emails` and `emails_fts` tables). Words and `"phrases"` search subject, sender, recipients and the indexed preview; `-word` excludes and `OR` takes either. Operators: `from:` `to:` `cc:` `with:` `subject:` `filename:` `has:attachment` `larger:5M` `after:` `before:` `newer_than:30d` `in:` `is:flagged` `is:done` `id:`. Every chip, filter and facet writes its operator into the box. An operator the index has no field for yet is listed as left out, never faked.
- **Preview:** the indexed fields with the matches marked, and **Show all**, which reads the full mail. **Open in Outlook** (the archived `.msg`), **Find in mailbox** (the live copy, looked up by Message-ID in the Inbox, the archive folder and Sent Items, and opened in its own window) and **Open in File Explorer** work on this PC only, as does **Open folder** on a mail (its folder in File Explorer, through the same route as Explore's open actions); every device gets Show folder (Explore at that folder) and Copy path.
- **Saved searches** sit under the box (stored in `state.sqlite3`; relative dates stay relative); the last 10 searches show when the box is empty, per device.
- **Keyboard:** `/` the box, `↓` or `j`/`k` the results, `Enter` preview, `a` Show all, `o` Outlook (this PC), `e` open the folder in File Explorer (this PC), `f` show the folder in Explore, `c` copy the path, `Esc` back. `e`, `f` and `c` mean the same on an email and on a folder result.

**Reading a mail.** Show all is the one place the app opens a file. It reads a `.msg` only when it sits inside the configured email folders (`email.roots`, each inside a scan root), looks the path up by the mail's row id (never a path from the client), and reads it in a child process with a 30 s timeout. Reading an online-only OneDrive mail downloads it, and the button says so. An HTML body is cleaned and shown in a sandboxed frame (no scripts, no same-origin, no referrer) in the app's current theme: in dark, the mail's own colours are inverted in luminance with the hue kept and images turned back, and a sun/moon button on the frame flips that one message to the other look. The frame loads nothing remote until you turn on **Load remote images** for that mail (off by default, per message, forgotten when you leave): then only `https:` images load, never scripts or anything else. Bodies are never stored. It needs `requirements-email.txt`; without it, Search works and Show all says why it is off.

## Archive sheet and the Outlook macro (this PC)

**Archive the open mail**, the first card on Search in email mode, opens the Archive sheet for the mail open or selected in Outlook (classic): a collapsible preview of its readable text; its suggested folders, ranked, each with its % match (a weak guess below 35% is marked), why (same email, same subject, sender, topic: a folder already holding a copy with this mail's Message-ID leads at 100% as **Same email**, then folders holding this very subject, found apart from the search ranking so a crowded-out thread folder still leads), how many similar mails the folder holds with one example subject, and an Open button that shows the folder in File Explorer (looked up by its place in the last disk scan, never by a path from the page); another email folder found by name; **Current Explorer folder**, which offers the folder open in the front File Explorer window with its whole path (the server asks Windows through the bridge's `explorer` verb, so it works for a page on this PC only; a folder outside the email folders is refused, and "Cannot read Explorer windows" and "No Explorer folder is open" are different messages); the Outlook category (or none); the date prefix (**Auto** follows `archive.date_prefix` and the folder's own names, **On** or **Off** for this mail only, sent as `date_prefix` and to the bridge's `file` as `--date-prefix`); and the file name it will get. Every path the app shows uses `\` separators; stored paths keep whatever form they have. **Archive** saves the `.msg` and its attachments into that folder, tags the mail, and indexes the folder; it never moves the mail in Outlook (that is the `apply` verb's job). A mail already in the index is refused, and the sheet says where it is filed; the **Archive a copy anyway** switch under that notice turns the button into **Archive a copy to …** and sends `duplicate: true`, so the copy takes the folder's next sequence number and nothing is overwritten (the Outlook bridge's `file` verb takes `--duplicate` for the same).

The **Outlook macro** puts the same flow on Outlook's ribbon. It starts `pythonw -m app.outlook archive --entry-id <id> --store-id <id>` hidden and returns at once, so Outlook never waits. The entry fetches the mail again by those ids, then hands it to the Archive sheet of a page open on this PC; with no page open, a Tk window shows the same sheet: the match % with its rank, why and similar mail, an Open folder action per folder, the mail preview, a typed or browsed other folder, a **Current Explorer folder** button (the same check and messages as the sheet), the category, the date prefix with the saved-as name and the already-archived notice with its copy choice. It takes the app's white or dark tokens from `tokens.css` and follows the Windows app theme. Failures say which case they are: Outlook not reachable, no mail selected, the mail moved, no email folder mounted, pywin32 not installed.

- The sheet and the macro read the mail from Outlook, never a `.msg` from disk, and write only the filed `.msg` and its attachments inside `email.roots`.
- Every Outlook step runs `python -m app.outlook` in its own process with a timeout (the web server makes no COM call), attaches to the running Outlook and never starts one. The verbs log to `outlook.log` in the runtime-data folder.
- Needs `requirements-outlook.txt` (pywin32) and Outlook (classic); otherwise the card says why it is off. The new Outlook for Windows runs neither macros nor COM.
- `FOLDER_EXPLORER_OUTLOOK=off` keeps a process from starting the bridge at all (the e2e suite's disposable instance sets it).

Set the macro up once per PC:

1. Set the user environment variable `FOLDER_EXPLORER_HOME` to this checkout's folder. Settings → Outlook macro shows the exact `setx` line for this PC's checkout (a worktree resolves to its primary checkout) with a Copy button, and whether the variable is already set (read-only from the user environment; the app never runs `setx`). Open the sheet on the PC itself; other devices don't see the line.
2. In Outlook (classic), press Alt+F11, then **File → Import File…** and pick `assets/ArchiveMacro.bas` (or paste the text from Settings → Outlook macro → Copy into a new module).
3. **File → Options → Customize Ribbon**: add a custom group, choose **Macros** in "Choose commands from", add `ArchiveCurrentItem` and rename it **Archive**.
4. Restart Outlook so it reads the variable. If macros are blocked, allow them for this project in **Trust Center → Macro Settings** (or sign it with a self-made certificate from `SelfCert.exe`).

## Archive verbs (Outlook)

`python -m app.cli email batch <verb>` is the headless Outlook archive that task-os and life-os drive: `plan` (Inbox or Sent mail with ranked archive folders), `apply` (save a mail as `NNN - subject.msg` with its attachments into an email folder, move it to the Outlook archive folder and tag it), `revert`, `dismiss` (move without keeping a file), `renumber` (put a folder's sequence back in date order), `fix-cut-names` (rename mails whose name lost its first letters), `draft`, `read` and `send` (an unsent draft, sent only when its fingerprint matches the approved one), `mailboxes`, `find` (live Outlook folders, read-only) and `sync` (Send/Receive). Every verb prints one JSON document on stdout (`schema_version` 1) and logs to stderr and `email-batch.log` in the runtime-data folder; flags, error codes and exit codes are in `app/cli/email_batch.py`. `tests/archive/test_consumer_contract.py` replays every command shape the two apps send.

- The verbs write only inside `email.roots`: a destination, a file to delete or a folder to renumber anywhere else is refused per item. They read and write this app's own `emails.sqlite3`; the email stage still indexes new mail (`email index --root <folder>` after filing).
- `renumber` reads a `.msg` inside `email.roots` for its date only when the index has none, through `src/email/reader.py`; `fix-cut-names` reads each mail's subject there under the stage's download budget and lock, and neither starts Outlook.
- Outlook is reached over COM in the verb's own process, which the caller runs with a timeout. A closed Outlook is started visibly (`outlook.exe`), never as a hidden COM instance; Exchange senders resolve through `GetExchangeUser()`.
- `mailboxes.json` beside `config.json` (gitignored; `mailboxes.sample.json` is the template) lists the mailboxes `--mailbox` picks from; without it the verbs use Outlook's default store.
- Needs `requirements-outlook.txt` (pywin32); without it every Outlook verb answers `com_unavailable` and `mailboxes` reports each mailbox `unknown`. The suggester needs `rapidfuzz` (`requirements-email.txt`).

## Moving over

This app replaces email-archiver and the folder searcher tray, which are retired.

- **The email index:** `email import --from <emails.db>` copies email-archiver's index in once (see Scans); from then on this app is the index's only writer.
- **Callers:** task-os and life-os run the archive verbs and `email index --root` here, and life-os looks folders up with `folders search --json`. life-os finds this checkout beside its own (`LIFE_OS_FOLDER_EXPLORER_REPO` overrides that). For one release it still falls back to email-archiver (`LIFE_OS_EMAIL_ARCHIVER_REPO`) and the folder searcher's index file when this app is not found, and email-archiver's own scan hands over to `email index --fresh` here once its `database.path` points at this app's index.
- **Nightly:** this app's scan replaces both email-archiver's scan and the folder searcher's scan in the nightly chain.
- **Archiving a mail:** the Outlook macro and the Archive sheet replace email-archiver's archive dialog and its Stream Deck buttons.

## Features

- **Search:** folders by every word of their path, emails by words and operators, with facets, saved searches and a preview (see Search).
- **Explore:** the root's totals, a folder tree that expands level by level (heaviest first, with share of parent, size and file count), and the selected folder's contents: its own files and folders, or every nested file. Hidden and system items (and everything inside a hidden folder) stay out of the list until the **Hidden items** switch is on, saved as `explore.show_hidden`. When shown, they carry a crossed-eye mark. Sizes and totals always include them, and the contents say how many items the list leaves out. A scan from before hidden items were recorded asks for a rescan and lists everything.
- **Opening from Explore (this PC):** double-click a folder to open it in File Explorer, or a file to open it in its default app. Each row's ⋮ menu also has **Edit** (the shell's edit action), **Show in File Explorer** (its folder, with the file selected) and **Copy path**. The server looks the item up by its id in the current scan, never by a path from the page, and refuses every caller but this PC: loopback, or the PC's own tailnet or LAN address (a socket the OS lets bind that address), so opening the app by its tailnet name on the PC works the same, with no sign-in. On a phone the menu offers Copy path only, under a line saying opening works on the PC.
- **Largest:** the 10 largest files under the selected folder, and how much of it the largest 1, 5 or 10 percent of files hold, grouped by subfolder.
- **Types:** bytes, count and share per extension; the box above the list narrows it by extension, and tapping a type filters every tab to it.
- **Scans:** each root's last scan, live progress, and Rescan.
- **Archive sheet and the Outlook macro:** file the open mail from Search or from Outlook's ribbon, with the folder suggestions and why (see Archive sheet).
- **Archive verbs:** the headless Outlook archive another app drives, with a folder suggester that puts a folder already holding the thread first and scores each folder by its best match (see Archive verbs). email-archiver's legacy suggester sums its matches per folder instead, so the two can disagree; a test with invented folders pins this app's order and %.
- **Size / On disk** toggle for every total; online-only files carry a cloud mark.
- Phone and desktop layouts, light and dark, text size, and the fleet's bottom-tab navigation; the build line in the footer.

## Configuration

`config.json` (gitignored; `config.example.json` lists every key): `remote_access`, `port` (8453), `server` (`hypercorn` serves HTTP/2 over the certificate; `uvicorn` is the HTTP/1.1 fallback), `roots` (`null` = every drive + OneDrive), `schedule`, `schedule_time`, `exclude` (folders every scan skips), `email` (`roots`: the email folders; `exclude`: folders inside them the email index skips; `index_path`: `null` for this app's own index, or another app's index to read only; `download_budget_mb`: `null` for no limit, else a cap in MB), `folder_search` (`skip_levels`, `prune_people`, `onedrive_prevails`), `explore` (`show_hidden`, the Hidden items switch), `archive` (`date_prefix`: `false`, `true` or `"auto"` to follow the destination folder; `max_path_length`; `max_suggestions`, `min_score`) and `outlook` (`archive_folder`, `category`, `dismiss_category`, `self_address`), which only `config.json` sets. The folder lists and the download budget are also set in Settings. Secrets live in `.env` (`.env.example`).

## Verification gate

```powershell
powershell -File scripts\verify-before-ship.ps1
```

Byte-compile, the unit tests, the browser suite (Chromium desktop and WebKit iPhone against a disposable instance over an invented tree), and the ten fleet ratchets. Needs `requirements-dev.txt` and Node (`npx`) for the static ratchet.

## Layout

Where code goes: [docs/layout.md](docs/layout.md). Agent instructions: [CLAUDE.md](CLAUDE.md).

## License

MIT, see [LICENSE](LICENSE).
