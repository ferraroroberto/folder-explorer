"""The FastAPI app: the browse, search, scan and settings API, sign-in, and the static PWA.

Read-only over the disk: it lists folders through ``src/scanner.py`` and
never moves or deletes a file. The one file it reads is a mail's ``.msg`` for
"Show all", through ``src/email/reader.py`` in a child process. Start it with ``python -m app.webapp``
(which picks the bind from the ``remote_access`` setting) or from the tray.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from src import config, scan_runner, store
from src.compression import StreamSafeGZipMiddleware
from src.roots import resolve_roots
from src.usage_log import usage_router
from app.webapp import auth
from app.webapp.build import BUILD_INFO, STATIC_DIR
from app.webapp.routers import browse, emails, scans, search, settings
from app.webapp.state import EMAILS, POOL, refresh_pool

logger = logging.getLogger(__name__)

# Hash-stamped assets cache for a year: the fleet hash in the URL changes on
# every edit, so a stale copy is never served. Icons and the manifest
# revalidate daily. The shell always revalidates, or it would pin old URLs.
_LONG_CACHE = "public, max-age=31536000, immutable"
_DAY_CACHE = "public, max-age=86400"
_SHELL_CACHE = "no-cache, must-revalidate"
_DAILY_SUFFIXES = frozenset({".webmanifest", ".png", ".ico", ".svg"})


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` with a per-suffix ``Cache-Control`` and hash-stamped JS imports."""

    def file_response(self, full_path: "os.PathLike[str]", stat_result: os.stat_result, scope: Scope,
                      status_code: int = 200) -> Response:
        path = Path(full_path)
        suffix = path.suffix.lower()
        if suffix == ".js":
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                return super().file_response(full_path, stat_result, scope, status_code)
            media_type, _ = mimetypes.guess_type(str(path))
            return Response(BUILD_INFO.stamp_js(body, path), status_code=status_code,
                            media_type=media_type or "text/javascript", headers={"Cache-Control": _LONG_CACHE})
        response = super().file_response(full_path, stat_result, scope, status_code)
        if suffix == ".css":
            response.headers["Cache-Control"] = _LONG_CACHE
        elif suffix in _DAILY_SUFFIXES:
            response.headers["Cache-Control"] = _DAY_CACHE
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    scan_runner.settle_if_idle()
    refresh_pool(resolve_roots(config.load()["roots"]))
    scheduler = scan_runner.Scheduler()
    scheduler.start()
    logger.info("ℹ️ folder-explorer %s up; in-app schedule: %s", BUILD_INFO.git_sha, config.load()["schedule"])
    yield
    scheduler.stop()
    POOL.close_all()
    EMAILS.close()


def create_app() -> FastAPI:
    app = FastAPI(title="folder-explorer", lifespan=_lifespan, docs_url=None, redoc_url=None)
    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(browse.router)
    app.include_router(scans.router)
    app.include_router(search.router)
    app.include_router(emails.router)
    # Local-only view/tap log, under the same sign-in rule as every other route.
    app.include_router(usage_router(store.data_dir() / "usage.sqlite3"))

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = BUILD_INFO.stamp_html((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
        return HTMLResponse(html, headers={"Cache-Control": _SHELL_CACHE})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.ico", headers={"Cache-Control": _DAY_CACHE})

    app.mount("/static", CachingStaticFiles(directory=str(STATIC_DIR)), name="static")
    app.add_middleware(auth.SignInMiddleware)
    # Added last, so it is the outermost layer: a 401 from the gate passes through unchanged.
    app.add_middleware(StreamSafeGZipMiddleware, bypass_paths=())
    return app


app = create_app()
