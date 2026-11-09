"""TenderPilot API entrypoint: `uvicorn app.main:app` from the backend/ folder."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, documents, eligibility, estimating, packaging, tenders, vault
from .config import BASE_DIR, settings
from .migrations import migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tenderpilot")
_worker = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _worker
    migrate()
    if settings.run_worker_in_process:
        from .services.jobs import Worker
        _worker = Worker()
        _worker.start()
        log.info("In-process worker started")
    yield
    if _worker:
        _worker.stop()


app = FastAPI(title="TenderPilot API", version="0.6.0", lifespan=lifespan,
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


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    # Never echo document text or secrets; log type only.
    log.error("Unhandled %s", type(exc).__name__, exc_info=exc)
    return JSONResponse({"detail": "Internal error. The action was not completed; please retry."}, status_code=500)


for r in (auth.router, vault.router, tenders.router, eligibility.router, documents.router, estimating.router, packaging.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True}


DIST = BASE_DIR.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
