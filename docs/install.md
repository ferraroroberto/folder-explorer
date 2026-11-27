# Install, update and move folder-explorer

folder-explorer moves to another Windows PC the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. Neither ever resets or overwrites what a PC has configured. `setup.bat` runs the vendored installer (`src/installer.py`); what is specific to this app is declared in the `[install]` table of `.fleet.toml`.

In the commands below, `<repo>` is the folder you cloned into.

## What you need

- Windows 10 or 11, Git, and Python 3.11 or newer (the `py` launcher, or `python` on `PATH`) with Tk (the tray's screenshot needs it).
- Optional: Tailscale with HTTPS certificates turned on, to open the app from a phone ([README, Remote access](../README.md#remote-access-phone)); Outlook (classic) for the archive verbs.

## Fresh PC

```powershell
git clone <this repository's URL> folder-explorer
cd folder-explorer
.\setup.bat -WhatIf     # optional: lists every change it would make, makes none
.\setup.bat
.\tray.bat
```

`setup.bat` runs `src/installer.py`, which:

1. creates `.venv` when it is missing;
2. installs `requirements.txt`, and later reinstalls only when that file changed (its hash is kept inside `.venv`);
3. runs the dependency step, `pip install` of `requirements-tray.txt`, `requirements-email.txt` and `requirements-outlook.txt`, again only when one of those files changed;
4. creates `config.json` from `config.example.json` and `.env` from `.env.example` when they are missing;
5. ends with two lists: the required items, each `ok`, `missing` or `unknown`, and the optional features, each `available`, `off` or `unknown` with what it unlocks.

Each step prints `created`, `installed`, `added` or `unchanged`; a `failed` line names what went wrong and the run exits 1. Every key in `config.json` has a default, so it needs no edit until you change something.

Setup has succeeded when it ends with `Setup complete` and exits 0; an optional feature that is `off` never changes that. The optional features, each needed only for what it unlocks:

- **Sign-in password**, **Sign-in token**, **Tailnet certificate**: needed only to open the app from a phone over the tailnet. This PC alone uses the app without them. They are set with `.\.venv\Scripts\python.exe -m src.password set`, `.\.venv\Scripts\python.exe scripts\gen_token.py` and `.\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py`.
- **App answering on its port**: `available` when something answers `/api/version` on the configured port. It does not say which checkout answers, so compare the build it reports with `git rev-parse --short HEAD`.
- **Start at log on**: optional. `.\setup.bat -Startup` adds a shortcut to `tray.bat` in your Startup folder, only when no Startup entry already starts this checkout's tray.

Then open `http://127.0.0.1:8453/` and set the rest in Settings: the folders to scan, the email folders, and **Available features** (switch off a block this PC cannot run). Press **Rescan** in the Scans tab. [setup-and-migration.md](setup-and-migration.md) has the details for each setting.

Check it worked: the required list shows `ok` for `.venv`, `Requirements installed`, `config.json` and `.env`, and the Explore tab shows each root's folders with sizes after the first scan.

## Update

```powershell
cd <repo>
git pull --ff-only
.\tray.bat --restart
```

The page footer and `GET /api/version` show the running build; it should match `git rev-parse --short HEAD`. `tray.bat --restart` exits non-zero when the build it finds is not `HEAD`.

Every file the app, the tray, a restart, `setup.bat` or the gate writes in the checkout is gitignored, so the pull never meets a local change and always fast-forwards.

Run `.\setup.bat` as well when the pull brought a new requirement or a new config key; running it when nothing changed is harmless (it reports `unchanged`). It is also how a new setting arrives:

- **`config.json`**: every existing value stays as it is. Keys that `config.example.json` has and the file lacks are added, after a copy named `config.json.bak-<date>-<time>` beside it, and the run prints each added key name (never a value). The added values are the app's defaults, so the app behaves the same afterwards. A file that does not parse is left alone and reported, and the run exits 1. `setup.bat` tops up the `config.json` in the checkout only; a PC that points `FOLDER_EXPLORER_CONFIG` at another file keeps that file as it is.
- **`.env`**: created from `.env.example` only when missing. An existing one is never read or changed.
- **`mailboxes.json`**: never created or changed. The app runs without it (it uses the one Outlook mailbox), and `mailboxes.sample.json` shows its shape for when you need more than one.
- **`.venv`**: kept. Requirements are installed into it only when a requirements file changed.

A PC with an AllSigned PowerShell execution policy needs nothing different: `setup.bat` is a batch file, and `tray.bat` starts the shared tray helper through `src\ps_script.py` and the gate loads its PowerShell as text. Run the gate on such a PC through the launcher: `& .\.venv\Scripts\python.exe -m src.ps_script scripts\verify-before-ship.ps1`.

## Migrate from an existing install

Use this on a PC that already runs an older copy of folder-explorer: one that may not be a git clone, and may live at another path. The old copy keeps running until the new one is verified, and it is deleted only when you say so. Never copy the old folder wholesale, and never copy `.venv`.

In the steps below, `<old>` is the old folder, `<new>` the fresh clone, and `<runtime-data>` the runtime-data folder: `C:\sqlite\folder-explorer\` unless `FOLDER_EXPLORER_DATA_DIR` or `FLEET_DATA_ROOT` names another place. It is never under the repo, so it needs no move when the new copy runs on the same PC.

1. **Clone fresh beside it.** `git clone <this repository's URL> <new>`; never clone over `<old>`.
2. **Stop the old copy**, so no SQLite file is copied mid-write: quit its tray from the tray menu, or stop `scripts\start.bat`'s console. If the old copy started at log on, remove its Startup entry now, or it will start again at the next sign-in.
3. **Copy this PC's own files across.** From `<old>` into `<new>`, keeping the relative paths. All of them are gitignored, so nothing tracked is touched:
   - `config.json` (or the file `FOLDER_EXPLORER_CONFIG` names): every setting, including the folders to scan and **Available features**.
   - `.env`: the sign-in token and the optional wrong-password alert keys. Copy it over a private channel, never by mail or a shared folder.
   - `config\app_password.json` (or the file `APP_PASSWORD_FILE` names): the salted password hash.
   - `mailboxes.json`, if you use the archive verbs.
   - `webapp\certificates\`: the tailnet certificate and key. They are issued per PC and name this PC's tailnet host, so on a new PC issue them again with `scripts\gen_tailscale_cert.py` instead of copying.
   - State, only when the new copy uses another runtime-data folder than `<old>` did: `<runtime-data>\state.sqlite3` (scan history, saved searches, recent documents, the cleanup shortlist), `<runtime-data>\emails.sqlite3` (the email index; rebuildable), and `<runtime-data>\doc-backups\`. `<runtime-data>\index\` is rebuilt by a scan, and the `*.lock` and `*.log` files are never copied. [setup-and-migration.md](setup-and-migration.md#what-to-copy) says what each holds.
4. **Top them up.** In `<new>`, run `.\setup.bat -WhatIf` and read what it would change, then `.\setup.bat`. `config.json` gains only the keys it lacks (after a backup); the values you copied stay as they were. On a different PC, also edit the folder paths in `config.json` ([setup-and-migration.md](setup-and-migration.md#what-to-edit-after-copying)).
5. **Start the new copy.** `.\tray.bat`, and `.\setup.bat -Startup` if it should start at log on. Set `FOLDER_EXPLORER_HOME` (user environment) to `<new>` if you use the Outlook macro, and switch **Associate Markdown files with this app** on again in Settings: it is a per-user registry entry that does not travel.
6. **Verify it.** Open `http://127.0.0.1:8453/` and check that `GET /api/version` reports the `git_sha` of `git rev-parse --short HEAD` in `<new>`; that Settings → Available features shows the blocks you meant to keep on; and that Scans → Rescan finishes and Explore shows each root's folders with sizes. If anything is wrong, stop the new copy and start the old one again: it is untouched.
7. **Retire the old folder, only after you confirm.** Nothing in this playbook or in `setup.bat` deletes the old copy. When you are satisfied, rename it first (for example `<old>.retired`) and run on the new copy for a while; delete it yourself once you are sure. An agent working through this playbook asks you before it deletes anything.

## Uninstall

1. In Settings, switch off **Associate Markdown files with this app** if you turned it on (it removes its own per-user registry entry).
2. Quit the tray from its menu.
3. Remove the log-on shortcut with `.\.venv\Scripts\python.exe -m src.install.startup disable`. It removes only the shortcut `setup.bat -Startup` made, and only when it points at this checkout; an entry you made yourself stays, so delete that one from `shell:startup` by hand.
4. Remove the Outlook macro in Outlook, and the `FOLDER_EXPLORER_HOME` user environment variable, if you set them. Remove any scheduler job that runs the scan.
5. Delete `<repo>`. Keep a copy of the files listed under "Migrate" first if you may want them back. The scan history and indexes stay in `<runtime-data>`, so delete that folder too if you don't want to keep them.
