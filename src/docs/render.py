"""Markdown text to the viewer's HTML and outline: CommonMark + GFM tables, task lists and footnotes, Pygments listings.

Reads nothing from disk: the caller hands over the text and two lookups, one
for a relative link and one for an image, each answering from the scan
index. Raw HTML in the document stays text. A relative link to a scanned
Markdown or HTML file becomes a ``/doc`` address by id; one that leaves the root, isn't
scanned or isn't a document renders inert, with no address. An image loads only
by its id (the page fetches it), never from a path or the web.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Optional

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml, unescapeAll
from markdown_it.token import Token
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.anchors.index import slugify
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from src.docs.locate import doc_address

# Heading ids share the page with the app's own element ids, so each carries this prefix.
ID_PREFIX = "doc-"
_WEB = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)
_FORMATTER = HtmlFormatter(nowrap=True)

# What a link or image lookup answers: a target dict, or the reason it has none.
Lookup = Callable[[str], "tuple[Optional[dict[str, Any]], str]"]

_COPY = ('<button type="button" class="icon-button code-listing-copy" aria-label="Copy code" title="Copy code" '
         'data-track="doc-copy-code"><svg class="icon code-listing-copy-glyph" aria-hidden="true"><use href="#i-copy"></use></svg>'
         '<svg class="icon code-listing-copied" aria-hidden="true"><use href="#i-check"></use></svg></button>')


def _listing(code: str, lang: str) -> str:
    try:
        body = highlight(code, get_lexer_by_name(lang), _FORMATTER) if lang else escapeHtml(code)
    except ClassNotFound:
        body = escapeHtml(code)
    return ('<div class="code-listing"><div class="code-listing-head"><span class="code-listing-title">' + escapeHtml(lang or "Text") +
            '</span><span class="code-listing-status" role="status"></span>' + _COPY + '</div><pre><code>' + body + '</code></pre></div>\n')


def _fence(self: Any, tokens: Sequence[Token], idx: int, options: Any, env: Any) -> str:
    info = unescapeAll(tokens[idx].info).strip()
    return _listing(tokens[idx].content, info.split()[0] if info else "")


def _code_block(self: Any, tokens: Sequence[Token], idx: int, options: Any, env: Any) -> str:
    return _listing(tokens[idx].content, "")


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    md.use(footnote_plugin).use(tasklists_plugin)
    md.use(anchors_plugin, min_level=1, max_level=6, slug_func=lambda s: ID_PREFIX + slugify(s))
    md.add_render_rule("fence", _fence)
    md.add_render_rule("code_block", _code_block)
    md.add_render_rule("table_open", lambda *_a: '<div class="doc-table"><table>\n')
    md.add_render_rule("table_close", lambda *_a: "</table></div>\n")
    return md


_MD = _build()


def _inert(tok: Token, title: str) -> None:
    tok.tag = "span"
    tok.attrs = {"class": "doc-link-off", "title": title}


def _rewrite_links(children: list[Token], link: Lookup) -> None:
    opened: list[Token] = []
    for tok in children:
        if tok.type == "link_open":
            opened.append(tok)
            href = str(tok.attrGet("href") or "")
            if href.startswith("#"):
                tok.attrSet("href", "#" + ID_PREFIX + href[1:])
            elif _WEB.match(href):
                tok.attrSet("target", "_blank")
                tok.attrSet("rel", "noopener noreferrer")
            else:
                target, why = link(href)
                if target is None:
                    _inert(tok, why)
                else:
                    tok.attrs = {"href": doc_address(target, href), "data-doc-id": str(target["id"])}
        elif tok.type == "link_close" and opened:
            tok.tag = opened.pop().tag


def _task_mark(tok: Token) -> None:
    """A task list's checkbox drawn as a mark: the viewer can't tick it, so it is no control to tap."""
    done = 'checked="checked"' in tok.content
    tok.content = ('<span class="task-mark' + (' is-done' if done else '') + '" role="img" aria-label="' + ("Done" if done else "To do") +
                   '">' + ('<svg class="icon" aria-hidden="true"><use href="#i-check"></use></svg>' if done else "") + "</span>")


def _rewrite_images(children: list[Token], image: Lookup) -> list[Token]:
    out = []
    for tok in children:
        if tok.type == "html_inline" and tok.content.startswith('<input class="task-list-item-checkbox"'):
            _task_mark(tok)
        if tok.type != "image":
            out.append(tok)
            continue
        alt = "".join(c.content for c in tok.children or [])
        src = str(tok.attrGet("src") or "")
        target, why = (None, "Images from the web aren't loaded") if _WEB.match(src) else image(src)
        if target is None:
            off = Token("html_inline", "", 0)
            off.content = '<span class="doc-img-off" title="' + escapeHtml(why) + '">' + escapeHtml(alt or "Image") + "</span>"
            out.append(off)
            continue
        tok.attrs = {"data-doc-img": str(target["id"]), "alt": alt}
        if target.get("online"):
            tok.attrSet("data-online", "1")
        out.append(tok)
    return out


def _heading_text(tok: Token) -> str:
    return "".join(c.content for c in tok.children or [] if c.type in ("text", "code_inline")).strip()


def render(text: str, link: Lookup, image: Lookup, lines: bool = False) -> dict[str, Any]:
    """``{html, outline}`` for ``text``; ``outline`` lists each heading's level, text and element id.

    ``lines`` marks each top-level block with the source line it starts on
    (``data-line``), so the editor's preview can follow the cursor.
    """
    env: dict[str, Any] = {}
    tokens = _MD.parse(text, env)
    outline = []
    for i, tok in enumerate(tokens):
        if lines and tok.nesting == 1 and tok.level == 0 and tok.map:
            tok.attrSet("data-line", str(tok.map[0]))
        if tok.type == "heading_open":
            outline.append({"level": int(tok.tag[1]), "text": _heading_text(tokens[i + 1]), "id": tok.attrGet("id")})
        elif tok.type == "inline" and tok.children:
            _rewrite_links(tok.children, link)
            tok.children = _rewrite_images(tok.children, image)
    return {"html": _MD.renderer.render(tokens, _MD.options, env), "outline": outline}
