"""
SMTP / notification and data-retention configuration endpoints.
Admin-only for writes.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.config import settings
from backend.api.deps import get_current_user, get_db, require_admin
from backend.auth.service import AuthenticatedUser
from backend.core.store import DatabaseManager

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SmtpConfigRequest(BaseModel):
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = "admin@example.com"


class SmtpConfigResponse(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password_set: bool
    alert_email_to: str


@router.get("/smtp")
def get_smtp_config(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> SmtpConfigResponse:
    cfg = settings.get_smtp_config()
    return SmtpConfigResponse(
        smtp_host=cfg["smtp_host"],
        smtp_port=cfg["smtp_port"],
        smtp_user=cfg["smtp_user"],
        smtp_password_set=bool(cfg["smtp_password"]),
        alert_email_to=cfg["alert_email_to"],
    )


@router.put("/smtp")
def update_smtp_config(
    body: SmtpConfigRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
):
    settings.update_smtp_config(body.model_dump())
    return {"ok": True, "message": "SMTP settings saved. Restart the server for changes to take effect."}


class RetentionConfigRequest(BaseModel):
    retention_days: Optional[int] = Field(default=None, ge=0, description="0 or null disables automatic purging")


class RetentionConfigResponse(BaseModel):
    retention_days: Optional[int]


@router.get("/retention")
def get_retention_config(
    _user: AuthenticatedUser = Depends(get_current_user),
) -> RetentionConfigResponse:
    return RetentionConfigResponse(**settings.get_retention_config())


@router.put("/retention")
def update_retention_config(
    body: RetentionConfigRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
):
    settings.update_retention_config(body.retention_days)
    return {"ok": True, "retention_days": settings.retention_days}


@router.post("/retention/purge-now")
def purge_now(
    _admin: AuthenticatedUser = Depends(require_admin),
    db: DatabaseManager = Depends(get_db),
):
    """Runs the purge immediately against the currently-configured
    retention_days, rather than waiting for the next daily tick --
    useful right after lowering the window, or for testing."""
    if not settings.retention_days:
        return {"ok": False, "message": "Retention is disabled -- set a retention period first.", "batches_purged": 0, "events_purged": 0}
    result = db.purge_batches_older_than(settings.retention_days)
    return {"ok": True, **result}
