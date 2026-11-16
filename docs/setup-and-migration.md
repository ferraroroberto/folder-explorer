# Set up on another computer, and move over

The code is identical on every machine. What differs is machine-local: `config.json`, the Settings choices saved in it (including **Available features**), the secrets in `.env`, and the runtime-data folder. Nothing here is detected for you: you set it once per machine.

Every path below is written with a placeholder: `<repo>` is the folder you cloned into, `<runtime-data>` is the runtime-data folder (see [Where the state lives](#where-the-state-lives)), `<user>` is the Windows account.

The copy list was derived from the code, not from memory: every path the app reads or writes comes from `src/config.py` (`config.json`), `src/secrets_env.py` and `scripts/gen_token.py` (`.env`), `src/password.py` (the password hash), `app/webapp/bind.py` (the certificate), `src/store.py`, `src/email/schema.py`, `src/email/indexer.py`, `src/scan_lock.py`, `src/docs/writer.py` and `src/usage_log.py` (the runtime-data folder), `src/email/mailboxes.py` (the mailbox registry), `src/macro_home.py` and `src/file_assoc.py` (per-user Windows settings). To check it again after a change, search the code for `data_dir()`, `PROJECT_ROOT /` and `os.environ`.

## Set up on a new machine

**Prerequisites**

- Windows, and Python 3.11 or newer on `PATH`.
- Git.
- Optional, by feature (each is a block in Settings → Available features; a machine without one switches that block off and the rest runs unchanged):
  - **Email** needs `requirements-email.txt` (`extract-msg`, `rapidfuzz`).
  - **Outlook archive** needs `requirements-outlook.txt` (`pywin32`) and Outlook (classic).
  - **Screenshot** and the tray need `requirements-tray.txt` (`pystray`, `pillow`, `pywin32`, `mss`) and a Tk-enabled Python. Without it `tray.bat` starts nothing (it has no console to say why); `start.bat` needs only `requirements.txt`.
  - Remote access from a phone needs Tailscale with HTTPS certificates turned on.

**Steps**

1. Clone the repository, then in `<repo>`:

   ```powershell
   python -m venv .venv
   & .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-tray.txt
   & .\.venv\Scripts\python.exe -m pip install -r requirements-email.txt     # optional: the Email block
   & .\.venv\Scripts\python.exe -m pip install -r requirements-outlook.txt   # optional: the Outlook archive block
   ```

2. Create the machine's settings. Either `copy config.example.json config.json`, or run the app and press **Create config.json** in Settings (it never overwrites a file that exists). Every key has a default, so this step is optional until you change something.
3. Run the app: `tray.bat` for the always-on tray, or `start.bat` for a console. Open `http://127.0.0.1:8453/`. Put a shortcut to `tray.bat` in `shell:startup` to start it at sign-in.
4. In **Settings → Available features** switch off every block this machine cannot run (a block whose dependency is missing says why on its row). Blocks are on by default; a change applies at once, except the Screenshot block, which applies when the tray next starts (`tray.bat --restart`).
5. Set the folders: **Folders to scan** (default: every fixed drive and each OneDrive folder), and, if you use the Email block, **Email folders**. Press **Rescan** in the Scans tab, or run `& .\.venv\Scripts\python.exe -m app.cli scan`.
6. Optional, remote access from a phone: `scripts\gen_tailscale_cert.py`, then `python -m src.password set` and `scripts\gen_token.py`, then restart (README, Remote access).
7. Optional, the Outlook macro: set `FOLDER_EXPLORER_HOME` to `<repo>` for the user (Settings → Outlook macro prints the exact line) and import `assets\ArchiveMacro.bas` in Outlook.
8. Optional, a nightly scan: a scheduler job that runs `& <repo>\.venv\Scripts\python.exe -m app.cli scan`, with Settings → Scan schedule left `off` so two schedules never double-run.

**Update**: `git pull`, then `tray.bat --restart` (or stop and start `start.bat`). `--restart` needs the shared `tray_lifecycle.ps1` that the fleet-config repo's install puts under `%USERPROFILE%\.claude\tray\`; on a machine without it, `tray.bat --restart` stops with an error, so quit the tray from its menu and run `tray.bat` again. The page footer and `GET /api/version` show the running build; it should match `git rev-parse --short HEAD`.

## Where the state lives

| Place | What it holds | Moved with |
| --- | --- | --- |
| `<repo>\config.json` (or the file `FOLDER_EXPLORER_CONFIG` names) | Every setting, including the roots, the email folders and **Available features** | copy, then edit |
| `<repo>\.env` | Secrets | copy |
| `<repo>\mailboxes.json` | The mailbox registry for the archive verbs | copy |
| `<repo>\config\app_password.json` (or `APP_PASSWORD_FILE`) | The salted password hash | copy or set again |
| `<repo>\webapp\certificates\` | The tailnet certificate and key | issue again |
| `<runtime-data>` | The scan history and indexes, the email index, logs, backups | see below |

`<runtime-data>` is `C:\sqlite\folder-explorer\` by default. `FOLDER_EXPLORER_DATA_DIR` names the folder itself and `FLEET_DATA_ROOT` names a root that holds one folder per app. It is never under `<repo>`.

All of `config.json`, `.env`, `mailboxes.json`, `config\app_password.json` and `webapp\` are gitignored, so a `git clone` on the new machine has none of them.

## Migrate from an existing machine

Do this with the app stopped on both machines (`tray.bat` quit from the tray menu), so no SQLite file is copied mid-write.

### What to copy

| What | Required? | Notes |
| --- | --- | --- |
| `config.json` | Recommended | Carries your choices, including **Available features**. Edit it after copying (below). Without it the app runs on defaults. |
| `.env` | If remote access is on | The names are `FOLDER_EXPLORER_TOKEN` (written by `scripts\gen_token.py`), and the optional `FOLDER_EXPLORER_TELEGRAM_BOT_TOKEN` and `FOLDER_EXPLORER_TELEGRAM_ALERT_CHAT_ID` for the wrong-password alert. An old `FOLDER_EXPLORER_PASSWORD` line is read only until a password hash exists. Copy over a private channel, never by mail or a shared folder; or mint a new token with `scripts\gen_token.py` and sign in again on each device. |
| `config\app_password.json` | If remote access is on | Or run `python -m src.password set` on the new machine. |
| `mailboxes.json` | If you use the archive verbs | Names and addresses are machine-local and never committed; `mailboxes.sample.json` is the template. |
| `<runtime-data>\state.sqlite3` | Optional | Scan history, saved searches, recent documents, found documents and the cleanup shortlist. A row is tied to a root by its key, which comes from the folder's name (a drive root by its letter), so it carries over only for a root with the same key on the new machine. Without it the app starts with empty history and rescans. |
| `<runtime-data>\emails.sqlite3` | Optional, rebuildable | The email index. Rebuild with `python -m app.cli email index --fresh`, which reads every mail again (and downloads online-only mail, within `email.download_budget_mb`). Copy it to avoid that, then run `email index` once to bring it up to date. |
| `<runtime-data>\index\*.sqlite3` | Rebuildable, do not copy | One folder index per finished scan. A scan on the new machine writes its own. |
| `<runtime-data>\doc-backups\` | Optional | The copies **Overwrite theirs…** kept. Read only by you. |
| `<runtime-data>\cut-names-undo-*.json` | Optional | The undo logs `email batch fix-cut-names` wrote, needed only to revert that rename on the old machine's folders. |
| `<runtime-data>\usage.sqlite3` and `*.log` | No | The local view log and the logs. They describe the old machine. |
| `<runtime-data>\*.lock` | Never | Held by a running process only. |
| `webapp\certificates\` | No | Issued per machine: run `scripts\gen_tailscale_cert.py` on the new one. |

Never copy `.venv`: create it on the new machine. Never copy `<repo>` wholesale from a running install; clone it.

### What to edit after copying

In `config.json`:

- `roots`, `exclude`, `email.roots`, `email.exclude`: absolute folder paths. Change each to the folder on this machine, or set `roots` to `null` for every drive and OneDrive. Entries pointing at a folder that does not exist show as missing in Scans; the path rules (an email folder inside a scan root) are checked when you edit them in Settings.
- `email.index_path`: `null` for this app's own index, otherwise the path of another app's index file on this machine.
- `snap.folder`: the screenshot save folder, or `null` for `Pictures\snaps`.
- `port`, `remote_access`: change only if the port is taken or the machine is not on the tailnet. With `remote_access` true and no certificate yet, the app stays on loopback.
- `features`: switch off each block this machine lacks, in Settings or by setting its key to `false`.

Elsewhere:

- `FOLDER_EXPLORER_HOME` (user environment) for the Outlook macro: set it to `<repo>` on this machine.
- The Markdown "Open with" association: switch **Associate Markdown files with this app** on again in Settings; it is a per-user registry entry that does not travel.
- The Outlook macro: import `assets\ArchiveMacro.bas` in Outlook again.
- A startup shortcut to `tray.bat`, and any scheduler job that runs the scan.

### Check that the copy worked

1. `& .\.venv\Scripts\python.exe -m app.cli roots` lists each root and its last scan (none yet on a machine that has not scanned).
2. Start the app, then `GET http://127.0.0.1:8453/api/version`: `git_sha` equals `git rev-parse --short HEAD`.
3. Settings → Available features shows every block you meant to keep on, and a reason on any that cannot run here.
4. Run a scan (Scans → Rescan, or `python -m app.cli scan`). Explore then shows each root's folders with sizes.
5. With the Email block on: `python -m app.cli email status` shows the index's size and last runs; Search → Emails finds a mail you know.
6. With remote access on: open the tailnet address from another device and sign in.

## Moving over from the retired tools

This app replaces email-archiver, the folder searcher tray and the automation repo's region screenshot tool (`snap`), which are retired.

- **The email index:** `email import --from <emails.db>` copies email-archiver's index in once; from then on this app is the index's only writer.
- **The folder searcher's window:** the tray's Quick search window covers it: instant search over the saved index, words split on spaces or `;` and AND-ed over the full path, email-branch pruning, path-depth trimming, Enter, double-click to open in File Explorer, always on top, one window. Its Emails tab covers email-archiver's search and opening a found `.msg`.
- **Callers:** task-os and life-os run the archive verbs and `email index --root` here, and life-os looks folders up with `folders search --json`. life-os finds this checkout beside its own (`LIFE_OS_FOLDER_EXPLORER_REPO` overrides that). For one release it still falls back to email-archiver (`LIFE_OS_EMAIL_ARCHIVER_REPO`) and the folder searcher's index file when this app is not found, and email-archiver's own scan hands over to `email index --fresh` here once its `database.path` points at this app's index.
- **The screenshot hotkey:** the tray's **Screenshot** menu and the same default hotkey (`ctrl+shift+a`) replace the separate snap script. Its settings are the `snap` block of `config.json`, and the snaps land in the folder it names: set `snap.folder` to the folder you read them from (the default is `Pictures\snaps`).
- **Nightly:** this app's scan replaces both email-archiver's scan and the folder searcher's scan in the nightly chain.
- **Archiving a mail:** the Outlook macro and the Archive sheet replace email-archiver's archive dialog and its Stream Deck buttons.

## Available features

**Settings → Available features** lists the feature blocks. Each is on by default, and the choice is saved in `config.json` under `features` (machine-local, never committed). A block that is off hides its screens, stops its background work and answers its API with "Switched off in Settings, Available features". Turning it on again restores it with no restart, except Screenshot (the tray starts it, so it applies the next time the tray starts).

| Block | Covers | Needs on this machine |
| --- | --- | --- |
| Email | The email index and its search, mail previews with Show all, the email step of every scan (and `email index` on the command line, which then exits 4, "not set up here") and the Emails section of Scans. | `extract-msg` |
| Outlook archive | The Archive sheet and window, the Outlook macro, Find in mailbox, the archive verbs (`email batch`, the Outlook bridge) and `/api/v1/archive`. | `pywin32`, Outlook (classic) |
| Documents | Opening, editing and dropping Markdown and HTML files, the Documents search mode and the Markdown "Open with" registration. | nothing beyond `requirements.txt` |
| Screenshot | The tray's region screenshot and its hotkey. | `requirements-tray.txt`, Tk |

A block whose dependency is missing stays switchable but shows as not available, with the reason on its row. There is no machine detection: switch off what the machine cannot run.
