# Install, update and move folder-explorer

folder-explorer moves to another Windows PC the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. Neither ever resets or overwrites what a PC has configured. `setup.bat` runs the vendored installer (`src/installer.py`); what is specific to this app is declared in the `[install]` table of `.fleet.toml`.

In the commands below, `<repo>` is the folder you cloned into. This page holds the steps; what each setting and state file is, and what each feature block needs, is in [setup-and-migration.md](setup-and-migration.md).

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

`tray.bat` runs the always-on tray; `scripts\start.bat` runs the app in a console instead.

Then open `http://127.0.0.1:8453/` and set the rest in Settings: **Available features** (switch off every block this PC cannot run), the folders to scan (default: every fixed drive and each OneDrive folder) and, with the Email block on, the email folders. Press **Rescan** in the Scans tab, or run `& .\.venv\Scripts\python.exe -m app.cli scan`.

Optional, once the app runs:

- **Remote access from a phone:** run the three commands under **Sign-in password**, **Sign-in token** and **Tailnet certificate** above, then `.\tray.bat --restart` ([README, Remote access](../README.md#remote-access-phone)).
- **Outlook macro:** set `FOLDER_EXPLORER_HOME` to `<repo>` for the user (Settings → Outlook macro prints the exact line) and import `assets\ArchiveMacro.bas` in Outlook.
- **Nightly scan:** a scheduler job that runs `& <repo>\.venv\Scripts\python.exe -m app.cli scan`, with Settings → Scan schedule left `off` so two schedules never double-run.

Check it worked: the required list shows `ok` for `.venv`, `Requirements installed`, `config.json` and `.env`, and the Explore tab shows each root's folders with sizes after the first scan.

## Update

```powershell
cd <repo>
git pull --ff-only
.\tray.bat --restart
```

The page footer and `GET /api/version` show the running build; it should match `git rev-parse --short HEAD`. `tray.bat --restart` exits non-zero when the build it finds is not `HEAD`. Without the tray, stop and start `scripts\start.bat` instead.

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
2. **Stop the old copy**, so no SQLite file is copied mid-write: quit its tray from the tray menu, or stop `scripts\start.bat`'s console. When the new copy is on another PC, nothing may be running on either. If the old copy started at log on, remove its Startup entry now, or it will start again at the next sign-in.
3. **Copy this PC's own files across.** From `<old>` into `<new>`, keeping the relative paths, copy what [What to copy](setup-and-migration.md#what-to-copy) lists: it says which files are required, optional or rebuildable, and what differs on another PC. The ones inside the checkout are gitignored, so nothing tracked is touched. Copy `.env` over a private channel, never by mail or a shared folder.
4. **Top them up.** In `<new>`, run `.\setup.bat -WhatIf` and read what it would change, then `.\setup.bat`. `config.json` gains only the keys it lacks (after a backup); the values you copied stay as they were. On a different PC, also edit the folder paths in `config.json` ([What to edit after copying](setup-and-migration.md#what-to-edit-after-copying)).
5. **Start the new copy.** `.\tray.bat`, and `.\setup.bat -Startup` if it should start at log on. Then set again what does not travel with a copied file (the Outlook macro, the Markdown association, a scheduler job): the second list under [What to edit after copying](setup-and-migration.md#what-to-edit-after-copying).
6. **Verify it.** If any check fails, stop the new copy and start the old one again: it is untouched.
   - `& .\.venv\Scripts\python.exe -m app.cli roots` lists each root and its last scan (none yet on a PC that has not scanned).
   - Open `http://127.0.0.1:8453/`: `GET /api/version` reports the `git_sha` of `git rev-parse --short HEAD` in `<new>`.
   - Settings → Available features shows every block you meant to keep on, and a reason on any that cannot run here.
   - Scans → Rescan (or `.\.venv\Scripts\python.exe -m app.cli scan`) finishes, and Explore shows each root's folders with sizes.
   - With the Email block on: `.\.venv\Scripts\python.exe -m app.cli email status` shows the index's size and last runs, and Search → Emails finds a mail you know.
   - With remote access on: open the tailnet address from another device and sign in.
7. **Retire the old folder, only after you confirm.** Nothing in this playbook or in `setup.bat` deletes the old copy. When you are satisfied, rename it first (for example `<old>.retired`) and run on the new copy for a while; delete it yourself once you are sure. An agent working through this playbook asks you before it deletes anything.

## Uninstall

1. In Settings, switch off **Associate Markdown files with this app** if you turned it on (it removes its own per-user registry entry).
2. Quit the tray from its menu.
3. Remove the log-on shortcut with `.\.venv\Scripts\python.exe -m src.install.startup disable`. It removes only the shortcut `setup.bat -Startup` made, and only when it points at this checkout; an entry you made yourself stays, so delete that one from `shell:startup` by hand.
4. Remove the Outlook macro in Outlook, and the `FOLDER_EXPLORER_HOME` user environment variable, if you set them. Remove any scheduler job that runs the scan.
5. Delete `<repo>`. Keep a copy of the files listed under [What to copy](setup-and-migration.md#what-to-copy) first if you may want them back. The scan history and indexes stay in `<runtime-data>`, so delete that folder too if you don't want to keep them.
