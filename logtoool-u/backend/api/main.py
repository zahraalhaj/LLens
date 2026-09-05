"""
FastAPI application entry point.

Run with (from the repo root):
    COOKIE_SECURE=false PYTHONPATH=. uvicorn backend.api.main:app --reload --port 8000

COOKIE_SECURE=false is required for local HTTP development: the session
cookie defaults to Secure (backend/api/config.py), which browsers silently
refuse to store/send over plain HTTP. Without this flag, login appears to
succeed (200 OK) but every following request -- including the forced
password-change on a fresh account -- comes back 401, since the cookie the
browser was handed on login was never actually kept. Omit the flag only
when actually serving over HTTPS.
"""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.config import settings
from backend.api.deps import get_auth_service, get_db, get_email_dispatcher, get_ollama_client, get_profile_manager, get_remote_machine_service
from backend.api.routes import (
    ai,
    ai_analyst,
    alerts,
    analytics,
    auth,
    cardinal,
    currency_map,
    debit_portal,
    logs,
    machines,
    otp_processor,
    profiles,
    settings as settings_routes,
    users,
    ila_bank,
    vflex,
    vplus_monitoring,
)
from backend.analysis.vplus_alerting import VPlusAvailabilityMonitor, register_vplus_availability_job
from backend.core.retention import register_retention_purge_job
from backend.remote import crypto as remote_crypto
from backend.remote.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("logtool.api")

_scheduler = None


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

    global _scheduler
    try:
        remote_service = get_remote_machine_service()
        _scheduler = start_scheduler(remote_service)
    except remote_crypto.EncryptionKeyMissingError as e:
        logger.warning(
            "Remote machine polling disabled: %s Everything else works normally; the "
            "Control Center will report this same error if used until the key is set.",
            e,
        )
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler()
        _scheduler.start()

    try:
        vplus_monitor = VPlusAvailabilityMonitor(
            db_path=settings.db_path, db_manager=get_db(), email_dispatcher=get_email_dispatcher()
        )
        register_vplus_availability_job(_scheduler, vplus_monitor)
    except Exception:
        logger.exception(
            "Failed to register V+ availability monitor -- V+ down/recovered alerting disabled, "
            "everything else works normally."
        )

    try:
        register_retention_purge_job(_scheduler, get_db(), settings)
    except Exception:
        logger.exception(
            "Failed to register retention purge job -- automatic data retention disabled, "
            "everything else works normally."
        )

    yield

    if _scheduler:
        _scheduler.shutdown(wait=False)


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
app.include_router(machines.router)
app.include_router(vplus_monitoring.router)
app.include_router(otp_processor.router)
app.include_router(debit_portal.router)
app.include_router(cardinal.router)
app.include_router(ila_bank.router)
app.include_router(vflex.router)
app.include_router(currency_map.router)
app.include_router(analytics.router)
app.include_router(ai_analyst.router)
app.include_router(settings_routes.router)
