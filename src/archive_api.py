"""The versioned archive contract another app calls (``/api/v1/archive/...``): what each answer says.

The routes in ``app/webapp/routers/archive_v1.py`` run the archive verbs as the
CLI does, one child process each (every Outlook call stays out of the server);
this module turns a child's answer into one envelope, so the shapes
``docs/service-api.md`` documents live in one place. Every answer carries:

- ``outcome``: ``done`` (the verb ran and every mail in it is confirmed),
  ``not-found`` (what the call named is not there), ``refused`` (declined before
  anything was touched; ``reason`` says why) or ``error`` (it ran, or tried to,
  and did not end well);
- ``acknowledged``: the archiver answered with its document, so the call was
  received and ran to its end. ``false`` (a timeout, a crash) means nobody can
  say what was written: plan again before retrying;
- ``confirmed`` / ``failed``: for ``apply`` and ``revert``, the mails whose own
  result is ``ok`` and the mails that are not. An acknowledged call is not a
  confirmed mail.
"""

from __future__ import annotations

from typing import Any, Optional

SCHEMA_VERSION = 1
DONE, NOT_FOUND, REFUSED, ERROR = "done", "not-found", "refused", "error"

# ``code`` values this layer adds to the child's own error codes.
NOT_THIS_PC = "not_this_pc"
OUTLOOK_OFF = "outlook_off"
BUSY = "busy"
TIMEOUT = "timeout"
NO_ANSWER = "no_answer"
SPAWN_FAILED = "spawn_failed"
SCHEMA_MISMATCH = "schema_mismatch"
PARTIAL = "partial"
FAILED = "failed"
NOT_INDEXED = "not_in_email_folder"
INDEX_NOT_SET_UP = "email_index_not_set_up"

# A child's "the run could not start" codes, by what the caller is told. Nothing was touched in any of them.
_NOT_FOUND_CODES = {"mailbox_unknown": 404, "mailbox_not_in_outlook": 404}
_REFUSED_CODES = {"bad_input": 422, "config_missing": 503, "mailbox_registry_invalid": 503,
                  "outlook_unavailable": 503, "com_unavailable": 503}
# A mail that is not where the verb looks for it.
_NOT_THERE = {"not_in_inbox", "not_in_sent", "not_in_archive_folder"}
# ``email index`` exit codes: 0 indexed, 2 busy, 3 outside the email folders, 4 not set up, anything else a failure.
_INDEX_REFUSED = {2: (BUSY, 409, "another email run held the index past the wait"),
                  3: (NOT_INDEXED, 422, "the folder is not inside the email folders"),
                  4: (INDEX_NOT_SET_UP, 503, "the email index is not set up on this PC")}


def answer(verb: str, outcome: str, status: int, *, code: Optional[str] = None, reason: Optional[str] = None,
           acknowledged: bool = True, result: Optional[dict[str, Any]] = None,
           confirmed: Optional[list[str]] = None, failed: Optional[list[dict[str, str]]] = None
           ) -> tuple[dict[str, Any], int]:
    """One envelope and its HTTP status; ``result`` is the archiver's own document, untouched."""
    return {"schema_version": SCHEMA_VERSION, "verb": verb, "outcome": outcome, "code": code, "reason": reason,
            "acknowledged": acknowledged, "confirmed": confirmed or [], "failed": failed or [],
            "result": result}, status


def refused(verb: str, code: str, reason: str, status: int) -> tuple[dict[str, Any], int]:
    """A call declined before any child ran."""
    return answer(verb, REFUSED, status, code=code, reason=reason, acknowledged=False)


def unanswered(verb: str, code: str, reason: str, status: int) -> tuple[dict[str, Any], int]:
    """A child that gave no document: the call may have written anything, so nothing is confirmed."""
    return answer(verb, ERROR, status, code=code, reason=reason, acknowledged=False)


def from_document(verb: str, doc: Any, exit_code: int) -> tuple[dict[str, Any], int]:
    """The envelope for the document a ``plan``, ``apply`` or ``revert`` child printed."""
    if not isinstance(doc, dict):
        return unanswered(verb, NO_ANSWER, "the archiver printed no document (exit %s)" % exit_code, 502)
    if doc.get("schema_version") != SCHEMA_VERSION:
        return unanswered(verb, SCHEMA_MISMATCH, "the archiver's document is schema_version %r; this API speaks %d"
                          % (doc.get("schema_version"), SCHEMA_VERSION), 502)
    error = doc.get("error")
    if isinstance(error, dict):
        return _from_error(verb, doc, error)
    if verb == "plan":
        return _plan(doc)
    return _per_mail(verb, doc)


def _from_error(verb: str, doc: dict[str, Any], error: dict[str, Any]) -> tuple[dict[str, Any], int]:
    code, reason = str(error.get("code") or "unknown"), str(error.get("message") or "the archiver refused the call")
    if code in _NOT_FOUND_CODES:
        return answer(verb, NOT_FOUND, _NOT_FOUND_CODES[code], code=code, reason=reason, result=doc)
    if code in _REFUSED_CODES:
        return answer(verb, REFUSED, _REFUSED_CODES[code], code=code, reason=reason, result=doc)
    # Anything else (``run_failed`` and codes this layer does not know) came after the walk began.
    return answer(verb, ERROR, 500, code=code, reason=reason, result=doc)


def _plan(doc: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """A plan that named mails and found none of them is ``not-found``; every other plan is ``done``."""
    skipped = [s for s in doc.get("skipped") or [] if isinstance(s, dict) and s.get("message_id")]
    if skipped and not doc.get("mails"):
        return answer("plan", NOT_FOUND, 404, code=skipped[0].get("reason") or "not_in_inbox",
                      reason="none of the named mails is in the folder", result=doc)
    return answer("plan", DONE, 200, result=doc)


def _failure_code(entry: dict[str, Any]) -> str:
    error = entry.get("error")
    return str(error.get("code")) if isinstance(error, dict) and error.get("code") else FAILED


def _per_mail(verb: str, doc: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """``apply`` / ``revert``: done only when every mail's own result is ok."""
    results = [r for r in doc.get("results") or [] if isinstance(r, dict)]
    confirmed = [str(r.get("message_id")) for r in results if r.get("ok")]
    failed = [{"message_id": str(r.get("message_id")), "code": _failure_code(r)} for r in results if not r.get("ok")]
    if not failed:
        return answer(verb, DONE, 200, result=doc, confirmed=confirmed)
    if not confirmed and all(f["code"] in _NOT_THERE for f in failed):
        return answer(verb, NOT_FOUND, 404, code=failed[0]["code"], reason="none of the mails is where the verb looks",
                      result=doc, failed=failed)
    reason = "%d of %d mail(s) not confirmed: each one's result says what is on disk and in Outlook" % (
        len(failed), len(results))
    if confirmed:
        return answer(verb, ERROR, 207, code=PARTIAL, reason=reason, result=doc, confirmed=confirmed, failed=failed)
    return answer(verb, ERROR, 502, code=FAILED, reason=reason, result=doc, failed=failed)


def from_index_exit(exit_code: int) -> tuple[dict[str, Any], int]:
    """The envelope for ``email index --root``, which prints no document: its exit code is the whole answer."""
    if exit_code == 0:
        return answer("index", DONE, 200)
    if exit_code in _INDEX_REFUSED:
        code, status, reason = _INDEX_REFUSED[exit_code]
        return answer("index", REFUSED, status, code=code, reason=reason)
    return answer("index", ERROR, 500, code=FAILED, reason="the re-index failed (exit %s)" % exit_code)
