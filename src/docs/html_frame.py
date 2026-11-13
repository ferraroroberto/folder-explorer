"""An HTML document as its sandboxed frame's ``srcdoc``: the files beside it inlined, links to scanned documents by id.

Reads nothing from disk: the caller hands over the text and two lookups that
answer from the scan index, one for a link and one for an asset's bytes (a
stylesheet, script or image beside the page, read through
``src/docs/reader.py`` and never downloaded). The page renders as the author
wrote it, scripts included; what keeps it off the app is where the page draws
it, a frame sandboxed without ``allow-same-origin``, and the sign-in gate's
refusal of an ``Origin: null`` request. The one rewrite of the author's markup
is in the start tags that name a file: a relative stylesheet, script or image
becomes a ``data:`` address, a relative link to a scanned Markdown or HTML file
opens it in the ``/doc`` window by id, and any other relative link goes inert.
With "Block remote content in HTML" on, a policy at the top of the frame
loads nothing from the web.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from html import escape
from html.parser import HTMLParser
from typing import Any, Optional

from src.docs.locate import doc_address

# Every inlined asset of one page together; past it an asset stays out, so a page can't stall the phone over the tailnet.
INLINE_MAX_BYTES = 10 * 1024 * 1024
# The "Block remote content in HTML" policy: the page's own inline and inlined content only, no network at all.
STRICT_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; font-src data:; "
              "script-src 'unsafe-inline' data:; media-src data:")
# A link opens a new tab, never the frame itself, so the page in view stays the file's own.
_BASE = '<base target="_blank">'
# A scheme (https:, data:, javascript:) or a protocol-relative address: not a file beside the page.
_ABSOLUTE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+.-]*:|//)")

# A link lookup answers a target dict (root, scan_id, id) or the reason it has none.
LinkLookup = Callable[[str], "tuple[Optional[dict[str, Any]], str]"]
# An asset lookup, given the address and its kind ("css", "js" or "image"), answers its bytes and type, or None and why.
AssetLookup = Callable[[str, str], "tuple[Optional[bytes], str]"]


class _StartTags(HTMLParser):
    """Each start tag with where it sits, its source text and attributes; script and style bodies are never parsed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.found: list[tuple[tuple[int, int], str, str, list[tuple[str, Optional[str]]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.found.append((self.getpos(), self.get_starttag_text() or "", tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)


def _tag(tag: str, attrs: list[tuple[str, Optional[str]]], raw: str) -> str:
    parts = [tag] + [name if value is None else name + '="' + escape(value, quote=True) + '"' for name, value in attrs]
    return "<" + " ".join(parts) + (" />" if raw.rstrip().endswith("/>") else ">")


def _attr(attrs: list[tuple[str, Optional[str]]], name: str) -> Optional[str]:
    return next((v for k, v in attrs if k == name), None)


def _without(attrs: list[tuple[str, Optional[str]]], name: str) -> list[tuple[str, Optional[str]]]:
    return [(k, v) for k, v in attrs if k != name]


def _is_relative(address: str) -> bool:
    return bool(address.strip()) and not address.strip().startswith("#") and not _ABSOLUTE.match(address)


class _Inliner:
    """Asset addresses to ``data:`` ones, under the page's one total budget."""

    def __init__(self, asset: AssetLookup) -> None:
        self.asset = asset
        self.spent = 0

    def __call__(self, address: str, kind: str) -> tuple[Optional[str], str]:
        data, mime_or_why = self.asset(address, kind)
        if data is None:
            return None, mime_or_why
        if self.spent + len(data) > INLINE_MAX_BYTES:
            return None, "Too much to inline"
        self.spent += len(data)
        return "data:" + mime_or_why + ";base64," + base64.b64encode(data).decode("ascii"), ""


def _rewrite(tag: str, attrs: list[tuple[str, Optional[str]]], link: LinkLookup, inline: _Inliner,
             block_remote: bool) -> Optional[list[tuple[str, Optional[str]]]]:
    """The tag's new attributes, ``[]`` to drop the tag, or None to keep it exactly as written."""
    if tag == "base":
        return []
    if tag == "meta" and block_remote and (_attr(attrs, "http-equiv") or "").lower() == "refresh":
        return []
    named = {"a": "href", "img": "src", "script": "src", "link": "href"}.get(tag)
    address = _attr(attrs, named) if named else None
    if named is None or address is None or not _is_relative(address):
        return None
    if tag == "a":
        target, why = link(address)
        if target is None:
            return _without(attrs, "href") + [("title", why)]
        return [(k, doc_address(target, address) if k == "href" else v) for k, v in attrs]
    if tag == "link" and "stylesheet" not in (_attr(attrs, "rel") or "").lower().split():
        return None
    data_url, why = inline(address, {"img": "image", "script": "js", "link": "css"}[tag])
    if data_url is None:
        return _without(attrs, named) + ([("title", why)] if why == "Too much to inline" else [])
    return [(k, data_url if k == named else v) for k, v in attrs]


def frame(text: str, link: LinkLookup, asset: AssetLookup, block_remote: bool) -> dict[str, Any]:
    """``{html, outline, kind}`` for an HTML document; ``html`` is the frame's ``srcdoc`` and ``outline`` is always empty."""
    parser = _StartTags()
    parser.feed(text)
    parser.close()
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    inline = _Inliner(asset)
    edits = []
    for (line, col), raw, tag, attrs in parser.found:
        new = _rewrite(tag, attrs, link, inline, block_remote)
        if new is not None:
            at = starts[line - 1] + col
            edits.append((at, at + len(raw), _tag(tag, new, raw) if new else ""))
    out, last = [], 0
    for begin, end, replacement in edits:
        out.append(text[last:begin])
        out.append(replacement)
        last = end
    out.append(text[last:])
    head = ('<meta http-equiv="Content-Security-Policy" content="' + STRICT_CSP + '">' if block_remote else "") + _BASE
    return {"html": head + "".join(out), "outline": [], "kind": "html"}
