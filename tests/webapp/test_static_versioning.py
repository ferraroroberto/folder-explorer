"""The shell page's include fragments are spliced into the served document."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.static_versioning import expand_includes


def test_include_is_replaced_by_the_fragment(tmp_path: Path) -> None:
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "part.html").write_text("<svg>\n  <symbol/>\n</svg>\n", encoding="utf-8")
    body = "<body>\n<!--include parts/part.html-->\n<nav></nav>\n</body>"
    assert expand_includes(body, tmp_path) == "<body>\n<svg>\n  <symbol/>\n</svg>\n<nav></nav>\n</body>"


def test_a_page_without_a_directive_is_unchanged(tmp_path: Path) -> None:
    assert expand_includes("<p>hi</p>", tmp_path) == "<p>hi</p>"


def test_a_missing_fragment_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        expand_includes("<!--include gone.html-->", tmp_path)


def test_a_path_is_not_an_include(tmp_path: Path) -> None:
    body = "<!--include ../secret.html-->"
    assert expand_includes(body, tmp_path) == body


def test_the_real_index_serves_its_sprite_inline() -> None:
    static = Path(__file__).resolve().parents[2] / "app" / "webapp" / "static"
    html = expand_includes((static / "index.html").read_text(encoding="utf-8"), static)
    assert "<!--include" not in html
    assert html.count('<symbol id="i-folder"') == 1
    assert html.index("<symbol") < html.index('<nav class="tabs"')
