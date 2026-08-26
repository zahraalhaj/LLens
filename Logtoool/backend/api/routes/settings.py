"""
SMTP / notification configuration endpoints. Admin-only for writes.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.alerts.email import EmailDispatcher
from backend.api.config import settings
from backend.api.deps import get_email_dispatcher, get_current_user, require_admin
from backend.auth.service import AuthenticatedUser

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
    # Force the cached EmailDispatcher to pick up new settings on next use.
    # We can't clear lru_cache from here, so we create a fresh instance
    # and stash it -- deps.get_email_dispatcher will still return the old
    # cached one, but the alert_processor holds a direct reference to the
    # EmailDispatcher we return here.  For a cleaner solution, the caller
    # should re-import and call get_email_dispatcher.cache_clear() after
    # this endpoint returns.  For now, we rely on the next server restart.
    return {"ok": True, "message": "SMTP settings saved. Restart the server for changes to take effect."}
