"""Tiered folder search: a wrong, misspelled or nonsense word still finds folders, exact hits first, fast on a large index."""

from __future__ import annotations

import ntpath
import random
import statistics
import time
from pathlib import Path
from typing import Iterator

import pytest

from src import search_tiers as tiers
from src import winfs
from src.folder_search import search
from src.index import Index, open_index
from src.roots import Root
from src.scanner import scan_to_index

# Invented folders: a project tree, a long word for two-letter typos, and look-alike names.
FOLDERS = (
    "Projects/Alpha/2025/Budget",
    "Projects/Alpha/Notes",
    "Projects/Kestrel/Budget",
    "Archive/Presupuestos 2025",
    "Archive/Correspondence",
    "Archive/Photos",
)


def scanned(tmp_path: Path) -> tuple[Root, Index]:
    base = tmp_path / "Invented drive"
    for rel in FOLDERS:
        (base / rel).mkdir(parents=True)
    index_path = tmp_path / "root.sqlite3"
    scan_to_index(str(base), index_path, root_name="Invented drive")
    return Root("inv", "Invented drive", str(base), "local"), Index(open_index(index_path))


def tiered(root: Root, ix: Index, query: str) -> list[tuple[str, str]]:
    return [(r["name"], r["tier"]) for r in search([(root, ix)], query, prune=True, skip=0)["results"]]


def test_the_tier_rules() -> None:
    assert tiers.tier_of(3, 0, 3) == "exact"
    assert tiers.tier_of(2, 0, 3) == "partial" and tiers.tier_of(1, 0, 2) == "partial"
    assert tiers.tier_of(1, 1, 3) == "similar" and tiers.tier_of(0, 1, 1) == "similar"
    assert tiers.tier_of(0, 0, 1) is None and tiers.tier_of(1, 0, 4) is None
    assert tiers.edit_score("budget", 1) == 0.833 and tiers.edit_score("presupuesto", 2) == 0.818
    assert tiers.two_edit_words("prezupeusto", {11: ["presupuesto", "corresponde"]}) == ["presupuesto"]
    assert tiers.two_edit_words("budgte", {6: ["budget"]}) == []


def test_a_wrong_extra_word_still_lists_the_folders_holding_the_others(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    # Two of the three words are at least half: only the folder holding both alpha and budget qualifies.
    assert tiered(root, ix, "alpha budget wrongword") == [("Budget", "partial")]
    # One of two is half: every folder holding either word, those holding the word in their own name first.
    found = tiered(root, ix, "budget wrongword")
    assert {t for _, t in found} == {"partial"} and [n for n, _ in found][:2] == ["Budget", "Budget"]


def test_a_one_or_two_letter_typo_finds_the_folder(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    assert tiered(root, ix, "corespondence") == [("Correspondence", "similar")]
    assert tiered(root, ix, "corespondnece") == [("Correspondence", "similar")]
    assert tiered(root, ix, "presupeusto")[0] == ("Presupuestos 2025", "similar")


def test_a_query_matching_nothing_lists_the_closest_folders(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    result = search([(root, ix)], "fotoz", prune=True, skip=0)
    assert result["results"][0]["name"] == "Photos"
    assert {r["tier"] for r in result["results"]} == {"closest"}
    assert 0 < len(result["results"]) <= tiers.CLOSEST_LIMIT and result["total"] == len(result["results"])


def test_exact_hits_rank_above_every_other_tier(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    found = tiered(root, ix, "budget kestrel")
    assert found[0] == ("Budget", "exact")
    order = [tiers.rank(t) for _, t in found]
    assert order == sorted(order) and order.count(0) == 1 and len(order) > 1


def test_a_word_start_matches_and_a_root_word_matches_everything(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    assert tiered(root, ix, "presupuesto")[0] == ("Presupuestos 2025", "exact")
    # The root's own path holds "invented", so every folder holds it and the one holding both leads.
    found = tiered(root, ix, "invented photos")
    assert found[0] == ("Photos", "exact") and {t for _, t in found[1:]} == {"partial"}
    assert len(found) == len(FOLDERS) + 6


# The synthetic index: about 110,000 folders named from 20,000 invented words, and the caps it must stay inside.
SYNTH_TOP, SYNTH_MID, SYNTH_LEAF = 20, 60, 90
SYNTH_WORDS = 20_000
# A full four-tier query on this PC; docs/folder-search.md gives the caps that bound it on a slower one.
BUDGET_MS = 100


def _synthetic_lister(base: str) -> tuple["winfs.Lister", list[str]]:
    rng = random.Random(330)
    syllables = ["ka", "ri", "mo", "tel", "nor", "vis", "da", "lu", "pre", "sto", "ber", "qua", "zen", "fi", "ol"]
    words = sorted({"".join(rng.choice(syllables) for _ in range(rng.randint(2, 5))) for _ in range(SYNTH_WORDS * 2)})
    words = words[:SYNTH_WORDS]
    children: dict[str, list[str]] = {}

    def names(count: int) -> list[str]:
        return sorted({" ".join(rng.sample(words, 2)) + " " + str(rng.randint(2000, 2030)) for _ in range(count)})

    children[base] = names(SYNTH_TOP)
    for top in children[base]:
        top_path = ntpath.join(base, top)
        children[top_path] = names(SYNTH_MID)
        for mid in children[top_path]:
            children[ntpath.join(top_path, mid)] = names(SYNTH_LEAF)

    def lister(path: str) -> Iterator[winfs.Entry]:
        for name in children.get(path, ()):
            yield winfs.Entry(name, True, 0, 0, winfs.FILE_ATTRIBUTE_DIRECTORY, 0, 0)

    return lister, words


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> tuple[Root, Path, list[str]]:
    base = "Q:\\Synthetic"
    lister, words = _synthetic_lister(base)
    index_path = tmp_path_factory.mktemp("synthetic") / "root.sqlite3"
    scan_to_index(base, index_path, root_name="Synthetic", lister=lister)
    return Root("syn", "Synthetic", base, "local"), index_path, words


def _median_ms(root: Root, index_path: Path, query: str) -> tuple[float, dict]:
    """The median of three runs, each on a freshly opened index so its vocabulary is read every time."""
    times, result = [], {}
    for _ in range(3):
        ix = Index(open_index(index_path))
        start = time.perf_counter()
        result = search([(root, ix)], query, prune=True, skip=0)
        times.append((time.perf_counter() - start) * 1000)
        ix.conn.close()
    return statistics.median(times), result


def test_every_tier_stays_inside_its_budget_on_a_large_index(synthetic: tuple[Root, Path, list[str]]) -> None:
    root, index_path, words = synthetic
    long_word = next(w for w in words if len(w) >= 10)
    typo = long_word[:2] + long_word[3] + long_word[2] + long_word[4:-1]
    ix = Index(open_index(index_path))
    named = ix.folder_rows([5000])[0]["name"].split()
    ix.conn.close()
    queries = {
        "exact": words[100],
        "partial": named[0] + " " + named[1] + " zzzqqq",
        "similar": typo,
        "closest": "xyxyxy wvwvwv",
    }
    for want, query in queries.items():
        ms, result = _median_ms(root, index_path, query)
        assert result["results"], query
        assert result["results"][0]["tier"] == want, (query, result["results"][0])
        assert ms < BUDGET_MS, "%s query %r took %.0f ms" % (want, query, ms)
