"""The Quick search window's only way to the data: the web app's own local HTTP API.

It calls the routes the Search tab calls (``/api/search/folders``, ``/api/search/emails``,
``/api/folders/open``, ``/api/emails/{id}/open``), so the window and the page answer the
same query the same way and every open goes through the server's desktop helper. It
never reads an index or a SQLite file itself.

A call that never reaches the app raises :class:`Unreachable`; a call the app answers
with an error raises :class:`ApiError` with the app's own sentence. The window shows
the two differently, and neither as "no matches".
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import requests

from src.pooled_http import build_session, pooled_request

logger = logging.getLogger(__name__)

# A search answers in tens of ms on a warm index; past this the app is treated as not answering.
TIMEOUT_S = 8.0
UNREACHABLE = "The folder-explorer web app isn't answering, so nothing can be searched. Start it from the tray (Restart web app)."

# The local API is on 127.0.0.1, which the tailnet certificate does not name, so it is not verified (as the tray's probe).
_SESSION = build_session(pool_size=2)
_SESSION.verify = False
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass

Send = Callable[..., Any]


class Unreachable(Exception):
    """The web app did not answer at all: not running, still starting, or past the timeout."""


class ApiError(Exception):
    """The web app answered with an error; the message is its own sentence."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        return "the web app answered %d" % response.status_code
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else "the web app answered %d" % response.status_code


class SearchClient:
    """The five calls the window makes.

    ``base()`` gives the app's loopback address (``https://127.0.0.1:<port>``), asked on every call so a
    changed bind is followed; the tray passes it, as ``src`` never imports ``app``. ``send(method, path,
    **kwargs)`` replaces the HTTP transport; the tests pass a FastAPI TestClient's.
    """

    def __init__(self, base: Callable[[], str], send: Optional[Send] = None) -> None:
        self._base = base
        self._send = send

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            if self._send is not None:
                response = self._send(method, path, **kwargs)
            else:
                response = pooled_request(method, self._base() + path, timeout=TIMEOUT_S, session=_SESSION, **kwargs)
        except requests.RequestException as exc:
            logger.info("ℹ️ quick search: the web app is unreachable (%s)", type(exc).__name__)
            raise Unreachable(UNREACHABLE) from exc
        if response.status_code >= 400:
            raise ApiError(response.status_code, _detail(response))
        return response.json()

    def folders(self, query: str) -> dict[str, Any]:
        return self._call("GET", "/api/search/folders", params={"q": query})

    def emails(self, query: str) -> dict[str, Any]:
        return self._call("GET", "/api/search/emails", params={"q": query, "sort": "relevance", "offset": 0})

    def open_folder(self, root: str, scan_id: Optional[int], folder_id: int) -> dict[str, Any]:
        return self._call("POST", "/api/folders/open", json={"root": root, "scan_id": scan_id, "id": folder_id})

    def open_email(self, row_id: int) -> dict[str, Any]:
        return self._call("POST", "/api/emails/%d/open" % row_id, json={})

    def reveal_email(self, row_id: int) -> dict[str, Any]:
        return self._call("POST", "/api/emails/%d/reveal" % row_id, json={})

