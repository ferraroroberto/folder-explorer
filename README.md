# folder-explorer

Where the space on your drives goes. A Windows web app that scans folders by their listings only, then shows a folder tree with sizes and shares, the largest files, the top percentile of files and a breakdown by file type. OneDrive files that live only in the cloud are counted both ways: **Size** (what the cloud stores) and **On disk** (what this PC holds), with a toggle for which one drives the totals. A scan never opens a file, so it never downloads one.

FastAPI + a static PWA, a Windows tray, and the fleet design system. Read-only: it never moves, deletes or opens your files.

## Install

Python 3.11+ on Windows.

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-tray.txt
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

The default roots are every local fixed drive plus each OneDrive folder, deduplicated by path. Set `roots` in `config.json` (a list of folders) or edit them in Settings. Three ways to scan, all under one lock so two scans never overlap:

- **Rescan** in the Scans tab (one root or all), or **Rescan now** in the tray menu.
- **In-app schedule:** Settings → Scan schedule (`off`, `nightly` at `schedule_time`, `hourly`). Off by default; it runs only while the app runs, so it suits a PC without app-launcher.
- **CLI:** `& .\.venv\Scripts\python.exe -m app.cli scan` (all roots) or `... scan --root c`; `python -m app.cli roots` lists the roots and their last scan. Exit 0 ok, 1 a root failed, 2 another scan is running, 3 unknown root.

On this PC the nightly scan is an app-launcher Job that runs the CLI, so the in-app schedule stays off. How a scan reads sizes without downloading anything, and the index it writes: [docs/scanning.md](docs/scanning.md).

Scan output (the index, the scan history, logs, the usage log) lives in the runtime-data folder, `C:\sqlite\folder-explorer\` by default (`FLEET_DATA_ROOT` or `FOLDER_EXPLORER_DATA_DIR` move it), never in the repo.

## Features

- **Explore:** the root's totals, a folder tree that expands level by level (heaviest first, with share of parent, size and file count), and the selected folder's contents: its own files and folders, or every nested file.
- **Largest:** the 10 largest files under the selected folder, and how much of it the largest 1, 5 or 10 percent of files hold, grouped by subfolder.
- **Types:** bytes, count and share per extension; tap a type to filter every tab to it.
- **Scans:** each root's last scan, live progress, and Rescan.
- **Size / On disk** toggle for every total; online-only files carry a cloud mark.
- Phone and desktop layouts, light and dark, text size, and the fleet's bottom-tab navigation; the build line in the footer.

## Configuration

`config.json` (gitignored; `config.example.json` lists every key): `remote_access`, `port` (8453), `roots` (`null` = every drive + OneDrive), `schedule`, `schedule_time`. Secrets live in `.env` (`.env.example`).

## Verification gate

```powershell
powershell -File scripts\verify-before-ship.ps1
```

Byte-compile, the unit tests, the browser suite (Chromium desktop and WebKit iPhone against a disposable instance over an invented tree), and the ten fleet ratchets. Needs `requirements-dev.txt` and Node (`npx`) for the static ratchet.

## Layout

Where code goes: [docs/layout.md](docs/layout.md). Agent instructions: [CLAUDE.md](CLAUDE.md).

## License

MIT, see [LICENSE](LICENSE).
