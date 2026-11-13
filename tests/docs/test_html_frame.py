"""An HTML document turned into its sandboxed frame's ``srcdoc``: assets inlined by lookup, links by id, and the remote switch.

``src.docs.html_frame`` reads nothing: the tests hand it the text and fake
lookups, and prove what it rewrites, what it leaves as the author wrote it,
and that the inlined assets stay under one total budget.
"""

from __future__ import annotations

import base64
from typing import Any, Optional

from src.docs import html_frame
from src.docs.html_frame import INLINE_MAX_BYTES, frame

PAGE = """<!doctype html>
<html><head><base href="https://example.invalid/"><title>T</title>
<link rel="stylesheet" href="page.css"><link rel="icon" href="icon.png">
<script src="page.js"></script>
</head><body>
<img alt="Plan" src="diagram.png"><img alt="Far" src="../../outside.png"><img alt="Web" src="https://example.invalid/w.png">
<a href="guide.md#beds">guide</a> <a href="missing.html">missing</a> <a href="https://example.invalid/x">web</a> <a href="#top">top</a>
<script>if (1 < 2 && "</p>") { document.title = 'x'; }</script>
</body></html>
"""

ASSETS = {"page.css": (b".a { color: red }", "text/css"), "page.js": (b"window.x = 1;", "text/javascript"),
          "diagram.png": (b"\x89PNG-invented", "image/png")}


def _link(href: str) -> tuple[Optional[dict[str, Any]], str]:
    if href.startswith("guide.md"):
        return {"root": "R1", "scan_id": 7, "id": 42}, ""
    return None, "Not in the last scan"


def _inline(asked: list[tuple[str, str]]) -> Any:
    def inline(href: str, kind: str) -> tuple[Optional[bytes], str]:
        asked.append((href, kind))
        if href in ASSETS:
            return ASSETS[href]
        return None, "Points outside this folder tree"
    return inline


def _data(data: bytes, mime: str) -> str:
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def test_relative_assets_are_inlined_by_lookup_and_named_by_kind() -> None:
    asked: list[tuple[str, str]] = []
    html = frame(PAGE, _link, _inline(asked), block_remote=False)["html"]
    assert ('page.css', 'css') in asked and ('page.js', 'js') in asked and ('diagram.png', 'image') in asked
    assert ('../../outside.png', 'image') in asked
    # A web address is never looked up, and an icon link is not a stylesheet.
    assert not any(h.startswith("https:") or h == "icon.png" for h, _ in asked)
    for name, (data, mime) in ASSETS.items():
        assert _data(data, mime) in html, name
    assert 'src="https://example.invalid/w.png"' in html
    assert '<img alt="Far">' in html


def test_links_open_a_scanned_document_by_id_and_the_rest_go_inert() -> None:
    html = frame(PAGE, _link, _inline([]), block_remote=False)["html"]
    assert 'href="/doc?root=R1&amp;scan=7&amp;id=42#beds"' in html
    assert '<a title="Not in the last scan">missing</a>' in html
    assert 'href="https://example.invalid/x"' in html and 'href="#top"' in html


def test_the_page_opens_links_in_a_new_tab_and_the_authors_base_is_dropped() -> None:
    html = frame(PAGE, _link, _inline([]), block_remote=False)["html"]
    assert html.startswith('<base target="_blank">')
    assert "example.invalid/\"><title>" not in html and html.count("<base") == 1


def test_script_and_text_stay_as_the_author_wrote_them() -> None:
    html = frame(PAGE, _link, _inline([]), block_remote=False)["html"]
    assert """<script>if (1 < 2 && "</p>") { document.title = 'x'; }</script>""" in html
    assert "<!doctype html>" in html and "</body></html>" in html


def test_remote_content_loads_unless_the_switch_blocks_it() -> None:
    open_html = frame(PAGE, _link, _inline([]), block_remote=False)["html"]
    assert "Content-Security-Policy" not in open_html
    strict = frame(PAGE, _link, _inline([]), block_remote=True)["html"]
    assert strict.startswith('<meta http-equiv="Content-Security-Policy" content="' + html_frame.STRICT_CSP + '">')
    assert "default-src 'none'" in html_frame.STRICT_CSP and "connect-src" not in html_frame.STRICT_CSP
    assert "https:" not in html_frame.STRICT_CSP and "http:" not in html_frame.STRICT_CSP


def test_inlined_assets_stop_at_the_total_budget() -> None:
    big = b"x" * (INLINE_MAX_BYTES // 2 + 1)
    text = '<img alt="a" src="a.png"><img alt="b" src="b.png">'

    def inline(href: str, kind: str) -> tuple[Optional[bytes], str]:
        return big, "image/png"

    html = frame(text, _link, inline, block_remote=False)["html"]
    assert html.count("data:image/png;base64,") == 1
    assert '<img alt="b" title="Too much to inline">' in html


def test_the_answer_is_an_html_document_with_no_outline() -> None:
    body = frame("<p>Hi</p>", _link, _inline([]), block_remote=False)
    assert body["kind"] == "html" and body["outline"] == []
