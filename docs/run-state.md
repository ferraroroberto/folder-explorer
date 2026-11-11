# Run state

Whether a disk scan or an email-stage run is running, finished, failed or died is one declared state, `RunState` in `src/run_state.py`, derived in one place by `derive_run_state`.

## Why it is declared

A run leaves two traces that can disagree:

- the `status` of its row in `state.sqlite3` (`scans`, `email_runs`), written by the run itself;
- the OS lock its process holds while it works (`scan.lock`, `email.lock`), which the operating system drops when the process exits, crashed or not.

A process that dies leaves a `running` row and no lock. Readers that map the two on their own disagree: one route answers `scanning: false` beside a `running` row, the Scans tab shows "Indexing now" next to an enabled "Index emails now" button, and the v1 search status folds a failed first scan into "no root has a finished scan yet". A reader never compares a row's `status` itself; it calls the derive function, so every screen and route gives the same answer.

## The states

| State | Meaning | Reason |
| --- | --- | --- |
| `idle` | nothing has run yet and no run holds the lock | none |
| `running` | a run holds its lock and is working | none |
| `ok` | the newest run finished and its result is in place | none |
| `failed` | the newest run ended with an error | the error text |
| `interrupted` | the process died before the newest run finished | why it stopped (the settled row's message) |
| `unknown` | the lock could not be read, so a running run cannot be told from a dead one; also a row whose status this app never writes | what could not be read |

The set is closed. A row's `status` column stores only `running`, `ok`, `failed` and `interrupted`; `idle` and `unknown` exist only as derived answers.

## The observation and the derivation

`derive_run_state(row_status, row_message, lock_held, any_run=False)` takes one observation and returns the state and its reason:

- `row_status` / `row_message`: the row's stored text, `None` before any row.
- `lock_held`: `True` held, `False` free, `None` when the lock file could not be read (`scan_lock.probe_lock`). An unreadable lock is never folded into free.
- `any_run`: the question is whether any run is active, not how this row ended. A held lock is then `running` whatever the newest row says (between two roots, or after the lock is taken and before the run writes its row), and an unreadable lock is `unknown` even over a settled row.

| Row | Lock held | Lock free | Lock unreadable |
| --- | --- | --- | --- |
| none | `running` | `idle` | `unknown` |
| `running` | `running` | `interrupted` | `unknown` |
| `ok` | `ok` (`running` with `any_run`) | `ok` | `ok` (`unknown` with `any_run`) |
| `failed` | `failed` (`running` with `any_run`) | `failed` | `failed` (`unknown` with `any_run`) |
| `interrupted` | `interrupted` (`running` with `any_run`) | `interrupted` | `interrupted` (`unknown` with `any_run`) |

Settling a dead `running` row to `interrupted` in the database (`store.settle_interrupted`, `store.settle_email_runs`) is housekeeping that still happens at the start of the next run and on `GET /api/roots`; the derived answer does not wait for it.

## Who reads it

| Reader | Where | What it adds |
| --- | --- | --- |
| `GET /api/scan` | `app/webapp/routers/scans.py` via `scan_runner.scan_activity` | `state`, `reason`; `scanning` is `state == running`, `running` lists rows only then |
| `GET /api/roots` | `describe_root` | `state` and `reason` for the scan and for each root; `running` / `failed` stay, derived from the same state |
| `GET /api/email/status` | `app/webapp/routers/emails.py` via `indexer.email_state` / `indexer.history` | `state`, `reason`, and a `state` / `reason` / `live` / `attention` on each run (`attention` is failed, interrupted or unknown), so the Scans tab marks a run without naming a state |
| `POST /api/email/index` | the same router | 409 while running, 503 when the lock cannot be read |
| `GET /api/v1/search/status`, `/folders`, `/emails` | `search_api.folders_state` / `emails_state` | the `run` state beside each part, and the `indexing` / failed reasons ([service-api.md](service-api.md)) |
| Scans and Explore tabs | `scans.js`, `email-scans.js`, `main.js` | read the fields above; an `unknown` state disables the Rescan and Index buttons |

The first email build is the one place the state decides more than "is it running": this app's own email index file exists from the start of its first run, so search is `indexing` until a run has finished `ok`, `not-configured` with a reason when the only runs so far failed or stopped, and trusted as it is when no run was ever recorded (an imported index) or the index belongs to another app.

## Adding a reader

Call `scan_runner.scan_state` / `indexer.email_state` (or `derive_run_state` for a row you hold), never compare a row's `status` or a lock result yourself. When a fix needs a fact the state does not carry, add it to the observation and to `derive_run_state`, with a row in `tests/test_run_state.py`. The machine is declared in `.fleet.toml` (`[[state_machines]]`, name `run-state`) and the conventions ratchet's state rules report any other module that re-derives it.
