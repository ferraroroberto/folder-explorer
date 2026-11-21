# Install and update

One command sets folder-explorer up on a fresh Windows PC, and the same command is safe on a PC that already runs it: it creates what is missing, adds only what a newer version needs, and never overwrites a setting. After that, an update is `git pull` plus a restart.

In the commands below, `<repo>` is the folder you cloned into.

## A fresh PC

You need Windows, Git and Python 3.11 or newer (from python.org, with the `py` launcher or `python` on `PATH`).

1. Clone and install:

   ```powershell
   git clone <this repository's URL> folder-explorer
   cd folder-explorer
   .\scripts\install.bat
   ```

   To see first what it would do, run `.\scripts\install.bat -WhatIf`: it prints every change and makes none.

2. Read the lines it prints. Each step says `created`, `added keys`, `installed`, or `unchanged` / `kept` when there was nothing to do. A `[FAIL]` line names what went wrong, and the run exits 1.
3. Read the checklist at the end. Each row is `ok`, `missing` or `unknown`, and a `missing` row names the command that fixes it:
   - **sign-in password**, **sign-in token**, **tailnet certificate**: needed only to open the app from a phone over the tailnet (README, Remote access). This PC uses the app without them.
   - **start at sign-in**: optional. `.\scripts\install.bat --startup` adds a shortcut to `tray.bat` in your Startup folder. Skip it if something else already starts the tray.
   - **app running**: start it with `tray.bat` (the tray) or `start.bat` (a console), then open `http://127.0.0.1:8453/`.
4. Set the rest in the app, under Settings: the folders to scan, the email folders, and **Available features** (switch off a block this PC cannot run). Then press **Rescan** in the Scans tab. [setup-and-migration.md](setup-and-migration.md) has the details for each setting and what to copy from another PC.

Check it worked: the installer's own lines end with `Result:`, the checklist shows `ok` for `.venv`, `requirements` and `config`, and the Explore tab shows each root's folders with sizes after the first scan.

## A PC that already runs it

```powershell
cd <repo>
git pull --ff-only
.\scripts\install.bat -WhatIf   # optional: what a newer version would add
.\scripts\install.bat
tray.bat --restart
```

The install step is needed only when a pull brings a new requirement or a new config key, and running it when nothing changed is harmless (it reports `unchanged`). Otherwise `git pull --ff-only` and `tray.bat --restart` are the whole update: every file the app, the tray, a restart or the gate writes in the checkout is gitignored, so the pull never meets a local change.

What the installer does to an existing PC's files:

- **`config.json`** (or the file `FOLDER_EXPLORER_CONFIG` names): every existing value stays as it is. Keys that `config.example.json` has and the file lacks are added, after a copy named `config.json.<date>-<time>.bak` beside it, and the run prints each added key. The added values are the app's defaults, so the app behaves the same afterwards. A file that does not parse is left untouched and reported, and you fix it by hand.
- **`.env`**: created from `.env.example` only when missing. An existing one is never read or changed.
- **`mailboxes.json`**: never created or changed. The app runs without it (it uses the one Outlook mailbox), and `mailboxes.sample.json` shows its shape for when you need more than one.
- **`.venv`**: kept. Requirements are installed into it only when one is missing or out of date.
- **Startup shortcut**: only with `--startup`, and only when no Startup entry already starts this checkout's `tray.bat`. An entry you made yourself is kept.

`tray.bat --restart` uses the shared tray helper when the PC has one, and otherwise its own standalone restart (`scripts/standalone_restart.py`), which stops only this checkout's tray and server and checks that the new server reports the pulled build. Either way it exits non-zero when the build it finds is not `HEAD`.

## A PC with an AllSigned execution policy

Nothing changes. `tray.bat` and the gate load their PowerShell as text, which an AllSigned or Restricted policy allows without being weakened, and no tracked script carries a signature, so `git pull` never conflicts with one. Run the gate on such a PC through the launcher:

```powershell
& .\.venv\Scripts\python.exe -m src.ps_script scripts\verify-before-ship.ps1
```

## Uninstall

1. In Settings, switch off **Associate Markdown files with this app** if you turned it on (it removes its own per-user registry entry).
2. Quit the tray from its menu.
3. `.\scripts\install.bat --uninstall` removes the Startup shortcut, but only if this installer made it and it points at this checkout. Run it with `-WhatIf` first to see what it would remove.
4. Delete `<repo>`. The scan history and indexes stay in the runtime-data folder (README, Scans), so delete that folder too if you don't want to keep them.
