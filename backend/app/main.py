"""TenderPilot API entrypoint: `uvicorn app.main:app` from the backend/ folder."""
from __future__ import annotations

import os

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, documents, eligibility, estimating, network, packaging, system, tenders, update, vault
from .config import FRONTEND_DIST, settings
from .migrations import migrate
from .version import APP_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tenderpilot")
_worker = None


class _SkipReread(Exception):
    pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _worker
    migrate()
    try:  # only when AUTO_REREAD=1: documents read by an older reader are read again (reviewed values are kept)
        if not settings.auto_reread:
            raise _SkipReread
        from .db import SessionLocal
        from .services.jobs import requeue_outdated_extractions
        with SessionLocal() as db:
            n = requeue_outdated_extractions(db)
        if n:
            log.info("Queued %d document(s) to be re-read by the updated tender reader", n)
    except _SkipReread:
        pass
    except Exception:  # noqa: BLE001  never block start-up
        log.exception("Could not queue documents for re-reading")
    try:
        from .services.updater import cleanup_old_installers
        cleanup_old_installers()  # installers of versions already installed
    except Exception:  # noqa: BLE001  never block start-up
        log.exception("Could not tidy the updates folder")
    if settings.run_worker_in_process:
        from .services.jobs import Worker
        _worker = Worker()
        _worker.start()
        log.info("In-process worker started")
    yield
    if _worker:
        _worker.stop()


app = FastAPI(title="TenderPilot API", version=APP_VERSION, lifespan=lifespan,
              description="Tender intelligence & bid preparation. Versioned API under /api/v1.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    # FastAPI's default 422 echoes the submitted value ("input"): drop it so passwords and API keys are never reflected.
    errors = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    # Never echo document text or secrets; log type only.
    log.error("Unhandled %s", type(exc).__name__, exc_info=exc)
    return JSONResponse({"detail": "Internal error. The action was not completed; please retry."}, status_code=500)


for r in (system.router, auth.router, vault.router, tenders.router, eligibility.router, documents.router, estimating.router, packaging.router, update.router, network.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True, "app": "TenderPilot", "demo": os.environ.get("DEMO_MODE") == "1", "version": APP_VERSION,
            "https": os.environ.get("TP_HTTPS_ACTIVE") == "1"}


DIST = FRONTEND_DIST
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
