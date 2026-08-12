from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.deps import get_current_user, get_email_dispatcher, get_dedup_engine
from backend.alerts.email import EmailDispatcher
from backend.alerts.state import AlertDeduplicationEngine
from backend.auth.service import AuthenticatedUser

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# The underlying AlertRulesProcessor (alerts/rules.py) currently implements
# exactly these two fixed rules, evaluated on every ingested batch. There's
# no per-rule enable/disable or custom-rule creation yet -- this endpoint
# reports what's actually true rather than implying configurability that
# doesn't exist.
_FIXED_RULES = [
    {
        "name": "CRITICAL Event Immediate Trigger",
        "description": "Fires an immediate email for every CRITICAL-level event, deduplicated by "
        "(source, component, message) for 1 hour by default.",
        "configurable": False,
    },
    {
        "name": "ERROR Batch Summary Trigger",
        "description": "Fires a digest email summarizing ERROR-level events found in a batch, "
        "deduplicated per batch/source.",
        "configurable": False,
    },
]


class TestAlertRequest(BaseModel):
    recipient_override: str | None = None


@router.get("/rules")
def get_rules(_user: AuthenticatedUser = Depends(get_current_user)):
    return {"rules": _FIXED_RULES}


@router.post("/test")
def test_alert(
    body: TestAlertRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
    dispatcher: EmailDispatcher = Depends(get_email_dispatcher),
):
    success, status_msg = dispatcher.send_alert_email(
        subject="[Test] Log Visualization Tool alert dispatch check",
        body_text="This is a test alert triggered manually from the Alerts page.",
        recipient_override=body.recipient_override,
    )
    return {"success": success, "status": status_msg}


@router.post("/dedup/reset")
def reset_dedup_state(
    _user: AuthenticatedUser = Depends(get_current_user),
    dedup: AlertDeduplicationEngine = Depends(get_dedup_engine),
):
    dedup.clear_state()
    return {"ok": True}
