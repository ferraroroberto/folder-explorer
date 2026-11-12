"""``pythonw -m app.launcher <file>``: the "Open with" handler for Markdown files.

Windows starts it with a ``.md`` file's path (Settings, "Associate Markdown
files with this app", registers it). It names the file by its scanned folder
(``src.docs.launch``, a read-only look at the root's current index in this
process), asks the running app over loopback for the file's id
(``POST /api/doc/find``, which also finds a file newer than the scan in one
listing of that folder), and opens the app's /doc page in an app window.
The path itself never leaves this process. Anything it can't do is said in a
message box, and logged to ``launcher.log`` in the runtime-data folder.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import requests

from app.webapp.bind import page_base, resolve_bind
from src import config, desktop, store
from src.docs.launch import NotPlaced, Place, place_of
from src.pooled_http import build_session, pooled_request
from src.roots import resolve_roots

logger = logging.getLogger("app.launcher")

TITLE = "folder-explorer"
# A find answers from a warm index in milliseconds; past this the app is treated as not answering.
TIMEOUT_S = 10.0
NOT_RUNNING = "folder-explorer isn't running, so the document can't open. Start it from the tray, then open the file again."

Send = Callable[[Place], Any]


def _send(place: Place) -> Any:
    """``POST /api/doc/find`` on the app's loopback address; the local certificate names the tailnet, so it is not verified."""
    session = build_session(pool_size=1)
    session.verify = False
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass
    return pooled_request("POST", resolve_bind(config.load()).local_url + "/api/doc/find", timeout=TIMEOUT_S,
                          session=session, json=place.as_request())


def _tell(text: str) -> None:
    """A message box: the launcher has no window of its own."""
    if sys.platform != "win32":
        return
    import ctypes  # noqa: PLC0415 - Windows only

    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x40)  # MB_ICONINFORMATION


def _answer_text(response: Any) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else "folder-explorer answered %d" % response.status_code


def doc_url(cfg: dict[str, Any], found: dict[str, Any]) -> str:
    """The /doc address for the app's answer to a find."""
    return page_base(cfg) + "/doc?" + urlencode({"root": found["root"], "scan": found["scan_id"], "id": found["id"]})


def launch(path: str, send: Send = _send, tell: Callable[[str], None] = _tell) -> int:
    """Open ``path`` in a document window: 0 when a window was asked for, 1 when the user was told why not."""
    cfg = config.load()
    try:
        place = place_of(path, resolve_roots(cfg["roots"]))
    except NotPlaced as exc:
        logger.info("ℹ️ launcher: not opened (%s)", exc.reason)
        tell(str(exc))
        return 1
    try:
        response = send(place)
    except requests.RequestException as exc:
        logger.info("ℹ️ launcher: the app is unreachable (%s)", type(exc).__name__)
        tell(NOT_RUNNING)
        return 1
    if response.status_code >= 400:
        text = _answer_text(response)
        logger.info("ℹ️ launcher: the app refused the find (%d): %s", response.status_code, text)
        tell(text + ".")
        return 1
    try:
        desktop.open_app_window(doc_url(cfg, response.json()))
    except FileNotFoundError as exc:
        logger.warning("⚠️ launcher: no window opened: %s", exc)
        tell(str(exc) + ".")
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, filename=str(store.data_dir() / "launcher.log"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
    if len(args) != 1:
        _tell("Open a Markdown file with folder-explorer from File Explorer.")
        return 2
    return launch(args[0])


if __name__ == "__main__":
    sys.exit(main())
