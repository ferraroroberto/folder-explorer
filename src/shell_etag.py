"""One ETag and 304 answer for a web app's shell document.

The entry document is served ``no-cache, must-revalidate`` so every launch asks
the server whether it is still current. Without a validator the answer is the
whole body each time; with one, a repeat load on a phone costs a few header
bytes. This module is that validator, written once so the comparison rules
cannot drift between apps:

* ``etag_for(body)`` is a **weak** tag (``W/"..."``) over the bytes of the
  stamped HTML. Weak, because the gzip middleware re-encodes the body on the
  way out and a strong tag would claim byte equality with a representation
  that differs per ``Content-Encoding``. Weak is also all ``If-None-Match``
  ever needs: that header is compared weakly.
* The tag is derived from the *stamped* HTML (after the fleet hash is written
  into the asset URLs), so any build change rotates it and a stale shell is
  never pinned.
* ``etag_matches(header, etag)`` implements the ``If-None-Match`` rules: a
  comma-separated list, ``*``, and weak comparison (a ``W/`` prefix on either
  side is ignored).
* ``shell_response(request, html)`` returns the full ``200`` or a bodiless
  ``304``; both carry ``Cache-Control: no-cache, must-revalidate`` and the
  ``ETag``, because a 304 that drops them leaves the cache with a stale policy.

It is deliberately not a cache: no service worker, no offline copy, no
``max-age``. The shell still revalidates on every load.

Vendor-verbatim: consuming apps copy this file byte-identical into their own
``src/`` and serve the shell through it::

    @router.get("/")
    async def index(request: Request) -> Response:
        return shell_response(request, BUILD_INFO.stamp_html(read_index()))

Register ``StreamSafeGZipMiddleware`` (``src/compression.py``) last as usual;
the two compose because the compression layer leaves an ``ETag`` as set and a
bodiless 304 is under its size minimum, so it passes through. Requires
``starlette`` (already present in any FastAPI app).
"""

from __future__ import annotations

import hashlib
import re

from starlette.requests import Request
from starlette.responses import Response

SHELL_CACHE_CONTROL = "no-cache, must-revalidate"
SHELL_MEDIA_TYPE = "text/html; charset=utf-8"

# 16 bytes of SHA-256 is far past any collision a single document can meet.
_TAG_BYTES = 16

# One entity-tag in an If-None-Match list: optional weak marker + quoted opaque
# string. Matching tags (not splitting on commas) keeps a comma inside a quoted
# tag from being read as a list separator.
_ENTITY_TAG = re.compile(r'(?:W/)?"[^"]*"')


def etag_for(body: bytes | str) -> str:
    """Weak entity-tag for ``body``: the same bytes always give the same tag."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    return f'W/"{hashlib.sha256(data).hexdigest()[: _TAG_BYTES * 2]}"'


def _opaque(tag: str) -> str:
    """The quoted part of a tag, without a weak marker (weak comparison)."""
    return tag.removeprefix("W/")


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """True when an ``If-None-Match`` value revalidates ``etag``.

    ``*`` matches any current representation. A missing or empty header, or one
    with no parseable tag, never matches: the full body is the safe answer.
    """
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    wanted = _opaque(etag)
    return any(_opaque(tag) == wanted for tag in _ENTITY_TAG.findall(if_none_match))


def shell_response(
    request: Request,
    html: bytes | str,
    *,
    media_type: str = SHELL_MEDIA_TYPE,
) -> Response:
    """The shell as a ``200``, or a bodiless ``304`` for a matching request.

    ``html`` is the final stamped document: hash the bytes you would send.
    """
    etag = etag_for(html)
    headers = {"Cache-Control": SHELL_CACHE_CONTROL, "ETag": etag}
    # A repeated If-None-Match header line is one comma-joined list (RFC 9110).
    if_none_match = ", ".join(request.headers.getlist("if-none-match"))
    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return Response(html, media_type=media_type, headers=headers)
