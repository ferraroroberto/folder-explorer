"""Ratchet on README.md top-level bullet length: the index rule, checked mechanically.

README.md#Readme-and-docs rule (CLAUDE.md, project-scaffolding docs/readme-and-docs.md):
"The README is an index (what it is, install, run, gate, one line per feature) under
40 KiB; reference moves to docs/<topic>.md." The 40 KiB budget is checked by
scripts/docs_ratchet.py; the *index* rule itself -- one line per feature bullet,
not a reference paragraph -- was not checked mechanically, so individual bullets grow
into full reference paragraphs one feature at a time.

This stage measures every top-level bullet in README.md (a line starting with
"- " at column 0) by word count, keyed by its leading **bold label** (or, with
none, its first few words) so the key survives most edits to the rest of the
bullet. Against a committed baseline, per bullet:

  * a bullet already in the baseline may not grow past its baselined word count
    (a feature bullet regrowing into a reference paragraph, exactly the failure
    mode above);
  * a bullet *not* in the baseline (new since the baseline was written) must fit
    under `max_words` on its own -- a brand-new bullet does not get to start life
    as a reference paragraph.

Existing debt never blocks work (a bullet baselined over `max_words` stays legal
until it grows further); it can only shrink or hold. A bullet line that must stay
long carries `<!-- readme-bullet-exempt: <reason> -->` on the line above it.

Exit codes (a state is never folded into a pass):

  0  pass, every bullet at or under its baseline (or, if new, under `max_words`)
  1  a bullet rose above its baseline, or a new bullet exceeds `max_words` with
     no exemption and no baseline entry; or the baseline file is missing
  2  bad configuration, baseline or arguments -- fail loud
  3  NOT CONFIRMED: README.md is missing or not valid UTF-8

Modes: `check` (default; what the gate runs), `--init` (write the first
baseline, refuses to overwrite), `--update-baseline` (lower or drop entries;
exits 1 and writes nothing while anything rose), `--update-baseline
--allow-additions` (the explicit flag that raises or adds -- shows in review).

Repo-specific (folder-explorer only -- not part of the vendored ratchet family,
not in .fleet.toml [vendored]): the "README is an index" rule is general, but a
per-bullet word-count ratchet keyed by a bold-label heuristic is a light-weight,
repo-owned encoding of it, not (yet) a project-scaffolding primitive.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
DEFAULT_BASELINE = REPO_ROOT / ".readme-index-ratchet-baseline.json"
DEFAULT_MAX_WORDS = 60
BASELINE_VERSION = 1

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

_BULLET = re.compile(r"^- (.*)$")
_BOLD_LABEL = re.compile(r"^\*\*(.+?)\*\*:?")
_MD_PUNCT = re.compile(r"[`*_\[\]()]")
_EXEMPT = re.compile(r"<!--\s*readme-bullet-exempt:\s*\S.*-->")


class ConfigError(RuntimeError):
    """The baseline file is unusable."""


class NotConfirmed(RuntimeError):
    """README.md could not be read."""


@dataclass(frozen=True)
class Bullet:
    key: str
    words: int
    line: int
    text: str


def _key(bullet_text: str) -> str:
    """A label that survives most edits to the rest of the bullet: its leading
    bold span, or, with none, its first few words -- lowercased so a case-only
    edit to the label does not read as a new bullet."""
    bold = _BOLD_LABEL.match(bullet_text)
    if bold:
        return bold.group(1).strip().lower()
    words = _MD_PUNCT.sub("", bullet_text).split()
    return " ".join(words[:6]).lower()


def _word_count(bullet_text: str) -> int:
    return len(_MD_PUNCT.sub(" ", bullet_text).split())


def scan_readme(text: str) -> list[Bullet]:
    """Every top-level bullet in README.md, in order; a line above it holding
    the exemption comment is skipped entirely (never measured, never baselined)."""
    bullets: list[Bullet] = []
    previous = ""
    for no, line in enumerate(text.splitlines(), 1):
        m = _BULLET.match(line)
        if m and not _EXEMPT.search(previous):
            bullets.append(Bullet(_key(m.group(1)), _word_count(m.group(1)), no, m.group(1)))
        if line.strip():
            previous = line
    return bullets


# ---------------------------------------------------------------- baseline

def read_baseline(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read baseline {path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"baseline {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise ConfigError(f"baseline {path}: expected an object with version {BASELINE_VERSION}")
    entries = data.get("entries")
    if not isinstance(entries, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
        for k, v in entries.items()
    ):
        raise ConfigError(f"baseline {path}: `entries` must be a map of key -> positive word count")
    return dict(entries)


def render_baseline(entries: dict[str, int]) -> str:
    lines = [f'    "{k}": {v}' for k, v in sorted(entries.items())]
    body = "{\n" + ",\n".join(lines) + "\n  }" if lines else "{}"
    return f'{{\n  "version": {BASELINE_VERSION},\n  "entries": {body}\n}}\n'


def diff(current: dict[str, int], baseline: dict[str, int], max_words: int) -> list[str]:
    """Keys that rose above their baseline, or are new and over `max_words`."""
    rose = []
    for key, words in current.items():
        base = baseline.get(key)
        if base is None:
            if words > max_words:
                rose.append(key)
        elif words > base:
            rose.append(key)
    return rose


# -------------------------------------------------------------------- CLI

def run(baseline_path: Path, max_words: int, *, init: bool, update: bool, allow_additions: bool) -> int:
    try:
        text = README.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"readme index ratchet: NOT CONFIRMED: cannot read {README}: {exc}")
        return EXIT_NOT_CONFIRMED
    except UnicodeDecodeError as exc:
        print(f"readme index ratchet: NOT CONFIRMED: {README} is not valid UTF-8: {exc}")
        return EXIT_NOT_CONFIRMED

    bullets = scan_readme(text)
    current = {b.key: b.words for b in bullets}

    baseline: dict[str, int] = {}
    if init:
        if baseline_path.exists():
            print(f"readme index ratchet: {baseline_path.name} already exists; --init never overwrites it")
            return EXIT_CONFIG
    elif baseline_path.is_file():
        try:
            baseline = read_baseline(baseline_path)
        except ConfigError as exc:
            print(f"readme index ratchet: BASELINE ERROR: {exc}")
            return EXIT_CONFIG
    else:
        print(f"readme index ratchet: FAIL: baseline {baseline_path.name} is missing -- "
              f"run `python scripts/readme_index_ratchet.py --init`")
        return EXIT_RISE

    if init:
        # runtime-write-exempt: the repo's own baseline file, named by the config, never a code folder
        baseline_path.write_text(render_baseline(current), encoding="utf-8", newline="\n")
        print(f"readme index ratchet: wrote {baseline_path.name} with {len(current)} bullet(s)")
        return EXIT_PASS

    rose = diff(current, baseline, max_words)
    if update:
        if rose and not allow_additions:
            _report(rose, bullets, baseline, max_words)
            print("readme index ratchet: nothing written -- shrink the bullet(s), or raise the "
                  "baseline with --allow-additions")
            return EXIT_RISE
        # runtime-write-exempt: the repo's own baseline file, named by the config, never a code folder
        baseline_path.write_text(render_baseline(current), encoding="utf-8", newline="\n")
        print(f"readme index ratchet: {baseline_path.name} now has {len(current)} bullet(s)")
        return EXIT_PASS

    if rose:
        _report(rose, bullets, baseline, max_words)
        print("  A README feature bullet is an index line, not a reference paragraph; move the "
              "detail to docs/<topic>.md and link it, or (reviewed) raise the baseline with "
              "`--update-baseline --allow-additions`.")
        return EXIT_RISE
    print(f"readme index ratchet: PASS ({len(current)} bullet(s), none above baseline, "
          f"none new over {max_words} words)")
    return EXIT_PASS


def _report(rose: list[str], bullets: list[Bullet], baseline: dict[str, int], max_words: int) -> None:
    by_key = {b.key: b for b in bullets}
    print(f"readme index ratchet: {len(rose)} bullet(s) above the baseline:")
    for key in rose:
        b = by_key[key]
        base = baseline.get(key)
        was = f"baseline {base} word(s)" if base is not None else f"new, over the {max_words}-word cap"
        print(f"  README.md:{b.line}: \"{key}\" -- {b.words} word(s) ({was})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="baseline file (default: repo root)")
    parser.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS,
                        help=f"word cap for a bullet with no baseline entry (default: {DEFAULT_MAX_WORDS})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="write the first baseline; never overwrites")
    mode.add_argument("--update-baseline", action="store_true", help="lower or drop entries (shrink only)")
    parser.add_argument("--allow-additions", action="store_true",
                        help="with --update-baseline: also raise or add entries (shows in review)")
    args = parser.parse_args(argv)
    if args.allow_additions and not args.update_baseline:
        parser.error("--allow-additions only applies with --update-baseline")
    if args.max_words < 1:
        parser.error("--max-words must be a positive integer")
    return run(args.baseline, args.max_words, init=args.init, update=args.update_baseline,
               allow_additions=args.allow_additions)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.exit(main())
