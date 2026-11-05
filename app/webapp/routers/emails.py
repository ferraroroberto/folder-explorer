"""The email index's runs for the Scans tab: status, failures, and "Index emails now".

The web server only starts a run (``python -m app.cli email index`` in its own
windowless process) and reads the history; it never reads a mail itself.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from src import config, store
from src.email import indexer
from src.email.settings import EmailPart, email_part, is_under
from src.roots import resolve_roots
from app.webapp.state import EMAILS

router = APIRouter(prefix="/api/email")

MB = 1024 * 1024
# Failed files listed at most, newest first.
FAILURES_SHOWN = 200


def _part() -> EmailPart:
    cfg = config.load()
    return email_part(cfg, resolve_roots(cfg["roots"]))


def _failures(part: EmailPart, limit: int) -> tuple[int, list[dict[str, Any]]]:
    if not part.ready or part.index_path is None or not part.own:
        return 0, []
    index = EMAILS.get(part.index_path)
    with index.lock:
        tables = {r[0] for r in index.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "email_failures" not in tables:
            return 0, []
        total = int(index.conn.execute("SELECT COUNT(*) FROM email_failures").fetchone()[0])
        rows = index.conn.execute("SELECT file_path, reason, failed_at FROM email_failures ORDER BY failed_at DESC LIMIT ?",
                                  (limit,)).fetchall()
    return total, [{"path": r[0], "reason": r[1], "at": r[2]} for r in rows]


@router.get("/status")
def status() -> dict[str, Any]:
    part = _part()
    indexed = 0
    if part.ready and part.index_path is not None:
        index = EMAILS.get(part.index_path)
        with index.lock:
            indexed = index.count()
    failed, _ = _failures(part, 0)
    return {
        "ready": part.ready, "problem": part.problem, "own": part.own, "stage_problem": part.stage_problem,
        "roots": part.roots, "budget_mb": part.budget_bytes // MB, "running": indexer.is_running(),
        "indexed": indexed, "failed": failed, "runs": store.email_runs(10),
    }


@router.get("/failures")
def failures() -> dict[str, Any]:
    total, rows = _failures(_part(), FAILURES_SHOWN)
    return {"total": total, "failures": rows}


@router.post("/index")
def index_now(body: dict[str, Any] = Body(default={})) -> JSONResponse:
    part = _part()
    if part.stage_problem is not None:
        raise HTTPException(status_code=503, detail="The email stage isn't set up here: " + part.stage_problem)
    scope = body.get("root") or None
    if scope is not None and not (isinstance(scope, str) and any(is_under(scope, f) for f in part.roots)):
        raise HTTPException(status_code=400, detail="that folder is not inside an email folder")
    if indexer.is_running():
        raise HTTPException(status_code=409, detail="the email index is already being updated")
    indexer.spawn("app", scope)
    return JSONResponse({"started": True, "scope": scope or "all"}, status_code=202)
