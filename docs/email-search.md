# Email search: matching and ranking

How Search's **Emails** mode matches a query against the email index. The Search tab, the tray's Quick search and `GET /api/v1/search/emails` ([service-api.md](service-api.md)) all answer from the same code (`src/email/search.py`).

## Matching

The index is read-only: this app's own, or another app's named by `email.index_path` (an SQLite file with the `emails` and `emails_fts` tables). Words search subject, sender, recipients and the indexed preview, case and accents ignored. A word is a prefix match from two letters (a single letter matches only itself). `"A phrase"` matches as typed, `-word` excludes, `OR` takes either, and the operators are listed in the README.

## Tiers

Results come in the tiers folder search ranks by (`src/search_tiers.py`, [folder-search.md](folder-search.md)), never interleaved:

| Tier | Holds | Within the tier |
| --- | --- | --- |
| `exact` | every word | `bm25(10, 3, 3, 1)` over subject, sender, recipients and preview, then the newest |
| `partial` | at least half the words | more matched words first, then the score, then relevance |
| `similar` | at least half the words, counting a near word: one edit off (or the start of a word one edit off) for a word of six letters or more, two edits for one of eight or more | the score, then relevance |
| `closest` | listed only when nothing at all was found: the 20 mails holding the words most like the query's (`rapidfuzz` ratio) | the likeness, then relevance |

The **score**, 0 to 1, is the mean of the words' scores: 1 for a word held exactly, `1 - edits / letters` for a near one, 0 for a missing one, so a `partial` mail that also holds a near spelling of the missing word ranks above one that does not. With the **Newest** sort the tiers stay apart and each lists the newest first.

Only the free words are relaxed. Operators (`from:`, `after:` and the others), `-` exclusions and quoted phrases are required in every tier, and an `OR` pair counts as one word. A query of operators and phrases only has nothing to relax, so it can answer with no mail.

The lower tiers are added only when there are fewer `exact` mails than one page (`limit`, 50 in the app): `similar` only while `exact` and `partial` still leave room. Then `total` and the facets count what is listed, and `truncated` says the cap cut the lower tiers (the page reads *Best N results*). A mail's preview marks the words that listed it, near ones included.

## Speed and the caps

Each word is one FTS5 lookup, and near and closest words come from the index's own vocabulary (`fts5vocab`), read once and again only after the index is written to. There is no second index and no pass over the mails' text.

| Cap | Where | What it bounds |
| --- | --- | --- |
| `MAX_WORDS` 30 | `src/email/relaxed.py` | free words counted towards a tier |
| `MAX_FUZZY_WORDS` 8 | `src/email/relaxed.py` | words looked up by near spelling or closest word |
| `MAX_NEAR` 40, `MAX_TWO_EDIT` 40 | `src/index.py`, `src/search_tiers.py` | near words per query word |
| `LOWER_LIMIT` 200 | `src/email/relaxed.py` | `partial` and `similar` mails listed |
| `CLOSEST_MAILS` 200 | `src/email/relaxed.py` | mails one closest word brings |
| `CLOSEST_WORDS` 20, `CLOSEST_LIMIT` 20 | `src/search_tiers.py` | closest words per query word, and closest results |

On a real index of about 18,700 mails and 46,000 words, a query that reaches the lower tiers answers in 6 to 250 ms, within what a broad `exact` query takes there (about 100 to 370 ms, most of it the facets). Reading the vocabulary adds about 60 ms to the first such query after the index changes (about 200 ms on a cold start). On a slower PC the times grow with the caps above.
