# Folder search: matching, ranking and the CLI contract

How Search's **Folders** mode matches a query against the current index, the
one-row-per-folder rule it applies before counting or ranking, and the JSON contract
another app gets with no app running.

## Matching

A query is split on spaces or `;` into words. A word matches a folder when
a word of its full path (case and accents folded) starts with it, so
`alpha budget` finds `...\Alpha\2025\Budget` and `budget` finds `Budgets`;
a word in the root's own path matches every folder in that root. Results
come in four tiers (`src/search_tiers.py`, which the email search shares)
that never interleave:

| Tier | Holds | Within the tier |
| --- | --- | --- |
| `exact` | every word | a word in the folder's own name first, then the shallower folder |
| `partial` | at least half the words | more matched words first, then the score |
| `similar` | at least half the words, counting a near word: one edit off (a letter dropped, added, changed or two swapped, or the start of a word one edit off) for a word of six letters or more, two edits for one of eight or more | the score |
| `closest` | asked only when no root found anything: the 20 folders whose own name words look most like the query's (`rapidfuzz` ratio) | the likeness |

Each result's **score**, 0 to 1, is the mean of its words' scores: 1 for a
word held exactly, `1 - edits / letters` for a near one, 0 for a missing
one; a `closest` result scores how alike the names are. The page labels
every tier below `exact`, and the API and the CLI return each hit's `tier`.

A word with punctuation in it (a number like `017-052`, `q1-2025`,
`cl@ve`) matches a folder whose path holds it as typed. It is looked up
by name too: a part after a separator (`052`) starts a word of the name
holding it, so only the names the folder words give for that part are read.

A word the folder words can't narrow is not looked up by name: one with no
part after a separator (`c#`), one holding a path separator, a single word
naming more than 20,000 folders (`a`), or a punctuated one past its caps
below (`017-2`). It is checked against the paths of the folders the other
words found, or, when no word can be looked up, every word is matched as a
substring of the stored paths, `exact` only. An index built before accent folding is matched that way too,
until the next scan replaces it.

### Speed and the caps

Every tier is read from `folder_words` and the id ranges of the subtrees
under each word's folders, never by a pass over every path, and a tier
runs only while the ones above leave fewer than `limit` results. Each one
has a cap, so a slow PC and a huge index stay interactive:

| Cap | Where | What it bounds |
| --- | --- | --- |
| `MAX_PREFIX_WORDS` 2,000 | `src/index.py` | words read for one query word's word start |
| `MAX_NAMED` 20,000 | `src/index.py` | folders one word may name before it is checked against paths |
| `JOINED_PREFIX_WORDS` 20,000, `JOINED_CANDIDATES` 100,000 | `src/folder_search.py` | words and candidate names read for one part of a punctuated word (one key lookup a name, about 100 ms for 77,000) |
| `MAX_NEAR` 40, `MAX_TWO_EDIT` 40 | `src/index.py`, `src/search_tiers.py` | near words per query word |
| `MAX_VOCABULARY` 1,000,000 | `src/index.py` | words held in memory for the two-edit and closest lookups, read once per open index |
| `CHECK_READS` 50,000 | `src/folder_search.py` | paths read to check a word that can't be looked up by name |
| `CANDIDATES` 5,000 | `src/folder_search.py` | folders ranked per root |
| `CLOSEST_IDS` 20, `CLOSEST_LIMIT` 20 | `src/folder_search.py`, `src/search_tiers.py` | folders per closest word, and closest results |

`tests/search/test_search_tiers.py` holds every tier under 100 ms on a synthetic
index of about 110,000 folders. On a real index of 4.3 million folders and
570,000 words a query answers in 25 to 100 ms, where the former path scan
took about 800 ms; the first `closest` or two-edit lookup on an index also
reads its vocabulary once (about 200 ms there), and the app keeps it for
later queries. On a slower PC the times grow with the caps above, not
with the number of folders.

A folder whose subfolders are named like email addresses (a "folder of
people") shows once in the results, with a count of how many, rather than one
row per address.

## One row per folder

Two roots can reach one folder: a root inside another, a path through a
junction, a share onto a local folder, or a OneDrive library on the drive
that holds it. The folder is listed once, as the synced root's row, else the
first configured root's. The other root's copy is left out of the query
itself, so the result count matches the list. It is worked out once per pair
of roots (`repeat_spans`), never per match: from the resolved paths, then from
the volume serial and file ID, which are never compared across two drive
letters (a cloned disk repeats both). The copy stays when the preferred scan
does not list that folder (not scanned yet, excluded, unreadable) or, for a
search kept to some folders, does not search them. An info log line names the
roots folded, never a path. Two separate copies on disk, such as a library
synced twice into two folders, are two folders and both show.

Three settings live under `folder_search` in `config.json`, all also in
Settings → Display: `skip_levels` (hide the first N path components in the
shown path), `prune_people` (fold a people-named folder's children into one
row) and `onedrive_prevails` (**Show OneDrive folders once**, on by default;
off, a OneDrive folder also shows under the drive that holds it, the one
overlap that stays). An index built before folder search exists asks for a
rescan.

## The CLI contract

Another app gets the same results with no app running:
`& .\.venv\Scripts\python.exe -m app.cli folders search --query "<words>" [--limit N] --json`
prints one JSON document on stdout: `schema_version` 1, and for each result
its full `path`, its shortened `display` (after `skip_levels`), its
`tier` and its `score` from 0 to 1. Exit codes: `0` searched, `1` the query has no words,
`4` no root has a finished scan. Keys and codes live in `app/cli/folders.py`.
