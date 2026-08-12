"""
FastAPI application entry point.

Run with:  PYTHONPATH=. uvicorn backend.api.main:app --reload --port 8000
(from the repo root)
"""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.config import settings
from backend.api.deps import get_auth_service, get_db, get_ollama_client, get_profile_manager
from backend.api.routes import ai, alerts, auth, logs, profiles, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("logtool.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Instantiating these ensures tables/default profiles exist before the
    # first request, and fails loudly at startup (not on a random user's
    # first request) if something's misconfigured.
    get_db()
    get_profile_manager()
    get_auth_service()
    is_ok, status = get_ollama_client().health_check()
    if is_ok:
        logger.info("Ollama check at startup: %s", status)
    else:
        logger.warning(
            "Ollama not reachable at startup (%s). AI features will report unavailable "
            "until it's up -- everything else works normally.",
            status,
        )
    yield


app = FastAPI(title="Log Visualization API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # required for the session cookie to be sent cross-origin in dev
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
def health():
    db_ok = True
    try:
        get_db().list_batches()
    except Exception:
        db_ok = False
    ollama_ok, ollama_status = get_ollama_client().health_check()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "sqlite (ok)" if db_ok else "sqlite (error)",
        "ollama": {"available": ollama_ok, "status": ollama_status},
    }


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(logs.router)
app.include_router(profiles.router)
app.include_router(alerts.router)
app.include_router(ai.router)
