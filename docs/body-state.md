# Body state

Whether a mail's full message in the Search preview is not asked for, being read, shown or failed is one declared state, `BodyState` in `app/webapp/static/body-state.js`, moved only by `bodyStep`.

## Why it is declared

"Show all" reads the mail on the server, and for an online-only file downloads it first, so it takes seconds and can fail. The preview keeps one entry per mail in `S.body`; each reader of that entry (the button, the busy button, the error block, the frame) used to compare its text, and nothing said which moves were legal. A second click while a load ran was guarded in one place only.

## The states

| State | Meaning | Carries |
| --- | --- | --- |
| `idle` | not asked for yet (the default for a mail with no entry) | nothing |
| `loading` | the body is being read | nothing |
| `ok` | the body arrived | `data` (`text`, and `html` when the mail has it) |
| `error` | the read failed | `error` (the server's code) and `message` |

## The moves

| From | To |
| --- | --- |
| `idle` | `loading` |
| `loading` | `ok`, `error` |
| `error` | `loading` (Try again) |
| `ok` | none: a loaded body never reloads |

`bodyStep(bodies, id, next, detail)` throws on any other move and leaves the entry as it was. `canLoad` answers whether a load may start now, so a second click while `loading` does nothing.

## Adding a reader

Read the entry with `bodyOf` and compare against `BodyState`, never a string. A fact the entry needs goes in the `detail` of the move that sets it. The machine is declared in `.fleet.toml` (`[[state_machines]]`, name `body-state`); `tests/js/body_state.test.mjs` proves an illegal move is refused and `tests/test_body_state_js.py` runs it inside the suite.
