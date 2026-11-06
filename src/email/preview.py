"""The readable text of a mail body, for the indexed preview and the search snippets.

Outlook renders an HTML mail's plain-text body with every link as ``<url>`` and
every image as ``[cid:…]`` or ``[image]``. Those tokens fill the few hundred
characters the index keeps and push the words a person wrote out of them. Both
places that cut a preview (the ``.msg`` reader and the live Outlook read) call
``clean_preview`` first, so the cut spends its budget on real text.
"""

from __future__ import annotations

import re

# A link target Outlook wraps in angle brackets. The final alternative also takes a target the 500-character cut
# stopped inside, so a row stored before this cleaner existed loses its dangling half-URL too.
_LINK = re.compile(r"[ \t]*<(?:[a-z][a-z0-9+.-]*:)[^>\s]*(?:>|$)", re.IGNORECASE)
# An image Outlook could not show: ``[cid:logo@01D…]``, ``[image]`` or ``[image: alt text]``.
_IMAGE = re.compile(r"[ \t]*\[(?:cid:[^\]\s]*|image(?::[^\]]*)?)\]", re.IGNORECASE)
# A line that is only a URL (a tracking link the sender printed on its own).
_URL_LINE = re.compile(r"^[ \t]*(?:https?|ftp)://\S+[ \t]*$", re.IGNORECASE | re.MULTILINE)
_SPACES = re.compile(r"[ \t]{2,}")
_LINE_END = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_preview(text: str) -> str:
    """``text`` without link targets, image placeholders and URL-only lines, whitespace tidied; link text stays."""
    if not text:
        return ""
    out = _IMAGE.sub("", _LINK.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))
    out = _URL_LINE.sub("", out)
    out = _LINE_END.sub("", _SPACES.sub(" ", out))
    return _BLANK_RUN.sub("\n\n", out).strip()
