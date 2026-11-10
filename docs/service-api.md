# Service API (v1)

folder-explorer is the fleet's one folder and email search and its one archive. Another app on this PC calls it over HTTP instead of keeping its own index or spawning its CLI. Search comes first; the archive verbs follow it, under [Archive](#archive). The contract below is version 1: a field is only ever added to it, never renamed or removed, while `schema_version` stays 1. `src/search_api.py` owns the states and hit shapes; `tests/test_search_api_v1.py` pins them.

## Calling it

- Base: `http(s)://127.0.0.1:8453` (HTTPS when remote access is on; the certificate names the tailnet host, so a loopback caller skips verification or calls the tailnet name).
- A caller on this PC (loopback, or this PC's own tailnet or LAN address) needs no sign-in. Any other device sends `Authorization: Bearer <token>` from `/api/login`, like the page.
- The search routes are reads: none writes the index, the email database or a user file. The archive routes write, and answer a caller on this PC only.
- folder-explorer down shows as a failed request (connection refused, timeout). A search answer always says which case it is through `state`; an empty list with `state: "ok"` really is "no match".

## The search envelope

Every answer is one JSON object that starts with:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `state` | `ok`, `not-configured`, `indexing` or `error` |
| `reason` | why the state isn't `ok` (plain text for a person), else `null` |

| `state` | HTTP | When |
| --- | --- | --- |
| `ok` | 200 | the part searched; no match is still `ok`, with an empty list |
| `not-configured` | 503 | this PC can't search that part: no roots, no finished scan yet, no email folders set, no email index |
| `indexing` | 503 | the part has nothing to search yet and its first scan or email index is being built |
| `error` | 500 | the index is there but couldn't be read; `reason` carries the error |

A query parameter out of range answers 422 (FastAPI's validation body, no envelope). A non-`ok` answer still carries the route's own fields, empty.

## `GET /api/v1/search/status`

Each part's state, for a settings card or a health line. Always `state: "ok"` (the route itself answered).

```json
{"schema_version": 1, "state": "ok", "reason": null,
 "folders": {"state": "ok", "reason": null, "roots": 3, "scanned": 3, "scanning": false},
 "emails": {"state": "ok", "reason": null, "count": 1234}}
```

`roots` is the roots set up, `scanned` those with a finished scan, `scanning` whether a scan runs now (a scan in progress still answers from the last finished index). `count` is the mails in the email index.

## `GET /api/v1/search/folders`

| Parameter | Default | |
| --- | --- | --- |
| `q` | `""` | words, split on spaces or `;`; every word must appear in the folder's full path. No words answers `ok` with no results. |
| `limit` | 30 | 1 to 200 |
| `within` | none | a folder path; repeat it for several. Only those folders and everything below them are searched (a root's own path keeps the whole root). It is also the fast path: a scoped search reads an id range, not every folder on every drive. |

Matching is the Search tab's: case and accents never matter, a word of six letters or more one typo off still matches below the exact hits, a folder of people (subfolders named like email addresses) folds into one result (`folder_search.prune_people`), and a OneDrive folder is listed once (`folder_search.onedrive_prevails`).

```json
{"schema_version": 1, "state": "ok", "reason": null, "query": "alpha budget",
 "results": [{"root": "d", "root_name": "D:", "kind": "local", "id": 812, "name": "Budget",
              "path": "D:\\Projects\\Alpha\\Budget", "depth": 3, "score": 1.0, "size": 48000, "files": 4, "people": 0}],
 "total": 1, "truncated": false, "needs_rescan": [], "missing": [], "page": "/#search?mode=folders&q=alpha%20budget"}
```

| Result field | |
| --- | --- |
| `root`, `root_name`, `kind` | the scan root's key, display name and kind (`local` or `synced`) |
| `id` | the folder's id in that root's current index (changes with each scan) |
| `name`, `path` | the folder's own name and its full path on disk; a caller folds `path` onto its own placeholders |
| `depth` | levels below the root (the root is 0) |
| `score` | 0 to 1: 1 when every word matched exactly, lower for a near word |
| `size`, `files` | bytes and file count under the folder at the last scan |
| `people` | for a folder of people, how many people folders it holds, else 0 |

`total` counts every match before `limit`; `truncated` says a cap cut the list. `needs_rescan` names roots whose index predates folder search. `missing` lists the `within` paths no scanned root holds. `page` is this app's Search tab for the same words, relative to its address.

## `GET /api/v1/search/emails`

| Parameter | Default | |
| --- | --- | --- |
| `q` | `""` | the Search tab's query: words (prefix matches from two letters, a single letter matches only itself), `"phrases"`, `-word`, `OR`, and the operators in the README. Empty lists the newest mails. |
| `sort` | `relevance` | or `newest` |
| `offset` | 0 | for the next page |
| `limit` | 30 | 1 to 200 |
| `facets` | `false` | `true` adds the sender, folder and year counts on the first page; they cost most of a broad query's time |

Relevance is `bm25(emails_fts, 10, 3, 3, 1)` (subject, sender, recipients, preview); ties go to the newest mail.

```json
{"schema_version": 1, "state": "ok", "reason": null, "query": "budget",
 "hits": [{"id": 17, "subject": "Q4 \ue000budget\ue001 draft", "from_name": "Ana Lopez", "from_addr": "ana@example.com",
           "recipients": "leo@example.org", "date": "2025-10-14T09:12:00+02:00", "folder": "D:\\Mail\\Clients\\Northwind",
           "path": "D:\\Mail\\Clients\\Northwind\\001 - Q4 budget draft.msg", "filename": "001 - Q4 budget draft.msg",
           "snippet": "Here is the revised \ue000budget\ue001 for…", "flagged": true, "attachments": 2, "size": 412000}],
 "total": 3, "notes": [], "facets": null, "marks": {"start": "\ue000", "end": "\ue001"},
 "page": "/#search?mode=emails&q=budget"}
```

| Hit field | |
| --- | --- |
| `id` | the mail's row in the email index |
| `subject`, `snippet` | with each match wrapped in `marks.start` / `marks.end` (private-use characters no mail contains): escape the text first, then turn the marks into highlights |
| `from_name`, `from_addr`, `recipients`, `date` | as indexed; `date` is ISO 8601 |
| `folder`, `path`, `filename` | where the `.msg` sits on disk |
| `flagged` | the mail was flagged in Outlook |
| `attachments`, `size` | the attachment count and bytes, or `null` on an index without those fields |

`notes` lists operators the index has no field for (left out, never faked) and a query the index refused. A caller wanting a mail's full text or an Outlook action uses this app's page, through `page`.


## Archive

`src/archive_api.py` owns the outcomes and the envelope; `tests/archive/api/test_archive_api_v1.py` pins them.

Each route runs the CLI's own verb (`python -m app.cli email batch <verb>`, or `email index`) as a windowless child with a timeout, so the semantics are the CLI's (`app/cli/email_batch.py`) and an Outlook dialog hangs that child, never the server. One Outlook verb runs at a time; a second call while one runs answers `busy`. The routes answer a caller on this PC only (anything else is `not_this_pc`, 403), and an instance started with `FOLDER_EXPLORER_OUTLOOK=off` refuses them (`outlook_off`, 503). Bodies are JSON; an unknown field or a value out of range answers 422 (FastAPI's validation body, no envelope) before any child runs.

### The answer

Every route answers one object:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `verb` | `plan`, `apply`, `revert` or `index` |
| `outcome` | `done`, `not-found`, `refused` or `error` |
| `code` | why the outcome is not `done`, else `null`: the archiver's own error code (`bad_input`, `mailbox_unknown`, `outlook_unavailable`, `run_failed`, a mail's `not_in_inbox`, …) or this API's (below) |
| `reason` | the same in plain text, else `null` |
| `acknowledged` | the archiver answered with its document, so the call was received and ran to its end |
| `confirmed` | `apply` / `revert`: the `message_id` of every mail whose own result is `ok` |
| `failed` | `apply` / `revert`: `{message_id, code}` of every mail that is not |
| `result` | the archiver's own document, unchanged (`mails`, `results`, `renumbered`, …), or `null` when it gave none |

Acknowledged and confirmed are separate on purpose. `acknowledged: true` says the verb ran; a mail is filed only when it is in `confirmed`. `acknowledged: false` (`timeout`, `no_answer`, `spawn_failed`, `schema_mismatch`) says nobody can tell what was written: `plan` again before retrying, never assume either way. After an `apply`, `POST /api/v1/archive/index` for each folder filed into is what confirms the email index holds the new mails.

| `outcome` | HTTP | When |
| --- | --- | --- |
| `done` | 200 | the verb ran and every mail in it is confirmed (a `plan` is `done` when it listed) |
| `not-found` | 404 | what the call named is not there: an unknown mailbox (`mailbox_unknown`, `mailbox_not_in_outlook`), a `plan` that named only mails the folder does not hold, an `apply` / `revert` whose every mail is not where the verb looks (`not_in_inbox`, `not_in_sent`, `not_in_archive_folder`) |
| `refused` | 422 / 503 / 409 / 403 | declined before anything was touched: `bad_input` (422), the archiver cannot start (`config_missing`, `mailbox_registry_invalid`, `outlook_unavailable`, `com_unavailable`, `outlook_off`: 503), `busy` (409), `not_this_pc` (403) |
| `error` | 207 / 502 / 504 / 500 | it ran and did not end well: `partial` (207, some mails confirmed, the rest in `failed`), `failed` (502, none confirmed), `timeout` (504), `no_answer` / `spawn_failed` / `schema_mismatch` (502), `run_failed` or an unknown code (500, the walk broke part-way: mails may be filed, plan again) |

### `POST /api/v1/archive/plan`

Ranks archive folders for the mails of the Inbox, or of Sent Items. `result` is the CLI's plan document.

| Field | Default | |
| --- | --- | --- |
| `candidates` | 10 | ranked folders per mail, 0 to 100; 0 lists the mails with no ranking |
| `message_ids` | `[]` | plan only these mails, looked up by Internet Message-ID |
| `since` | none | local time, `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` |
| `search` | `[]` | text the subject, sender, recipients or preview must hold; all must match |
| `ref` | none | only the mail stamped with this `X-Archive-Ref` |
| `folder` | `inbox` | or `sent` |
| `mailbox` | registry default | an alias of `mailboxes.json` |

### `POST /api/v1/archive/apply`

Files each decided mail: writes its `.msg` into the folder, moves it out of the Inbox and tags it. Body: `decisions` (the CLI's list of `{message_id, folder_path, date_prefix: true|false|"auto", source?}`; a `folder_path` outside the email folders fails that mail as `bad_decision` and writes nothing), `renumber` (default `false`: renumber each folder written into and report the old-to-new map under `result.renumbered`), `category`, `mailbox`.

### `POST /api/v1/archive/revert`

Undoes an apply: deletes the files and puts each mail back in the Inbox. Body: `items` (`[{message_id, files}]`; an `apply` result's `results` works as is), `renumber`, `category` (the one the apply used), `mailbox`.

### `POST /api/v1/archive/index`

Re-indexes one folder inside the email folders (`email index --root`), so the email index confirms what an `apply` filed there. It takes the email lock only, never waiting behind a disk scan. Body: `root` (a folder path), `wait` (seconds to wait for another email run, default 60, at most 600). `result` is `null`: the outcome is the answer. `done` means indexed; `busy` (409) another email run held the lock past `wait`; `not_in_email_folder` (422) the folder is outside the email folders; `email_index_not_set_up` (503); `failed` (500) the re-index broke.
