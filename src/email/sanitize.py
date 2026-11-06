"""Make a mail's HTML body safe to show: an allowlist of tags and attributes, nothing active.

The page shows the result in an ``<iframe sandbox>`` whose ``srcdoc`` carries a
Content-Security-Policy that blocks every remote load unless the reader turns
on "Load remote images" for that mail. This pass is the second wall: scripts,
frames, forms, embedded objects, event handlers and ``javascript:`` links never
reach the page. An image keeps its ``src`` only when it is inline ``data:``; an
``https:`` image keeps its address in ``data-remote-src``, which the page turns
into ``src`` only while that switch is on, so a mail read with the switch off
has no remote ``src`` in it at all.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from typing import Optional

_ALLOWED = frozenset((
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "code", "col", "colgroup", "dd", "div", "dl", "dt",
    "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "s", "small",
    "span", "strike", "strong", "style", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
))
# Elements dropped with everything inside them.
_DROP_WITH_CONTENT = frozenset(("script", "iframe", "object", "embed", "applet", "noscript", "template", "svg", "math",
                                "frame", "frameset", "form", "select", "textarea", "button", "head", "title"))
_VOID = frozenset(("br", "hr", "img", "col"))
_ATTRS = frozenset(("align", "alt", "bgcolor", "border", "cellpadding", "cellspacing", "color", "colspan", "dir",
                    "face", "height", "href", "rowspan", "size", "src", "style", "title", "valign", "width"))
_SAFE_HREF = ("http:", "https:", "mailto:", "#")


def _attr_ok(tag: str, name: str, value: str) -> bool:
    low = value.strip().lower()
    if name == "src":
        return tag == "img" and low.startswith("data:image/")
    if name == "href":
        return low.startswith(_SAFE_HREF)
    if name == "style":
        return "expression(" not in low and "javascript:" not in low
    return True


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _DROP_WITH_CONTENT:
            if tag not in _VOID:
                self.skip += 1
            return
        if self.skip or tag not in _ALLOWED:
            return
        self.in_style = tag == "style"
        kept = ["%s=\"%s\"" % (n, escape(v or "", quote=True)) for n, v in attrs
                if n in _ATTRS and _attr_ok(tag, n, v or "")]
        if tag == "img":
            kept += ["data-remote-src=\"%s\"" % escape(v.strip(), quote=True) for n, v in attrs
                     if n == "src" and v and v.strip().lower().startswith("https://")]
        self.out.append("<" + tag + (" " + " ".join(kept) if kept else "") + ">")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag not in _DROP_WITH_CONTENT:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "style":
            self.in_style = False
        if not self.skip and tag in _ALLOWED and tag not in _VOID:
            self.out.append("</" + tag + ">")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            # CSS stays as written: the frame's policy blocks any url() it names.
            self.out.append(data.replace("</", "<\\/") if self.in_style else escape(data, quote=False))


def sanitize_html(html: str) -> str:
    """The body with only inert markup left."""
    parser = _Clean()
    parser.feed(html or "")
    parser.close()
    return "".join(parser.out)
