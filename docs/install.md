# Install, update and migrate folder-explorer

folder-explorer installs, updates and migrates the way any app in the toolbox does: clone it and run one `setup.bat`. An update is a pull plus a restart. A migration, onto a fresh clone on the same PC or on another one, is one `setup.bat` call. None of them resets or overwrites what a PC has configured. `setup.bat` runs the vendored installer (`src/installer.py`); what is specific to this app is declared in the `[install]` tables of `.fleet.toml`.

In the commands below, `<repo>` is the folder you cloned into. This page holds the steps; what each setting and state file is, and what each feature block needs, is in [setup-and-migration.md](setup-and-migration.md).

## Install

You need Windows 10 or 11, Git, and Python 3.11 or newer (the `py` launcher, or `python` on `PATH`) with Tk (the tray's screenshot needs it). Optional: Tailscale with HTTPS certificates turned on, to open the app from a phone ([README, Remote access](../README.md#remote-access-phone)); Outlook (classic) for the archive verbs.

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

### Uninstall

1. In Settings, switch off **Associate Markdown files with this app** if you turned it on (it removes its own per-user registry entry).
2. Quit the tray from its menu.
3. Remove the log-on shortcut with `.\.venv\Scripts\python.exe -m src.install.startup disable`. It removes only the shortcut `setup.bat -Startup` made, and only when it points at this checkout; an entry you made yourself stays, so delete that one from `shell:startup` by hand.
4. Remove the Outlook macro in Outlook, and the `FOLDER_EXPLORER_HOME` user environment variable, if you set them. Remove any scheduler job that runs the scan.
5. Delete `<repo>`. Keep a copy of the files listed under [What to copy](setup-and-migration.md#what-to-copy) first if you may want them back. The scan history and indexes stay in the runtime-data folder, so delete that folder too if you don't want to keep them.

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

## Migrate

What moves is declared in `[install.migrate]` of `.fleet.toml`: `config.json`, `.env`, `mailboxes.json`, `config\app_password.json` and `webapp\certificates\`, each only when the old copy has it. The runtime-data folder (`C:\sqlite\folder-explorer\` unless an environment variable names another) holds the scan history and indexes; it stays where it is and both copies share it. `.venv` is never copied. The installer does the whole move, and nothing in it deletes the old copy.

**Reinstall in the same folder (the usual case).** Open a plain PowerShell terminal (not a session started by something that runs from the app's folder) and run these in order, from the folder above the app:

```powershell
cd <parent folder of the checkout>
git -C folder-explorer worktree list            # only the checkout itself: finish and remove any other first
git -C folder-explorer pull --ff-only           # so the current installer is the one that runs
.\folder-explorer\setup.bat -InPlace -WhatIf    # read the backup name, every step and every file it would copy; nothing changes
.\folder-explorer\setup.bat -InPlace
```

It stops every process that runs from the folder (the tray, the web app, and a scan or an archive verb in flight), renames the folder to `folder-explorer.bak-YYYYMMDD-N`, clones the `origin` remote's default branch into the same path, then copies, installs, starts with `tray.bat --restart` and verifies. It passes only when the new clone serves its own commit, and then ends with `in place: [ok] setup.bat -MigrateFrom` and exit code 0. The path is unchanged, so the launcher's app entry, a scheduled scan job, the Startup shortcut and `FOLDER_EXPLORER_HOME` need no edit.

**Beside it (another folder, or an old copy that is not a git clone):**

```powershell
git clone <this repository's URL> <new folder>
cd <new folder>
.\setup.bat -MigrateFrom <old folder> -StopOld
```

Keep `-StopOld` when the old copy is on the same PC: both copies would use one tray lock and one port, so only one can run.

Verify takes the port from the new copy's `config.json` (`port`; 8453 when it sets none) and asks `/api/version` on `127.0.0.1` over HTTPS, accepting the tailnet certificate, then over plain HTTP, so a PC with `remote_access` off or another `port` verifies too. It passes only when the answer is the new clone's commit, from a process that runs from the new clone. A `config.json` that does not parse, or a `port` that is not a TCP port, is `unknown` and fails the run.

Only you can do these steps:

- **Linked worktrees and local work:** a linked git worktree of the old copy points at the path the fresh clone takes over, so `-InPlace` breaks it. Local commits, uncommitted edits and ignored files that are not listed above stay in the backup only.
- **Repoint** what stores the old path, after a `-MigrateFrom` into another folder: the run lists the Startup links, Run keys and scheduled tasks that still point at it. Also set again what it cannot see: `FOLDER_EXPLORER_HOME` for the Outlook macro, **Associate Markdown files with this app** in Settings, and any launcher job that runs the scan.
- **Another PC:** copy the old folder across first and use it as `<old folder>`, without `-StopOld`. Then issue a new certificate and edit the folder paths in `config.json` ([What to edit after copying](setup-and-migration.md#what-to-edit-after-copying)). Runtime data does not travel with the installer ([What to copy](setup-and-migration.md#what-to-copy)).
- **Check the app itself:** Settings → Available features shows every block you meant to keep on; Explore shows each root's folders with sizes; with the Email block on, Search → Emails finds a mail you know; with remote access on, another device can open the tailnet address and sign in.
- **Fall back** if verify fails (the report names the step): stop the new copy, rename the new folder away and the backup to the original name, and start it with `tray.bat`; it is untouched.
- **Retire the backup** once you are sure the new copy works, by deleting it yourself. An agent working through this playbook asks you before it deletes anything.
