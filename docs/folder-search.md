# Folder search: matching, ranking and the CLI contract

How Search's **Folders** mode matches a query against the current index, the
OneDrive dedupe it applies before counting or ranking, and the JSON contract
another app gets with no app running.

## Matching

A folder matches when its full path (case and accents folded) holds every
word of the query, split on spaces or `;` (`alpha budget` finds
`...\Alpha\2025\Budget`). A word of six letters or more that is one typo off
(`src/folder_match.py`) still finds the folder, ranked below the exact hits.
Each result's **score**, 0 to 1, is the share of query words matched exactly;
100% (score 1) is every word exact. An index built before accent folding
matches words as typed, with no near words, until the next scan replaces it.

A folder whose subfolders are named like email addresses (a "folder of
people") shows once in the results, with a count of how many, rather than one
row per address.

## OneDrive dedupe

A folder inside a scanned OneDrive root is also on the drive that holds it,
so without dedupe every match would show twice. **Show OneDrive folders once**
(Settings → Display, on by default) lists it only as the OneDrive row: the
drive's copy is left out of the query itself, so the result count matches the
list, and a library with no finished scan still keeps the drive's copy. Three
settings live under `folder_search` in `config.json`, all also in Settings →
Display: `skip_levels` (hide the first N path components in the shown path),
`prune_people` (fold a people-named folder's children into one row) and
`onedrive_prevails` (this dedupe). An index built before folder search exists
asks for a rescan.

## The CLI contract

Another app gets the same results with no app running:
`& .\.venv\Scripts\python.exe -m app.cli folders search --query "<words>" [--limit N] --json`
prints one JSON document on stdout: `schema_version` 1, and for each result
its full `path`, its shortened `display` (after `skip_levels`) and its
`score` from 0 to 1. Exit codes: `0` searched, `1` the query has no words,
`4` no root has a finished scan. Keys and codes live in `app/cli/folders.py`.
